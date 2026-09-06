"""
Phase 4, Sub-feature 4.5: grading-pipeline MCP tools.

Thin MCP wrappers over Phase 2.7's grading pipeline. No grading logic
lives here — each tool opens a database session, delegates to the
existing function, and returns its result unaltered. Same
register(server) pattern as 4.2-4.4.

Signatures verified rather than assumed (they vary between services):
both grading functions take ``db`` FIRST, and grade_session_batch takes
the optional ``student_id`` filter added in 3.3.

Streaming
─────────
grade_session_batch is a generator, but an MCP tool call is a single
request/response — it is not a streaming channel. So grade_session
DRAINS the generator fully and returns every event plus the final
summary at once, exactly as the non-streaming REST
``POST /sessions/{id}/grade`` already does. Live per-file progress for a
human already exists over SSE at ``POST /chat/stream`` (3.4); MCP does
not duplicate it. The practical consequence: a large batch returns
nothing until the whole run finishes, so prefer grade_submission_file
or the student_id filter when a caller wants smaller units of work.

Unlike evaluate_submission (4.4), these tools DO persist Grade rows —
that is the whole point of the pipeline.
"""

import logging

from mcp.server import MCPServer

from app.database import SessionLocal
from app.services.grading_pipeline import (
    grade_session_batch,
    grade_single_submission_file,
)

logger = logging.getLogger(__name__)


def grade_submission_file(submission_file_id: int) -> dict:
    """
    Grade one student notebook and persist the resulting Grade row.

    Returns Phase 2.7's result dict exactly as-is — one of:
      {"submission_file_id": int, "student_id": int, "filename": str,
       "success": True, "score": float}
      {"submission_file_id": int, "student_id": int, "filename": str,
       "success": False, "error": str}

    Never raises: an individual failure (unmatched file, unparseable
    notebook, LLM outage) comes back as a failure dict, not an exception.
    Re-grading an already-graded file overwrites its existing Grade.
    """
    db = SessionLocal()
    try:
        result = grade_single_submission_file(db, submission_file_id)
    finally:
        db.close()

    logger.info(
        "MCP grade_submission_file: submission_file_id=%d -> success=%s",
        submission_file_id, result.get("success"),
    )
    return result


def grade_session(session_id: int, student_id: int | None = None) -> dict:
    """
    Grade every ungraded submission in a session, optionally for one student.

    Returns the same shape as the REST batch endpoint:
      {"events": [...], "summary": {...}}

    ``events`` is every progress event the pipeline produced, in order —
    "checking" before each file, then "graded" or "failed" for it — ending
    with the "summary" event, which is also returned separately as
    ``summary`` for convenience. An individual file failing never aborts
    the run; it is recorded in the summary's ``failures`` list and the
    batch continues.

    Only ungraded files are processed, so calling this again after a
    completed run is a no-op returning a zero-count summary rather than
    re-grading everything. Pass student_id to restrict the run to one
    student's submissions.
    """
    db = SessionLocal()
    try:
        # Drained here, deliberately: an MCP call is request/response, not
        # a stream. See the module docstring.
        events = list(grade_session_batch(db, session_id, student_id=student_id))
    finally:
        db.close()

    summary = events[-1] if events else {
        "event": "summary", "total": 0, "graded": 0, "failed": 0, "failures": [],
    }

    logger.info(
        "MCP grade_session: session_id=%d student_id=%s -> %d event(s), "
        "%d graded, %d failed",
        session_id, student_id, len(events),
        summary.get("graded", 0), summary.get("failed", 0),
    )
    return {"events": events, "summary": summary}


def register(server: MCPServer) -> None:
    """Register this module's tools on *server*."""
    server.add_tool(
        grade_submission_file,
        name="grade_submission_file",
        description=(
            "Grade one student notebook and record the grade. Returns the "
            "score out of 10 on success, or an error describing why that file "
            "could not be graded. Re-grading a file overwrites its existing "
            "grade."
        ),
    )
    server.add_tool(
        grade_session,
        name="grade_session",
        description=(
            "Grade every ungraded submission in a session, optionally "
            "restricted to one student via student_id. Returns the full list "
            "of per-file progress events plus a summary of how many were "
            "graded and how many failed. Already-graded files are skipped, so "
            "re-running is safe. This runs to completion before returning, so "
            "a large session may take a while; a single file can be graded "
            "with grade_submission_file instead."
        ),
    )
