"""
Assignment file upload (instructor) and download (any authenticated user).

POST   /sessions/{session_id}/assignments          → upload one .ipynb file
GET    /sessions/{session_id}/assignments          → list assignment files
GET    /sessions/{session_id}/assignments/{file_id}/download  → download a file
DELETE /sessions/{session_id}/assignments/{file_id}           → remove a file
"""

import logging
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
    assignment_dir,
    save_assignment_file,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sessions/{session_id}/assignments",
    tags=["assignments"],
)

_ALLOWED_EXTENSIONS = {".ipynb"}


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


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=UnsolvedFileRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an unsolved assignment file to a session (instructor only)",
)
async def upload_assignment(
    session_id: int,
    file: Annotated[UploadFile, File(description="A .ipynb notebook file")],
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> UnsolvedFileRead:
    _get_session_or_404(session_id, db)

    # Validate extension.
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Only .ipynb files are accepted for assignment upload. Got: '{suffix}'",
        )

    # Prevent duplicate filenames within the same session.
    existing = (
        db.query(UnsolvedFile)
        .filter(
            UnsolvedFile.session_id == session_id,
            UnsolvedFile.original_filename == file.filename,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"An assignment file named '{file.filename}' already exists "
                f"in session {session_id}. Delete it first or use a different name."
            ),
        )

    data = await file.read()
    rel_path = await save_assignment_file(session_id, file.filename, data)

    # Parse requirements text from the notebook at upload time so it's ready
    # for rubric generation and file matching later.  Import here to avoid
    # circular deps — notebook.py will be added in Phase 2.
    parsed_text: str | None = None
    try:
        from app.services.notebook import extract_requirements_text
        parsed_text = extract_requirements_text(absolute_path(rel_path))
    except Exception as exc:
        logger.warning(
            "Could not parse requirements text from %s: %s", file.filename, exc
        )

    unsolved = UnsolvedFile(
        session_id=session_id,
        original_filename=file.filename,
        file_path=rel_path,
        parsed_requirements_text=parsed_text,
    )
    db.add(unsolved)
    db.commit()
    db.refresh(unsolved)
    logger.info("Assignment file uploaded: %s → session %d", file.filename, session_id)
    return UnsolvedFileRead.from_orm_model(unsolved)


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
