"""
Phase 3, Sub-feature 3.3: POST /chat.

Pure orchestration — resolves a free-text instructor instruction into a
concrete session + optional student scope via 3.1 (session_matcher) and 3.2
(instruction_filter), then triggers the exact same grade_session_batch
pipeline Phase 2 already built. No new grading logic lives here.

A new top-level router (rather than folding this into sessions.py) because
/chat isn't a sub-resource of a specific session — it's the entry point that
*resolves* which session applies, and Phase 3.4's SSE variant will extend
this same file, not sessions.py.

Every branch returns HTTP 200 with a "status" field distinguishing the
outcome — these are normal conversational outcomes ("couldn't find that
session", "which student did you mean?"), not errors. 403 is reserved for
actual non-instructor access; 422 for a malformed request body (missing
"instruction" entirely, handled automatically by the Pydantic model below).

Response shapes (one per "status" value)
─────────────────────────────────────────
    {"status": "no_session_match", "message": str}
    {"status": "ambiguous_session", "candidates": [{"session_id", "session_title", "confidence"}, ...]}
    {"status": "student_not_found", "attempted_name": str}
    {"status": "ambiguous_student", "session_id", "session_title",
     "candidates": [{"student_id", "student_name"}, ...]}
    {"status": "unsupported_filter", "reason": str}
    {"status": "graded", "session_id", "session_title", "scope": "all" | "student",
     "student_name": str (only when scope == "student"),
     "events": [...], "summary": {...}}
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services.auth import require_instructor
from app.services.instruction_filter import parse_grading_filter
from app.services.session_matcher import match_instruction_to_session

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatInstruction(BaseModel):
    """Body for POST /chat — instructor's free-text instruction."""
    instruction: str


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    summary="Resolve a free-text grading instruction and act on it (instructor only)",
)
def chat(
    body: ChatInstruction,
    db: Annotated[Session, Depends(get_db)],
    instructor: Annotated[User, Depends(require_instructor)],
) -> dict:
    """
    Resolution order: session_matcher (3.1) -> instruction_filter (3.2) ->
    grade_session_batch (2.7), fully drained (no SSE yet — that's 3.4).
    """
    session_match = match_instruction_to_session(
        body.instruction, instructor_id=instructor.id, db=db
    )

    if session_match["status"] == "no_match":
        return {
            "status": "no_session_match",
            "message": "Could not find a session matching that instruction.",
        }

    if session_match["status"] == "ambiguous":
        return {
            "status": "ambiguous_session",
            "candidates": session_match["candidates"],
        }

    session_id = session_match["session_id"]
    session_title = session_match["session_title"]

    filter_result = parse_grading_filter(body.instruction, session_id, db)

    if filter_result["scope"] == "not_found":
        return {
            "status": "student_not_found",
            "attempted_name": filter_result["attempted_name"],
        }

    if filter_result["scope"] == "ambiguous":
        return {
            "status": "ambiguous_student",
            "session_id": session_id,
            "session_title": session_title,
            "candidates": filter_result["candidates"],
        }

    if filter_result["scope"] == "unsupported":
        return {
            "status": "unsupported_filter",
            "reason": filter_result["reason"],
        }

    # filter_result["scope"] is "all" or "student" here.
    from app.services.grading_pipeline import grade_session_batch

    student_id = filter_result["student_id"] if filter_result["scope"] == "student" else None
    events = list(grade_session_batch(db, session_id, student_id=student_id))
    summary = events[-1] if events else {
        "event": "summary", "total": 0, "graded": 0, "failed": 0, "failures": [],
    }

    response: dict = {
        "status": "graded",
        "session_id": session_id,
        "session_title": session_title,
        "scope": filter_result["scope"],
        "events": events,
        "summary": summary,
    }
    if filter_result["scope"] == "student":
        response["student_name"] = filter_result["student_name"]

    return response
