"""
Assignment file upload (instructor) and download (any authenticated user).

bugfix-original-upload-preservation: every listing/download/delete here now
operates on AssignmentUpload — the exact file the instructor uploaded, one
row per upload event, regardless of what it contains. A zip is one row with
its own filename; downloading it returns the original bytes byte-for-byte,
never a reconstruction and never a browse into what's inside it.

Internally, notebooks and resources are still extracted from a zip exactly
as before (UnsolvedFile for grading, ResourceFile for historical reasons) —
that logic is untouched — but neither is independently listed, downloaded,
or deleted anymore. Deleting an AssignmentUpload cascades to whatever it
produced (both the DB rows and their files on disk), via
UnsolvedFile.source_upload_id / ResourceFile.source_upload_id.

POST   /sessions/{session_id}/assignments                    → upload one or more files of any type,
                                                                 or a .zip archive (recursively extracted
                                                                 internally). Returns one record per upload.
GET    /sessions/{session_id}/assignments                    → list what was uploaded
GET    /sessions/{session_id}/assignments/{upload_id}/download → download the original bytes, exactly
DELETE /sessions/{session_id}/assignments/{upload_id}         → remove an upload and everything it produced
POST   /sessions/{session_id}/assignments/{file_id}/generate-rubric → unchanged; still keyed by the
                                                                 internal UnsolvedFile id, for grading use
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
from app.models.assignment_upload import AssignmentUpload
from app.models.unsolved_file import UnsolvedFile
from app.models.resource_file import ResourceFile
from app.models.user import User
from app.schemas.assignment_upload import AssignmentUploadRead
from app.services.auth import get_current_user, require_instructor
from app.services.storage import (
    absolute_path,
    save_assignment_file,
    save_original_upload_file,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sessions/{session_id}/assignments",
    tags=["assignments"],
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_session_or_404(session_id: int, db: Session) -> LMSSession:
    session = db.get(LMSSession, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    return session


def _get_upload_or_404(upload_id: int, session_id: int, db: Session) -> AssignmentUpload:
    u = db.get(AssignmentUpload, upload_id)
    if u is None or u.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Assignment file {upload_id} not found in session {session_id}.",
        )
    return u


async def _extract_for_grading(
    filename: str, data: bytes,
) -> tuple[list[tuple[str, bytes]], list[tuple[str, bytes]]]:
    """
    Run the EXISTING internal extraction for one uploaded file, unchanged from
    before this fix: a .ipynb is one notebook; a .zip is recursively expanded
    into notebooks (gradeable) and resources (everything else, download-only
    for historical reasons — no longer independently listed). Any other
    extension yields nothing to extract; the raw upload is still saved as
    itself by the caller regardless.

    Returns (notebooks, resources) as (filename, bytes) pairs — same shape
    _collect_files returned before, just for a single file at a time so the
    caller can still save the original bytes for every extension, not only
    .ipynb/.zip.
    """
    suffix = Path(filename).suffix.lower()

    if suffix == ".ipynb":
        return [(filename, data)], []

    if suffix == ".zip":
        from app.services.notebook import extract_files_from_zip

        notebooks: list[tuple[str, bytes]] = []
        resources: list[tuple[str, bytes]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            nb_paths, res_paths = extract_files_from_zip(data, tmp_dir)
            # Read bytes inside the context manager, before cleanup.
            for nb_path in nb_paths:
                notebooks.append((nb_path.name, nb_path.read_bytes()))
            for res_path in res_paths:
                resources.append((res_path.name, res_path.read_bytes()))
        return notebooks, resources

    # Any other file type: nothing extracted for the grading pipeline. The
    # original upload itself is still saved and listed — see upload_assignment.
    return [], []


def _existing_filename_conflict(
    session_id: int, filename: str, db: Session
) -> bool:
    """
    True if *filename* already exists in this session as a notebook, a
    resource, or an original upload. Notebooks/resources still share one
    on-disk directory keyed by original_filename (unchanged), and original
    uploads have their own separate directory but the same one-name-per-
    session expectation, so all three are checked together.
    """
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
    if in_resources:
        return True
    in_uploads = (
        db.query(AssignmentUpload)
        .filter(
            AssignmentUpload.session_id == session_id,
            AssignmentUpload.original_filename == filename,
        )
        .first()
    )
    return in_uploads is not None


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=list[AssignmentUploadRead],
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Upload assignment file(s) to a session (instructor only). Any file "
        "type is accepted, single or bundled in a .zip. Returns one record "
        "per file uploaded — a zip is one record with its own filename, "
        "never a list of what's inside it. Notebooks (standalone or inside "
        "a zip) are still extracted internally for grading, exactly as "
        "before; that never appears in this response."
    ),
)
async def upload_assignment(
    session_id: int,
    files: Annotated[
        list[UploadFile],
        File(description="One or more files of any type, or a single .zip archive."),
    ],
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> list[AssignmentUploadRead]:
    _get_session_or_404(session_id, db)

    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one file must be uploaded.",
        )

    # Read every upload's raw bytes once, up front, and run the existing
    # extraction logic for each — before writing anything, so a duplicate
    # filename found anywhere in the batch fails the whole request atomically.
    raw_uploads: list[tuple[str, str | None, bytes]] = []
    per_upload_notebooks: list[list[tuple[str, bytes]]] = []
    per_upload_resources: list[list[tuple[str, bytes]]] = []

    for upload in files:
        filename = upload.filename or "upload"
        data = await upload.read()
        raw_uploads.append((filename, upload.content_type, data))
        notebooks, resources = await _extract_for_grading(filename, data)
        per_upload_notebooks.append(notebooks)
        per_upload_resources.append(resources)

    # Duplicate-filename guard — spans notebooks, resources, AND original
    # upload filenames. Checked before persisting anything so the request
    # fails atomically on any conflict (no partial writes).
    all_names = [name for name, _, _ in raw_uploads]
    for notebooks in per_upload_notebooks:
        all_names.extend(name for name, _ in notebooks)
    for resources in per_upload_resources:
        all_names.extend(name for name, _ in resources)
    for filename in all_names:
        if _existing_filename_conflict(session_id, filename, db):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"A file named '{filename}' already exists "
                    f"in session {session_id}. Delete it first or use a different name."
                ),
            )

    created_uploads: list[AssignmentUpload] = []

    for (filename, content_type, data), notebooks, resources in zip(
        raw_uploads, per_upload_notebooks, per_upload_resources
    ):
        # Save the raw upload exactly as received — this is what listing,
        # download, and the UI serve from now on.
        original_rel_path = await save_original_upload_file(session_id, filename, data)
        upload_row = AssignmentUpload(
            session_id=session_id,
            original_filename=filename,
            content_type=content_type,
            file_path=original_rel_path,
        )
        db.add(upload_row)
        db.flush()  # need upload_row.id for the FK below

        # Persist extracted notebooks exactly as before (grading pipeline
        # never sees or cares about AssignmentUpload).
        for nb_filename, nb_data in notebooks:
            rel_path = await save_assignment_file(session_id, nb_filename, nb_data)
            parsed_text: str | None = None
            try:
                from app.services.notebook import extract_requirements_text
                parsed_text = extract_requirements_text(absolute_path(rel_path))
            except Exception as exc:
                logger.warning(
                    "Could not parse requirements text from %s: %s", nb_filename, exc
                )
            unsolved = UnsolvedFile(
                session_id=session_id,
                original_filename=nb_filename,
                file_path=rel_path,
                parsed_requirements_text=parsed_text,
                source_upload_id=upload_row.id,
            )
            db.add(unsolved)
            logger.info(
                "Assignment notebook extracted: %s → session %d (from upload %r)",
                nb_filename, session_id, filename,
            )

        # Persist extracted resources exactly as before.
        for res_filename, res_data in resources:
            rel_path = await save_assignment_file(session_id, res_filename, res_data)
            resource = ResourceFile(
                session_id=session_id,
                original_filename=res_filename,
                file_path=rel_path,
                source_upload_id=upload_row.id,
            )
            db.add(resource)
            logger.info(
                "Resource file extracted: %s → session %d (from upload %r)",
                res_filename, session_id, filename,
            )

        created_uploads.append(upload_row)
        logger.info("Assignment file uploaded: %s → session %d", filename, session_id)

    db.commit()
    for u in created_uploads:
        db.refresh(u)

    return [AssignmentUploadRead.from_orm_model(u) for u in created_uploads]


# ── List ──────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[AssignmentUploadRead],
    summary="List assignment files for a session — exactly what was uploaded",
)
def list_assignments(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[AssignmentUploadRead]:
    _get_session_or_404(session_id, db)
    uploads = (
        db.query(AssignmentUpload)
        .filter(AssignmentUpload.session_id == session_id)
        .order_by(AssignmentUpload.uploaded_at)
        .all()
    )
    return [AssignmentUploadRead.from_orm_model(u) for u in uploads]


# ── Download ──────────────────────────────────────────────────────────────────

@router.get(
    "/{upload_id}/download",
    summary="Download an assignment file exactly as it was uploaded",
    response_class=FileResponse,
)
def download_assignment(
    session_id: int,
    upload_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    u = _get_upload_or_404(upload_id, session_id, db)
    abs_path = absolute_path(u.file_path)
    if not abs_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File is recorded in the database but not found on disk.",
        )
    return FileResponse(
        path=str(abs_path),
        filename=u.original_filename,
        media_type=u.content_type or "application/octet-stream",
    )


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete(
    "/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an assignment file and everything it produced (instructor only)",
)
def delete_assignment(
    session_id: int,
    upload_id: int,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> None:
    u = _get_upload_or_404(upload_id, session_id, db)

    # Remove derived notebook/resource files from disk before the DB cascade
    # deletes their rows (ondelete=CASCADE via source_upload_id handles the
    # rows; it does nothing to the files those rows point at).
    for nb in db.query(UnsolvedFile).filter(UnsolvedFile.source_upload_id == u.id):
        nb_path = absolute_path(nb.file_path)
        if nb_path.exists():
            nb_path.unlink()
    for res in db.query(ResourceFile).filter(ResourceFile.source_upload_id == u.id):
        res_path = absolute_path(res.file_path)
        if res_path.exists():
            res_path.unlink()

    abs_path = absolute_path(u.file_path)
    if abs_path.exists():
        abs_path.unlink()

    db.delete(u)  # cascades to UnsolvedFile/ResourceFile rows via source_upload_id
    db.commit()


# ── Rubric Generation ────────────────────────────────────────────────────────
# Unchanged: still keyed by the internal UnsolvedFile id, for grading-pipeline
# and MCP use. Never called from the assignment-files UI (it lists uploads,
# not notebooks), but real API/MCP consumers still need it exactly as before.

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
    f = db.get(UnsolvedFile, file_id)
    if f is None or f.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Assignment file {file_id} not found in session {session_id}.",
        )
    from app.services.rubric import generate_rubric_for_unsolved_file
    return generate_rubric_for_unsolved_file(db, file_id, force=force)
