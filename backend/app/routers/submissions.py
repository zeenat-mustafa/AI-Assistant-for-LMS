"""
Student submission upload, listing, and download.

Submission-side rework: every listing/instructor-download/delete here now
operates on SubmissionUpload — the exact file(s) a student submitted, one row
per upload event, regardless of what it contains. Uploads are ADDITIVE: each
call to POST creates new SubmissionUpload row(s) alongside whatever the
student already uploaded, rather than replacing them.

Internally, notebooks are still extracted from a zip (or used directly, for
a bare .ipynb) exactly as before — SubmissionFile, matched via
file_matcher.py — that logic is untouched. The only link between the two
sides is SubmissionFile.source_upload_id, which exists solely so deleting a
SubmissionUpload also removes what it produced (its file(s) and any Grade,
cascading).

POST   /sessions/{session_id}/submissions                        -> student uploads one or more files (any type,
                                                                      single or inside a .zip). Additive.
GET    /sessions/{session_id}/submissions                        -> instructor lists every student's submission
GET    /sessions/{session_id}/submissions/mine                   -> student views their own submission
GET    /sessions/{session_id}/submissions/uploads/{upload_id}/download -> instructor downloads one upload, byte-exact
DELETE /sessions/{session_id}/submissions/uploads/{upload_id}    -> student deletes their own upload (warn-and-confirm
                                                                      if it would remove a graded file)
POST   /sessions/{session_id}/submissions/files/{submission_file_id}/grade -> unchanged
"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.submission_upload import SubmissionUpload
from app.models.submission_file import SubmissionFile
from app.models.user import User
from app.schemas.submission import SubmissionRead
from app.services.auth import get_current_user, require_instructor
from app.services.storage import (
    absolute_path,
    relative_path,
    save_submission_original_file,
    submission_extract_dir,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sessions/{session_id}/submissions",
    tags=["submissions"],
)


def _get_session_or_404(session_id: int, db: Session) -> LMSSession:
    session = db.get(LMSSession, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    return session


def _get_upload_or_404(session_id: int, upload_id: int, db: Session) -> SubmissionUpload:
    u = db.get(SubmissionUpload, upload_id)
    if u is None or u.submission.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submission upload {upload_id} not found in session {session_id}.",
        )
    return u


def _unique_extract_path(extract_dir: Path, filename: str) -> Path:
    """
    A collision-safe destination for one notebook copied into the grading
    extraction directory, real filesystem state checked each call (not just
    within one batch) — matches notebook.py's `_safe_extract_name` behavior,
    duplicated locally in ~6 lines rather than importing a private helper
    across modules for one small piece of logic.
    """
    stem, suffix = Path(filename).stem, Path(filename).suffix
    candidate = extract_dir / filename
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = extract_dir / f"{stem}_{counter}{suffix}"
    return candidate


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=SubmissionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload solved submission file(s) to a session — any file type, single or inside a .zip",
)
async def upload_submission(
    session_id: int,
    files: Annotated[
        list[UploadFile],
        File(description="One or more files of any type, or a single .zip archive."),
    ],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> SubmissionRead:
    _get_session_or_404(session_id, db)

    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one file must be uploaded.",
        )

    raw_uploads: list[tuple[str, str | None, bytes]] = []
    for upload in files:
        filename = upload.filename or "upload"
        data = await upload.read()
        raw_uploads.append((filename, upload.content_type, data))

    # Additive: reuse the student's existing Submission container for this
    # session if one exists; otherwise this is their first-ever upload here.
    submission = (
        db.query(Submission)
        .filter(
            Submission.session_id == session_id,
            Submission.student_id == current_user.id,
        )
        .first()
    )
    if submission is None:
        first_filename, _, _ = raw_uploads[0]
        submission = Submission(
            session_id=session_id,
            student_id=current_user.id,
            # Legacy NOT NULL columns from before uploads became additive —
            # no longer authoritative (see `uploads` below) and never read
            # elsewhere; set once, honestly, from the first real upload and
            # never updated again.
            original_filename=first_filename,
            uploaded_file_path="",
        )
        db.add(submission)
        db.flush()  # need submission.id for the FK below

    new_submission_files: list[SubmissionFile] = []
    created_uploads: list[SubmissionUpload] = []

    for filename, content_type, data in raw_uploads:
        upload_row = SubmissionUpload(
            submission_id=submission.id,
            original_filename=filename,
            content_type=content_type,
            file_path="",  # set below once we know the saved path
        )
        db.add(upload_row)
        db.flush()  # need upload_row.id — both for the saved path and the FK

        rel_path = await save_submission_original_file(
            session_id, current_user.id, upload_row.id, filename, data
        )
        upload_row.file_path = rel_path
        if submission.uploaded_file_path == "":
            submission.uploaded_file_path = rel_path

        suffix = Path(filename).suffix.lower()

        if suffix == ".ipynb":
            extract_dir = submission_extract_dir(session_id, current_user.id)
            dest = _unique_extract_path(extract_dir, filename)
            dest.write_bytes(data)
            sf = SubmissionFile(
                submission_id=submission.id,
                original_filename=filename,
                extracted_ipynb_path=relative_path(dest),
                source_upload_id=upload_row.id,
            )
            db.add(sf)
            new_submission_files.append(sf)

        elif suffix == ".zip":
            from app.services.notebook import extract_notebooks_from_zip

            extract_dir = submission_extract_dir(session_id, current_user.id)
            # Zero notebooks inside is a valid, successful upload — the raw
            # zip is still saved and listed above; nothing extra to do here.
            for nb_path in extract_notebooks_from_zip(data, extract_dir):
                sf = SubmissionFile(
                    submission_id=submission.id,
                    original_filename=nb_path.name,
                    extracted_ipynb_path=relative_path(nb_path),
                    source_upload_id=upload_row.id,
                )
                db.add(sf)
                new_submission_files.append(sf)

        # Any other extension: nothing to extract for grading. The raw
        # upload itself is still saved and listed — see SubmissionUpload row
        # created above.

        created_uploads.append(upload_row)
        logger.info(
            "Submission upload: student %d -> session %d (%s)",
            current_user.id, session_id, filename,
        )

    db.commit()
    db.refresh(submission)

    # Match only the notebooks extracted from THIS call, not every file the
    # student has ever submitted — match_all_files_in_submission() re-scores
    # every SubmissionFile under the submission on every call, which would
    # risk re-matching (and silently changing) an already-graded file's
    # match. Reuses the same scoring function file_matcher.py already
    # exposes for one file; no matching logic is duplicated or altered.
    if new_submission_files:
        try:
            from app.models.unsolved_file import UnsolvedFile
            from app.services.file_matcher import match_submission_file_to_unsolved
            from app.services.notebook import parse_notebook_file

            unsolved_candidates = [
                {
                    "id": uf.id,
                    "filename": uf.original_filename,
                    "requirements_text": uf.parsed_requirements_text,
                }
                for uf in db.query(UnsolvedFile).filter(UnsolvedFile.session_id == session_id).all()
            ]
            matched_count = 0
            for sf in new_submission_files:
                parsed = parse_notebook_file(absolute_path(sf.extracted_ipynb_path))
                submitted_markdown = parsed["markdown_text"] if parsed["valid"] else ""
                match_result = match_submission_file_to_unsolved(
                    submitted_markdown=submitted_markdown,
                    submitted_filename=sf.original_filename,
                    unsolved_candidates=unsolved_candidates,
                )
                sf.matched_unsolved_file_id = match_result["matched_unsolved_file_id"]
                if match_result["status"] == "matched":
                    matched_count += 1
            db.commit()
            logger.info(
                "File matching complete: submission %d — %d/%d new file(s) matched.",
                submission.id, matched_count, len(new_submission_files),
            )
        except Exception as exc:
            logger.warning(
                "File matching failed for submission %d (non-fatal): %s",
                submission.id, exc, exc_info=True,
            )

    db.refresh(submission)
    return SubmissionRead.from_orm_model(submission)


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


def _serve_upload_file(u: SubmissionUpload) -> FileResponse:
    """Shared byte-serving logic for both download endpoints below — the
    exact original bytes, byte-for-byte, regardless of who is asking."""
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


# ── Download (instructor) ─────────────────────────────────────────────────────

@router.get(
    "/uploads/{upload_id}/download",
    summary="Download a student's submission upload exactly as it was uploaded (instructor only)",
    response_class=FileResponse,
)
def download_submission_upload(
    session_id: int,
    upload_id: int,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> FileResponse:
    u = _get_upload_or_404(session_id, upload_id, db)
    return _serve_upload_file(u)


# ── Download (student, own upload only) ───────────────────────────────────────

@router.get(
    "/mine/uploads/{upload_id}/download",
    summary="Download one of the current student's own submission uploads, exactly as it was uploaded",
    response_class=FileResponse,
)
def download_my_submission_upload(
    session_id: int,
    upload_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    u = _get_upload_or_404(session_id, upload_id, db)
    # Ownership only — never another student's, same 404-not-403 convention
    # as delete_submission_upload below (don't leak whether the id exists).
    if u.submission.student_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submission upload {upload_id} not found in session {session_id}.",
        )
    return _serve_upload_file(u)


# ── Delete (student, own upload only) ─────────────────────────────────────────

@router.delete(
    "/uploads/{upload_id}",
    summary=(
        "Remove one of the current student's own submission uploads. If it "
        "would remove a graded file, returns 409 naming the real score(s) "
        "unless called again with confirm=true."
    ),
)
def delete_submission_upload(
    session_id: int,
    upload_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    confirm: bool = Query(False, description="Must be true to delete an upload with a graded file."),
) -> dict:
    u = _get_upload_or_404(session_id, upload_id, db)
    # Ownership only — never another student's, and an instructor has no
    # submission of their own to own this upload either.
    if u.submission.student_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submission upload {upload_id} not found in session {session_id}.",
        )

    linked_files = (
        db.query(SubmissionFile).filter(SubmissionFile.source_upload_id == u.id).all()
    )
    graded_files = [sf for sf in linked_files if sf.grade is not None]

    if graded_files and not confirm:
        if len(graded_files) == 1:
            sf = graded_files[0]
            detail = (
                f"This upload includes a graded file ('{sf.original_filename}') scoring "
                f"{sf.grade.score}/10. Pass confirm=true to delete it and its grade permanently."
            )
        else:
            scores = ", ".join(
                f"'{sf.original_filename}': {sf.grade.score}/10" for sf in graded_files
            )
            detail = (
                f"This upload includes {len(graded_files)} graded files ({scores}). "
                f"Pass confirm=true to delete them and their grades permanently."
            )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    for sf in linked_files:
        nb_path = absolute_path(sf.extracted_ipynb_path)
        if nb_path.exists():
            nb_path.unlink()

    abs_path = absolute_path(u.file_path)
    if abs_path.exists():
        abs_path.unlink()

    db.delete(u)  # cascades to SubmissionFile rows (and their Grade) via source_upload_id
    db.commit()
    return {"deleted": True, "upload_id": upload_id}


# ── Single-file grading ───────────────────────────────────────────────────────

@router.post(
    "/files/{submission_file_id}/grade",
    summary=(
        "Grade a single submission file and persist the result (instructor only). "
        "Evaluates the notebook against its matched rubric, assembles human-readable "
        "feedback, and writes a Grade record. Re-calling overwrites the existing grade."
    ),
)
def grade_submission_file(
    session_id: int,
    submission_file_id: int,
    db: Annotated[Session, Depends(get_db)],
    instructor: Annotated[User, Depends(require_instructor)],
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
    from app.services.grading_pipeline import grade_single_submission_file
    return grade_single_submission_file(
        db, submission_file_id, graded_by_instructor_id=instructor.id
    )
