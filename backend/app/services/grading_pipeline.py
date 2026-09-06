"""
Batch grading pipeline — Phase 2, Sub-feature 7/8.

Provides two public callables:

    grade_single_submission_file(db, submission_file_id) → dict
        Hardened wrapper around generate_feedback_and_persist.
        Never raises — always returns a success or failure dict.

    grade_session_batch(db, session_id) → Generator[dict, None, None]
        Generator that grades every ungraded SubmissionFile in a Session
        one at a time, yielding progress events as it goes:
            {"event": "checking",  "student_id": int, "filename": str}
            {"event": "graded",    "student_id": int, "filename": str, "score": float}
            {"event": "failed",    "student_id": int, "filename": str, "error": str}
            {"event": "summary",   "total": int, "graded": int, "failed": int,
                                   "failures": [{"student_id", "filename", "error"}, ...]}

        Individual failures never abort the batch.  Phase 3 will consume this
        generator over SSE; the POST /sessions/{id}/grade endpoint in
        sessions.py drains it eagerly and returns all events at once.
"""

import logging
from collections.abc import Generator
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── 1. grade_single_submission_file ──────────────────────────────────────────

def grade_single_submission_file(db: Session, submission_file_id: int) -> dict[str, Any]:
    """
    Hardened wrapper around ``generate_feedback_and_persist``.

    Loads the SubmissionFile row first to extract ``student_id`` (via the
    parent Submission) and ``original_filename`` for reporting.  Then calls
    ``generate_feedback_and_persist``; any exception anywhere in the
    underlying chain is caught and converted to a clean failure dict.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    submission_file_id:
        PK of the SubmissionFile to grade.

    Returns
    -------
    dict
        Success shape::

            {
                "submission_file_id": int,
                "student_id": int,
                "filename": str,
                "success": True,
                "score": float,
            }

        Failure shape::

            {
                "submission_file_id": int,
                "student_id": int,
                "filename": str,
                "success": False,
                "error": str,
            }
    """
    from app.models.submission_file import SubmissionFile

    # ── Resolve identity info for reporting ──────────────────────────────────
    sub_file: SubmissionFile | None = db.get(SubmissionFile, submission_file_id)

    if sub_file is None:
        logger.warning(
            "grade_single_submission_file: SubmissionFile %d not found.",
            submission_file_id,
        )
        return {
            "submission_file_id": submission_file_id,
            "student_id": -1,
            "filename": "unknown",
            "success": False,
            "error": f"SubmissionFile {submission_file_id} not found.",
        }

    student_id: int = sub_file.submission.student_id
    filename: str = sub_file.original_filename

    base: dict[str, Any] = {
        "submission_file_id": submission_file_id,
        "student_id": student_id,
        "filename": filename,
    }

    # ── Call the full pipeline, catching every possible exception ─────────────
    try:
        from app.services.feedback import generate_feedback_and_persist

        result = generate_feedback_and_persist(db, submission_file_id)

        if result.get("success"):
            logger.info(
                "grade_single_submission_file: SubmissionFile %d graded — "
                "score %.1f for student %d.",
                submission_file_id, result["score"], student_id,
            )
            return {**base, "success": True, "score": float(result["score"])}
        else:
            error_msg: str = result.get("error") or "Unknown evaluation failure."
            logger.warning(
                "grade_single_submission_file: SubmissionFile %d evaluation "
                "returned failure for student %d: %s",
                submission_file_id, student_id, error_msg,
            )
            return {**base, "success": False, "error": error_msg}

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "grade_single_submission_file: unexpected exception for "
            "SubmissionFile %d (student %d): %s",
            submission_file_id, student_id, exc,
            exc_info=True,
        )
        return {**base, "success": False, "error": str(exc)}


# ── 2. grade_session_batch ────────────────────────────────────────────────────

