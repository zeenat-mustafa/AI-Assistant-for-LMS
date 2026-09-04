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

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User
from app.schemas.unsolved_file import UnsolvedFileRead
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


async def _collect_notebooks(uploads: list[UploadFile]) -> list[tuple[str, bytes]]:
    """
    Expand a list of uploaded files into a flat list of (filename, bytes) pairs,
    one entry per .ipynb notebook found.

    Rules:
    - A .ipynb upload is passed through as-is.
    - A .zip upload is extracted recursively via extract_notebooks_from_zip;
      every .ipynb inside is included regardless of folder depth.
      Non-.ipynb entries are silently ignored (consistent with student submissions).
    - Any other extension raises HTTP 422 immediately.
    - A .zip that contains no .ipynb files raises HTTP 422.

    The existing extract_notebooks_from_zip from services/notebook.py is reused
    here — no extraction logic is duplicated.
    """
    from app.services.notebook import extract_notebooks_from_zip

    notebooks: list[tuple[str, bytes]] = []

    for upload in uploads:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Only .ipynb or .zip files are accepted for assignment upload. "
                    f"Got: '{suffix}'"
                ),
            )

        data = await upload.read()

        if suffix == ".ipynb":
            notebooks.append((upload.filename, data))

        else:  # .zip
            # Extract to a temporary directory, read each notebook's bytes,
            # then let the TemporaryDirectory be cleaned up automatically.
            with tempfile.TemporaryDirectory() as tmp_dir:
                extracted = extract_notebooks_from_zip(data, tmp_dir)
                if not extracted:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            f"The zip archive '{upload.filename}' contains no .ipynb files."
                        ),
                    )
                for nb_path in extracted:
                    # Read bytes inside the context manager before cleanup.
                    notebooks.append((nb_path.name, nb_path.read_bytes()))

    return notebooks


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=list[UnsolvedFileRead],
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Upload assignment file(s) to a session (instructor only). "
        "Accepts one or more .ipynb files in a single multipart request, or a .zip "
        "archive that is recursively extracted. Returns a list of all created records."
    ),
)
async def upload_assignment(
    session_id: int,
    files: Annotated[
        list[UploadFile],
        File(
            description=(
                "One or more .ipynb notebook files, or a single .zip archive "
                "containing .ipynb files at any folder depth. "
                "For a single file upload, supply exactly one entry — the response "
                "is always a list, preserving a consistent contract."
            )
        ),
    ],
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> list[UnsolvedFileRead]:
    _get_session_or_404(session_id, db)

    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one file must be uploaded.",
        )

    # Expand all uploads into (filename, bytes) pairs for every .ipynb found.
    notebooks = await _collect_notebooks(files)

    # Duplicate-filename guard — check before persisting anything so the
    # entire request fails atomically on any conflict (no partial writes).
    for filename, _ in notebooks:
        existing = (
            db.query(UnsolvedFile)
            .filter(
                UnsolvedFile.session_id == session_id,
                UnsolvedFile.original_filename == filename,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"An assignment file named '{filename}' already exists "
                    f"in session {session_id}. Delete it first or use a different name."
                ),
            )

    # Persist each notebook: save bytes to disk + create one UnsolvedFile row.
    # parse requirements text at upload time (same as original single-file flow)
    # so it is available immediately for rubric generation and file matching.
    created: list[UnsolvedFile] = []
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
        created.append(unsolved)
        logger.info("Assignment file uploaded: %s → session %d", filename, session_id)

    db.commit()
    for u in created:
        db.refresh(u)

    return [UnsolvedFileRead.from_orm_model(u) for u in created]


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


# ── Rubric Generation (TEMP) ──────────────────────────────────────────────────

# TEMP: for manual testing only — Sub-feature 2.8's pipeline will call this
# automatically instead of exposing it directly.
@router.post(
    "/{file_id}/generate-rubric",
    summary="Generate rubric for an assignment file (instructor only) [TEMP]",
)
def generate_rubric(
    session_id: int,
    file_id: int,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> dict:
    _get_session_or_404(session_id, db)
    _get_file_or_404(file_id, session_id, db)
    from app.services.rubric import generate_rubric_for_unsolved_file
    return generate_rubric_for_unsolved_file(db, file_id)
