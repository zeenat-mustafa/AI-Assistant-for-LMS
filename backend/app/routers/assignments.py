"""
Assignment file upload (instructor) and download (any authenticated user).

POST   /sessions/{session_id}/assignments                    → upload one or more .ipynb files,
                                                               or a .zip archive (recursively extracted).
                                                               Returns a list of all created records.
GET    /sessions/{session_id}/assignments                    → list assignment files
GET    /sessions/{session_id}/assignments/{file_id}/download → download a file
DELETE /sessions/{session_id}/assignments/{file_id}          → remove a file
"""

import logging
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.models.resource_file import ResourceFile
from app.models.user import User
from app.schemas.unsolved_file import UnsolvedFileRead
from app.schemas.resource_file import AssignmentUploadItem, ResourceFileRead
from app.services.auth import get_current_user, require_instructor
from app.services.storage import (
    absolute_path,
    save_assignment_file,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sessions/{session_id}/assignments",
    tags=["assignments"],
)

_ALLOWED_EXTENSIONS = {".ipynb", ".zip"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_session_or_404(session_id: int, db: Session) -> LMSSession:
    session = db.get(LMSSession, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    return session


def _get_file_or_404(file_id: int, session_id: int, db: Session) -> UnsolvedFile:
    f = db.get(UnsolvedFile, file_id)
    if f is None or f.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Assignment file {file_id} not found in session {session_id}.",
        )
    return f


def _get_resource_or_404(resource_id: int, session_id: int, db: Session) -> ResourceFile:
    r = db.get(ResourceFile, resource_id)
    if r is None or r.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource file {resource_id} not found in session {session_id}.",
        )
    return r


async def _collect_files(
    uploads: list[UploadFile],
) -> tuple[list[tuple[str, bytes]], list[tuple[str, bytes]]]:
    """
    Expand uploaded files into two flat lists of (filename, bytes) pairs:
    gradeable notebooks and downloadable resource files.

    Rules:
    - A .ipynb upload is a notebook, passed through as-is.
    - A .zip upload is extracted (any folder depth): every .ipynb inside is a
      notebook, every other real file is a resource (a dataset the notebook
      reads, slides, a reference PDF, …). Directories and __MACOSX metadata
      are skipped.
    - Any other direct upload extension raises HTTP 422 — resources arrive
      bundled in a .zip alongside (or instead of) notebooks, not as a bare
      non-notebook upload. This keeps the direct-upload contract unchanged.
    - A .zip that contains NEITHER a notebook NOR any resource file raises
      HTTP 422 (nothing to store); previously a zip with no notebooks was
      rejected even if it carried resources — that is the behaviour this fix
      changes.

    Notebooks and resources are stored in structurally separate tables by the
    caller (UnsolvedFile vs ResourceFile); only notebooks are ever gradeable.
    """
    from app.services.notebook import extract_files_from_zip

    notebooks: list[tuple[str, bytes]] = []
    resources: list[tuple[str, bytes]] = []

    for upload in uploads:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Only .ipynb or .zip files can be uploaded directly. "
                    f"Got: '{suffix}'. Bundle datasets or other resource files "
                    f"inside a .zip alongside your notebooks."
                ),
            )

        data = await upload.read()

        if suffix == ".ipynb":
            notebooks.append((upload.filename, data))

        else:  # .zip
            with tempfile.TemporaryDirectory() as tmp_dir:
                nb_paths, res_paths = extract_files_from_zip(data, tmp_dir)
                if not nb_paths and not res_paths:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            f"The zip archive '{upload.filename}' contains no "
                            f"usable files (no notebooks and no resource files)."
                        ),
                    )
                # Read bytes inside the context manager, before cleanup.
                for nb_path in nb_paths:
                    notebooks.append((nb_path.name, nb_path.read_bytes()))
                for res_path in res_paths:
                    resources.append((res_path.name, res_path.read_bytes()))

    return notebooks, resources


