"""
Student submission upload and listing.

POST  /sessions/{session_id}/submissions          → student uploads solved file(s)
GET   /sessions/{session_id}/submissions          → instructor lists all submissions
GET   /sessions/{session_id}/submissions/mine     → student views their own submission
"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.submission_file import SubmissionFile
from app.models.user import User, UserRole
from app.schemas.submission import SubmissionRead
from app.services.auth import get_current_user, require_instructor, require_student
from app.services.storage import (
    absolute_path,
    save_submission_file,
    submission_extract_dir,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sessions/{session_id}/submissions",
    tags=["submissions"],
)

_ALLOWED_EXTENSIONS = {".ipynb", ".zip"}


def _get_session_or_404(session_id: int, db: Session) -> LMSSession:
    session = db.get(LMSSession, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    return session


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=SubmissionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a solved submission (.ipynb or .zip) to a session",
)
async def upload_submission(
    session_id: int,
    file: Annotated[
        UploadFile,
        File(description="Solved .ipynb notebook or .zip archive containing .ipynb files"),
    ],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> SubmissionRead:
    _get_session_or_404(session_id, db)

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Only .ipynb or .zip files are accepted. Got: '{suffix}'",
        )

    data = await file.read()
    rel_path = await save_submission_file(session_id, current_user.id, file.filename, data)

    # Create (or replace) the Submission record for this student + session.
    # Re-submission overwrites the previous upload (consistent with re-grade behaviour).
    existing = (
        db.query(Submission)
        .filter(
            Submission.session_id == session_id,
            Submission.student_id == current_user.id,
        )
        .first()
    )
    if existing:
        # Remove old SubmissionFile records (grades cascade-delete via FK).
        for sf in existing.submission_files:
            db.delete(sf)
        db.delete(existing)
        db.flush()

    submission = Submission(
        session_id=session_id,
        student_id=current_user.id,
        original_filename=file.filename,
        uploaded_file_path=rel_path,
    )
    db.add(submission)
    db.flush()  # get submission.id before creating SubmissionFile rows

    # Create SubmissionFile records by extracting .ipynb paths now.
    # For .zip files, extraction happens here; for .ipynb, it's a single record.
    submission_files = _create_submission_files(
        db=db,
        submission=submission,
        suffix=suffix,
        data=data,
        session_id=session_id,
        student_id=current_user.id,
        original_filename=file.filename,
        rel_upload_path=rel_path,
    )

    db.commit()
    db.refresh(submission)
    logger.info(
        "Submission uploaded: student %d → session %d (%s, %d notebook(s))",
        current_user.id, session_id, file.filename, len(submission_files),
    )

    # Phase 2, Sub-feature 2: match each extracted notebook against the
    # session's unsolved files.  Runs synchronously after commit so that
    # matched_unsolved_file_id is populated before the response is returned.
    # Any matching failure is logged as a warning and never raises to the caller.
    try:
        from app.services.file_matcher import match_all_files_in_submission
        match_results = match_all_files_in_submission(db, submission.id)
        matched_count = sum(1 for r in match_results if r["status"] == "matched")
        logger.info(
            "File matching complete: submission %d — %d/%d file(s) matched.",
            submission.id, matched_count, len(match_results),
        )
        db.refresh(submission)
    except Exception as exc:
        logger.warning(
            "File matching failed for submission %d (non-fatal): %s",
            submission.id, exc, exc_info=True,
        )

    return SubmissionRead.from_orm_model(submission)


def _create_submission_files(
    *,
    db: Session,
    submission: Submission,
    suffix: str,
    data: bytes,
    session_id: int,
    student_id: int,
    original_filename: str,
    rel_upload_path: str,
) -> list[SubmissionFile]:
    """
    Build SubmissionFile rows for the uploaded file.
    - .ipynb: one record pointing at the upload path directly.
    - .zip: extract all nested .ipynb files, one record each.
    Extraction uses the notebook service (Phase 2).  If it's not available yet,
    we gracefully fall back to creating a single record with the upload path.
    """
    files: list[SubmissionFile] = []

    if suffix == ".ipynb":
        sf = SubmissionFile(
            submission_id=submission.id,
            original_filename=original_filename,
            extracted_ipynb_path=rel_upload_path,
        )
        db.add(sf)
        files.append(sf)

    else:  # .zip
        try:
            from app.services.notebook import extract_notebooks_from_zip
            extract_dir = submission_extract_dir(session_id, student_id)
            notebooks = extract_notebooks_from_zip(data, extract_dir)
            if not notebooks:
                raise ValueError("No .ipynb files found inside the .zip archive.")
            for nb_path in notebooks:
                from app.services.storage import relative_path
                sf = SubmissionFile(
                    submission_id=submission.id,
                    original_filename=nb_path.name,
                    extracted_ipynb_path=relative_path(nb_path),
                )
                db.add(sf)
                files.append(sf)
        except Exception as exc:
            logger.warning(
                "Could not extract notebooks from zip '%s': %s. "
                "Creating placeholder SubmissionFile record.",
                original_filename, exc,
            )
            # Placeholder so the submission is still recorded; grading will
            # flag it as a per-student failure (Section 5.6).
            sf = SubmissionFile(
                submission_id=submission.id,
                original_filename=original_filename,
                extracted_ipynb_path=rel_upload_path,
            )
            db.add(sf)
            files.append(sf)

    return files


# ── List (instructor) ─────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[SubmissionRead],
    summary="List all submissions for a session (instructor only)",
)
def list_submissions(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> list[SubmissionRead]:
    _get_session_or_404(session_id, db)
    subs = (
        db.query(Submission)
        .filter(Submission.session_id == session_id)
        .order_by(Submission.submitted_at)
        .all()
    )
    return [SubmissionRead.from_orm_model(s) for s in subs]


# ── My submission (student) ───────────────────────────────────────────────────

@router.get(
    "/mine",
    response_model=SubmissionRead | None,
    summary="Get the current student's own submission for a session",
)
def my_submission(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> SubmissionRead | None:
    _get_session_or_404(session_id, db)
    sub = (
        db.query(Submission)
        .filter(
            Submission.session_id == session_id,
            Submission.student_id == current_user.id,
        )
        .first()
    )
    if sub is None:
        return None
    return SubmissionRead.from_orm_model(sub)


# ── Submission Evaluation + Grade Persistence (TEMP) ─────────────────────────

# TEMP: for manual testing only — Sub-feature 8's full pipeline will replace
# this with a batch endpoint that handles every student in one call.
# This endpoint now persists a real Grade record (Sub-feature 5) so the
# existing Phase 1 GET /grades endpoints return real data immediately.
@router.post(
    "/files/{submission_file_id}/evaluate",
    summary=(
        "Evaluate a submission file and persist its grade (instructor only) [TEMP]. "
        "Calls generate_feedback_and_persist: evaluates the notebook, assembles "
        "human-readable feedback from the per-criterion explanations, and writes "
        "a Grade record to the database. Re-calling overwrites the existing grade."
    ),
)
def evaluate_submission(
    session_id: int,
    submission_file_id: int,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> dict:
    _get_session_or_404(session_id, db)
    sub_file = db.get(SubmissionFile, submission_file_id)
    if sub_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submission file {submission_file_id} not found.",
        )
    if sub_file.submission.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submission file {submission_file_id} does not belong to session {session_id}.",
        )
    from app.services.feedback import generate_feedback_and_persist
    return generate_feedback_and_persist(db, submission_file_id)

