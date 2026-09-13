"""
Phase 7, Sub-feature 7.5: student Q&A chatbot — POST /student-chat/stream.

The first live caller of 7.1-7.4 together: resolves which session a question
belongs to (chat_session_resolver), uses that session's 7.4 memory thread,
retrieves that session's material (7.2), assembles the 7.3 scope-safe prompt
(unchanged — no new framing is added near SCOPE_SAFETY_RULE), and streams the
answer via llm_provider.call_llm_stream.

A new router rather than an extension of chat.py: /chat is the
instructor-only grading chat; this is student-only Q&A.

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
from app.models.conversation import MessageRole
from app.models.lecture_file import LectureFile
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User
from app.services.auth import require_student
from app.services.chat_memory import add_message, get_context_for_prompt, get_or_create_thread
from app.services.chat_safety import build_scope_safe_prompt
from app.services.chat_session_resolver import build_clarification_message, resolve_session
from app.services.embeddings import retrieve
from app.services.llm_provider import call_llm_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/student-chat", tags=["student-chat"])

ANSWER_TOP_K = 5
ANSWER_MIN_SIMILARITY = 0.35
SNIPPET_CHARS = 200

# Genuinely new, safe message — never a truncation of a raw provider error.
STUDENT_CHAT_UNAVAILABLE_MESSAGE = (
    "The course assistant is temporarily unavailable — it couldn't reach the AI "
    "service. Please try again in a few minutes."
)


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


def _build_citations(db: Session, retrieved: list[dict]) -> list[dict]:
    """Per retrieved chunk: where it came from (file, slide/cell), how similar."""
    lecture_ids = {c["source_file_id"] for c in retrieved if c.get("source_type") == "lecture"}
    notebook_ids = {c["source_file_id"] for c in retrieved if c.get("source_type") == "notebook"}
    lecture_names = dict(
        db.query(LectureFile.id, LectureFile.original_filename).filter(LectureFile.id.in_(lecture_ids)).all()
    ) if lecture_ids else {}
    notebook_names = dict(
        db.query(UnsolvedFile.id, UnsolvedFile.original_filename).filter(UnsolvedFile.id.in_(notebook_ids)).all()
    ) if notebook_ids else {}

    citations = []
    for chunk in retrieved:
        source_type = chunk.get("source_type")
        citation = {
            "source_type": source_type,
            "source_file_id": chunk.get("source_file_id"),
            "session_id": chunk.get("session_id"),
            "similarity": chunk.get("similarity"),
            "snippet": (chunk.get("chunk_text") or "")[:SNIPPET_CHARS],
        }
        if source_type == "lecture":
            citation["filename"] = lecture_names.get(chunk.get("source_file_id"))
            citation["slide_number"] = chunk.get("slide_number")
            citation["source"] = chunk.get("source")
        elif source_type == "notebook":
            citation["filename"] = notebook_names.get(chunk.get("source_file_id"))
            citation["cell_index"] = chunk.get("cell_index")
            citation["cell_type"] = chunk.get("cell_type")
        citations.append(citation)
    return citations


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
        resolution = resolve_session(db, student.id, body.question, body.current_session_id)

        if resolution.status != "resolved":
            yield _sse({
                "event": "clarification_needed",
                "message": build_clarification_message(resolution.candidates),
                "candidates": resolution.candidates,
            })
            return

        yield _sse({
            "event": "resolved",
            "session_id": resolution.session_id,
            "session_title": resolution.session_title,
            "resolution": resolution.resolution,
        })

        thread = get_or_create_thread(db, student.id, resolution.session_id)
        context = get_context_for_prompt(db, thread)
        retrieved = retrieve(
            body.question, session_id=resolution.session_id,
            top_k=ANSWER_TOP_K, min_similarity=ANSWER_MIN_SIMILARITY,
        )
        yield _sse({"event": "citations", "citations": _build_citations(db, retrieved)})

        prompt = build_scope_safe_prompt(retrieved, body.question, conversation_history=context)

        parts: list[str] = []
        try:
            for text in call_llm_stream(prompt, purpose="student_chat"):
                parts.append(text)
                yield _sse({"event": "token", "text": text})
        except Exception as exc:  # noqa: BLE001 — never surface raw provider errors
            logger.error(
                "student_chat: generation failed for student=%s session=%s: %s",
                student.id, resolution.session_id, exc,
            )
            yield _sse({"event": "error", "message": STUDENT_CHAT_UNAVAILABLE_MESSAGE})
            return

        user_message = add_message(db, thread, MessageRole.user, body.question)
        assistant_message = add_message(db, thread, MessageRole.assistant, "".join(parts))
        yield _sse({
            "event": "done",
            "thread_id": thread.id,
            "user_message_id": user_message.id,
            "assistant_message_id": assistant_message.id,
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")