def _existing_filename_conflict(
    session_id: int, filename: str, db: Session
) -> bool:
    """True if *filename* already exists in this session as EITHER a notebook
    or a resource — the duplicate guard spans both tables since they share one
    on-disk directory keyed by original_filename."""
    in_notebooks = (
        db.query(UnsolvedFile)
        .filter(
            UnsolvedFile.session_id == session_id,
            UnsolvedFile.original_filename == filename,
        )
        .first()
    )
    if in_notebooks:
        return True
    in_resources = (
        db.query(ResourceFile)
        .filter(
            ResourceFile.session_id == session_id,
            ResourceFile.original_filename == filename,
        )
        .first()
    )
    return in_resources is not None


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=list[AssignmentUploadItem],
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Upload assignment file(s) to a session (instructor only). "
        "Accepts one or more .ipynb notebooks, or a .zip archive that is "
        "recursively extracted — .ipynb files become gradeable notebooks and "
        "any other files become downloadable resources. Returns every created "
        "record, each tagged with its file_role (notebook | resource)."
    ),
)
async def upload_assignment(
    session_id: int,
    files: Annotated[
        list[UploadFile],
        File(
            description=(
                "One or more .ipynb notebook files, or a single .zip archive. "
                "A .zip may contain notebooks at any folder depth plus supporting "
                "resource files (datasets, PDFs, slides); notebooks are gradeable, "
                "resources are downloadable only."
            )
        ),
    ],
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> list[AssignmentUploadItem]:
    _get_session_or_404(session_id, db)

    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one file must be uploaded.",
        )

    # Expand uploads into gradeable notebooks and downloadable resource files.
    notebooks, resources = await _collect_files(files)

    # Duplicate-filename guard — spans BOTH notebooks and resources, since they
    # share one on-disk directory keyed by original_filename. Checked before
    # persisting anything so the request fails atomically on any conflict (no
    # partial writes), matching 5.3's proven behaviour.
    for filename, _ in [*notebooks, *resources]:
        if _existing_filename_conflict(session_id, filename, db):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"A file named '{filename}' already exists "
                    f"in session {session_id}. Delete it first or use a different name."
                ),
            )

    created: list[AssignmentUploadItem] = []
    created_notebooks: list[UnsolvedFile] = []
    created_resources: list[ResourceFile] = []

    # Persist notebooks: parse requirements text at upload time (as before) so
    # it is available immediately for rubric generation and file matching.
    for filename, data in notebooks:
        rel_path = await save_assignment_file(session_id, filename, data)
        parsed_text: str | None = None
        try:
            from app.services.notebook import extract_requirements_text
            parsed_text = extract_requirements_text(absolute_path(rel_path))
        except Exception as exc:
            logger.warning(
                "Could not parse requirements text from %s: %s", filename, exc
            )
        unsolved = UnsolvedFile(
            session_id=session_id,
            original_filename=filename,
            file_path=rel_path,
            parsed_requirements_text=parsed_text,
        )
        db.add(unsolved)
        created_notebooks.append(unsolved)
        logger.info("Assignment notebook uploaded: %s → session %d", filename, session_id)

    # Persist resources into the separate ResourceFile table — never parsed,
    # matched, given a rubric, or counted toward the session's assignment total.
    for filename, data in resources:
        rel_path = await save_assignment_file(session_id, filename, data)
        resource = ResourceFile(
            session_id=session_id,
            original_filename=filename,
            file_path=rel_path,
        )
        db.add(resource)
        created_resources.append(resource)
        logger.info("Resource file uploaded: %s → session %d", filename, session_id)

    db.commit()
    for u in created_notebooks:
        db.refresh(u)
        created.append(AssignmentUploadItem.from_notebook(u))
    for r in created_resources:
        db.refresh(r)
        created.append(AssignmentUploadItem.from_resource(r))

    return created


# ── List ──────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[UnsolvedFileRead],
    summary="List assignment files for a session",
)
def list_assignments(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[UnsolvedFileRead]:
    _get_session_or_404(session_id, db)
    files = (
        db.query(UnsolvedFile)
        .filter(UnsolvedFile.session_id == session_id)
        .order_by(UnsolvedFile.uploaded_at)
        .all()
    )
    return [UnsolvedFileRead.from_orm_model(f) for f in files]


# ── Download ──────────────────────────────────────────────────────────────────

@router.get(
    "/{file_id}/download",
    summary="Download an assignment file",
    response_class=FileResponse,
)
def download_assignment(
    session_id: int,
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    f = _get_file_or_404(file_id, session_id, db)
    abs_path = absolute_path(f.file_path)
    if not abs_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File is recorded in the database but not found on disk.",
        )
    return FileResponse(
        path=str(abs_path),
        filename=f.original_filename,
        media_type="application/octet-stream",
    )


# ── Resource files (supporting material) ──────────────────────────────────────

@router.get(
    "/resources",
    response_model=list[ResourceFileRead],
    summary="List a session's resource (non-notebook) files",
)
def list_resources(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[ResourceFileRead]:
    _get_session_or_404(session_id, db)
    files = (
        db.query(ResourceFile)
        .filter(ResourceFile.session_id == session_id)
        .order_by(ResourceFile.uploaded_at)
        .all()
    )
    return [ResourceFileRead.from_orm_model(f) for f in files]


@router.get(
    "/resources/{resource_id}/download",
    summary="Download a resource file",
    response_class=FileResponse,
)
def download_resource(
    session_id: int,
    resource_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    r = _get_resource_or_404(resource_id, session_id, db)
    abs_path = absolute_path(r.file_path)
    if not abs_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File is recorded in the database but not found on disk.",
        )
    return FileResponse(
        path=str(abs_path),
        filename=r.original_filename,
        media_type="application/octet-stream",
    )


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an assignment file (instructor only)",
)
def delete_assignment(
    session_id: int,
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> None:
    f = _get_file_or_404(file_id, session_id, db)
    abs_path = absolute_path(f.file_path)
    if abs_path.exists():
        abs_path.unlink()
    db.delete(f)
    db.commit()


# ── Rubric Generation ────────────────────────────────────────────────────────

@router.post(
    "/{file_id}/generate-rubric",
    summary="Generate (or regenerate) the rubric for an assignment file (instructor only)",
)
def generate_rubric(
    session_id: int,
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
    force: bool = Query(False, description="Regenerate and overwrite an existing cached rubric."),
) -> dict:
    _get_session_or_404(session_id, db)
    _get_file_or_404(file_id, session_id, db)
    from app.services.rubric import generate_rubric_for_unsolved_file
    return generate_rubric_for_unsolved_file(db, file_id, force=force)
