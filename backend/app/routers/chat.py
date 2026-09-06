"""
Phase 3, Sub-features 3.3 (POST /chat) and 3.4 (POST /chat/stream).

Pure orchestration — resolves a free-text instructor instruction into a
concrete session + optional student scope via 3.1 (session_matcher) and 3.2
(instruction_filter), then triggers the exact same grade_session_batch
pipeline Phase 2 already built. No new grading logic lives here.

A new top-level router (rather than folding this into sessions.py) because
/chat isn't a sub-resource of a specific session — it's the entry point that
*resolves* which session applies.

Every /chat branch returns HTTP 200 with a "status" field distinguishing the
outcome — these are normal conversational outcomes ("couldn't find that
session", "which student did you mean?"), not errors. 403 is reserved for
actual non-instructor access; 422 for a malformed request body (missing
"instruction" entirely, handled automatically by the Pydantic model below).
/chat/stream carries the same outcomes but framed as SSE events instead of
one JSON blob (see its own docstring below).

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

import json
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services.auth import require_instructor
from app.services.instruction_filter import parse_grading_filter
from app.services.session_matcher import match_instruction_to_session

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatInstruction(BaseModel):
    """Body for POST /chat and /chat/stream — instructor's free-text instruction."""
    instruction: str


def _resolve_chat_instruction(instruction: str, current_user: User, db: Session) -> dict:
    """
    Shared resolution logic for /chat and /chat/stream: session_matcher (3.1)
    -> instruction_filter (3.2). Extracted in 3.4 so both endpoints share one
    implementation instead of /chat/stream duplicating /chat's inlined logic.

    Returns one of:
      - An early-exit dict — exactly one of /chat's own "no_session_match" /
        "ambiguous_session" / "student_not_found" / "ambiguous_student" /
        "unsupported_filter" shapes (see module docstring). The caller must
        return/stream this AS-IS and not proceed to grading.
      - {"resolved": True, "session_id": int, "session_title": str,
         "student_id": int | None, "student_name": str | None} — the caller
        should proceed to grade_session_batch(db, session_id, student_id).
    """
    session_match = match_instruction_to_session(
        instruction, instructor_id=current_user.id, db=db
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

    filter_result = parse_grading_filter(instruction, session_id, db)

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
    student_id = filter_result["student_id"] if filter_result["scope"] == "student" else None
    student_name = filter_result["student_name"] if filter_result["scope"] == "student" else None

    return {
        "resolved": True,
        "session_id": session_id,
        "session_title": session_title,
        "student_id": student_id,
        "student_name": student_name,
    }


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
    Resolves via _resolve_chat_instruction, then — if resolved — fully drains
    grade_session_batch (2.7) and returns one JSON response. No SSE here;
    see POST /chat/stream for the live-progress variant.
    """
    resolution = _resolve_chat_instruction(body.instruction, instructor, db)
    if not resolution.get("resolved"):
        return resolution

    from app.services.grading_pipeline import grade_session_batch

    session_id = resolution["session_id"]
    student_id = resolution["student_id"]
    scope = "student" if student_id is not None else "all"

    events = list(grade_session_batch(db, session_id, student_id=student_id))
    summary = events[-1] if events else {
        "event": "summary", "total": 0, "graded": 0, "failed": 0, "failures": [],
    }

    response: dict = {
        "status": "graded",
        "session_id": session_id,
        "session_title": resolution["session_title"],
        "scope": scope,
        "events": events,
        "summary": summary,
    }
    if scope == "student":
        response["student_name"] = resolution["student_name"]

    return response


@router.post(
    "/stream",
    summary="Resolve a free-text grading instruction and stream progress live via SSE (instructor only)",
)
def chat_stream(
    body: ChatInstruction,
    db: Annotated[Session, Depends(get_db)],
    instructor: Annotated[User, Depends(require_instructor)],
) -> StreamingResponse:
    """
    Same resolution as POST /chat, same access control, but every branch
    streams as text/event-stream instead of returning plain JSON:
      - An early-exit resolution (no_session_match/ambiguous_session/
        student_not_found/ambiguous_student/unsupported_filter) streams as
        exactly ONE SSE event, then the stream closes.
      - A resolved instruction streams grade_session_batch's own events
        live, one SSE event per yield (checking/graded/failed), ending
        naturally on the generator's final "summary" event — the generator
        is consumed incrementally here, never drained into a list first.

    Each SSE event is framed as ``f"data: {json.dumps(event)}\\n\\n"``.
    """

    def event_stream():
        resolution = _resolve_chat_instruction(body.instruction, instructor, db)
        if not resolution.get("resolved"):
            yield f"data: {json.dumps(resolution)}\n\n"
            return

        from app.services.grading_pipeline import grade_session_batch

        for event in grade_session_batch(
            db, resolution["session_id"], student_id=resolution["student_id"]
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
