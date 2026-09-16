"""
Phase 7, Sub-feature 7.5: student Q&A chatbot — POST /student-chat/stream.

A thin router: HTTP-specific concerns only (auth, request validation, SSE
framing). All resolution/retrieval/reply orchestration lives in
app.services.student_chat_service.run_student_chat, shared with the MCP
tool (app/mcp/tools/student_chat_tools.py) — Phase 7.8 audit fix (Item 1),
so the branching can never drift between the two callers again.

Student-only (require_student): conversation threads are keyed to a student,
and the scope-safety rule is written for students working on assignments.

Every SSE event is framed exactly like /chat/stream:
``f"data: {json.dumps(event)}\\n\\n"``.

Event sequence
──────────────
Ambiguous (no thread is created or touched):
    {"event": "clarification_needed", "message", "candidates": [{"session_id", "session_title", "best_similarity"}]}

Resolved:
    {"event": "resolved", "session_id", "session_title", "resolution": "current_session"|"redirected"|"broad_search"}
    {"event": "citations", "citations": [...]}   — the chunks GIVEN to the model
    {"event": "token", "text"}                   — one per streamed chunk
    {"event": "done", "thread_id", "user_message_id", "assistant_message_id"}
  or, if the model fails:
    {"event": "error", "message"}                — clean, generic; never raw

Persistence: the question and answer are saved together, only after the
stream completes. A model failure or client disconnect saves nothing, so a
thread never holds a question without its answer. Memory context is built
BEFORE anything is saved (7.4's contract), so the question never appears twice.

Known gap (by decision, 7.5): no per-student rate limiting or throttling.
"""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.session import LMSSession
from app.models.user import User
from app.services.auth import require_student
from app.services.student_chat_service import run_student_chat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/student-chat", tags=["student-chat"])


class StudentChatQuestion(BaseModel):
    """Body for POST /student-chat/stream."""
    question: str
    current_session_id: int | None = None

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be empty")
        return value


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post(
    "/stream",
    summary="Ask the course assistant a question and stream the answer via SSE (student only)",
)
def student_chat_stream(
    body: StudentChatQuestion,
    db: Annotated[Session, Depends(get_db)],
    student: Annotated[User, Depends(require_student)],
) -> StreamingResponse:
    if body.current_session_id is not None and db.get(LMSSession, body.current_session_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    def event_stream():
        for event in run_student_chat(db, student.id, body.question, body.current_session_id):
            yield _sse(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