def grade_session_batch(
    db: Session,
    session_id: int,
    student_id: int | None = None,
) -> Generator[dict[str, Any], None, None]:
    """
    Generator that grades every ungraded SubmissionFile in a Session.

    Yields progress events one at a time so callers can stream them
    incrementally (e.g. over SSE in Phase 3).  The TEMP endpoint in
    sessions.py drains the generator eagerly and returns all events at once.

    Event shapes
    ------------
    ``checking``  — emitted *before* grading starts for each file::

        {"event": "checking", "student_id": int, "filename": str}

    ``graded``    — emitted after a successful grade::

        {"event": "graded", "student_id": int, "filename": str, "score": float}

    ``failed``    — emitted after a failure; batch continues::

        {"event": "failed", "student_id": int, "filename": str, "error": str}

    ``summary``   — always the final event::

        {
            "event": "summary",
            "total": int,
            "graded": int,
            "failed": int,
            "failures": [{"student_id": int, "filename": str, "error": str}, ...]
        }

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    session_id:
        PK of the LMSSession whose ungraded files should be processed.
    student_id:
        If given (Phase 3.3), only that student's ungraded SubmissionFiles
        are processed — everyone else in the session is left untouched.
        Default None preserves the exact original behavior: every ungraded
        file in the session.
    """
    from app.models.submission import Submission
    from app.models.submission_file import SubmissionFile

    # ── Collect all ungraded SubmissionFile rows for this session ─────────────
    query = (
        db.query(SubmissionFile)
        .join(Submission, SubmissionFile.submission_id == Submission.id)
        .filter(
            Submission.session_id == session_id,
            SubmissionFile.graded == False,  # noqa: E712 — SQLAlchemy requires ==
        )
    )
    if student_id is not None:
        query = query.filter(Submission.student_id == student_id)

    ungraded_files: list[SubmissionFile] = query.order_by(SubmissionFile.id).all()

    total = len(ungraded_files)

    if total == 0:
        logger.info(
            "grade_session_batch: session %d has no ungraded files.", session_id
        )
        yield {
            "event": "summary",
            "total": 0,
            "graded": 0,
            "failed": 0,
            "failures": [],
        }
        return

    logger.info(
        "grade_session_batch: session %d — %d ungraded file(s) to process.",
        session_id, total,
    )

    graded_count = 0
    failed_count = 0
    failures: list[dict[str, Any]] = []

    for sub_file in ungraded_files:
        student_id: int = sub_file.submission.student_id
        filename: str = sub_file.original_filename

        # ── checking event ────────────────────────────────────────────────────
        yield {
            "event": "checking",
            "student_id": student_id,
            "filename": filename,
        }

        # ── grade (never raises — double-guarded) ────────────────────────────
        try:
            result = grade_single_submission_file(db, sub_file.id)
        except Exception as exc:  # noqa: BLE001 — last-resort safety net
            logger.error(
                "grade_session_batch: unexpected exception escaping "
                "grade_single_submission_file for SubmissionFile %d: %s",
                sub_file.id, exc, exc_info=True,
            )
            result = {
                "success": False,
                "error": f"Unexpected batch error: {exc}",
                "student_id": student_id,
                "filename": filename,
            }

        if result["success"]:
            graded_count += 1
            yield {
                "event": "graded",
                "student_id": student_id,
                "filename": filename,
                "score": result["score"],
            }
        else:
            failed_count += 1
            error_msg = result["error"]
            failures.append({
                "student_id": student_id,
                "filename": filename,
                "error": error_msg,
            })
            yield {
                "event": "failed",
                "student_id": student_id,
                "filename": filename,
                "error": error_msg,
            }

    # ── final summary event ───────────────────────────────────────────────────
    logger.info(
        "grade_session_batch: session %d complete — %d graded, %d failed.",
        session_id, graded_count, failed_count,
    )
    yield {
        "event": "summary",
        "total": total,
        "graded": graded_count,
        "failed": failed_count,
        "failures": failures,
    }
