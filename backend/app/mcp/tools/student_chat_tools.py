"""
Post-7.8 Fix 3 (Prompt 3): student chat MCP tools.

Thin MCP wrapper over Phase 7.5's student Q&A chatbot. No chat logic
lives here — the tool opens a database session, delegates to the same
service functions the streaming REST endpoint uses, and collects the full
response into one return value (MCP calls are request/response, not
streaming). Same register(server) pattern as Phase 4 tools.

Streaming collapse
──────────────────
The REST endpoint streams via SSE (resolved → citations → token... → done),
but an MCP tool call is a single request/response. This tool drains the
entire stream and returns:
  - All accumulated text as ``answer``
  - Session resolution details
  - Citations
  - Thread/message IDs if the answer was persisted

This matches Phase 4's grade_session pattern (drain a generator into one
return value).
"""

import logging

from mcp.server import MCPServer

from app.database import SessionLocal
from app.models.session import LMSSession
from app.models.user import User
from app.services.chat_session_resolver import resolve_session, build_clarification_message
from app.services.chat_memory import get_context_for_prompt, get_or_create_thread, add_message
from app.services.chat_safety import build_scope_safe_prompt
from app.services.embeddings import retrieve
from app.services.llm_provider import call_llm_stream
from app.models.conversation import MessageRole

logger = logging.getLogger(__name__)

# Same constants as routers/student_chat.py
ANSWER_TOP_K = 5
ANSWER_MIN_SIMILARITY = 0.35
STUDENT_CHAT_UNAVAILABLE_MESSAGE = (
    "The course assistant is temporarily unavailable. Please try again in a moment."
)


def ask_course_assistant(
    student_id: int,
    question: str,
    current_session_id: int | None = None,
) -> dict:
    """
    Ask the course assistant a question and return the full response.

    Wraps Phase 7.5's student chat service: session resolution, scoped
    retrieval, scope-safe prompting, and LLM streaming, but collects the
    entire response into one return dict instead of streaming it event by
    event.

    Returns one of:
      {"status": "clarification_needed", "message": str, "candidates": [...]}
        — the question was ambiguous, caller should ask which session
      {"status": "conversational", "answer": str}
        — greeting/small-talk, no retrieval or thread
      {"status": "answered", "answer": str, "session_id": int | None,
       "session_title": str | None, "citations": [...],
       "thread_id": int | None, "user_message_id": int | None,
       "assistant_message_id": int | None}
        — full answer with optional session resolution and thread persistence

    Never raises: errors surface as {"status": "error", "message": str}.
    """
    db = SessionLocal()
    try:
        # Validate student exists
        student = db.get(User, student_id)
        if student is None or student.role != "student":
            return {"status": "error", "message": f"Student {student_id} not found"}

        # Validate current_session_id if provided
        if current_session_id is not None and db.get(LMSSession, current_session_id) is None:
            return {"status": "error", "message": f"Session {current_session_id} not found"}

        # Resolve session
        resolution = resolve_session(db, student_id, question, current_session_id)

        # Greeting / small-talk path
        if resolution.status == "conversational":
            friendly = (
                "Hi! Ask me anything about your course material — "
                "lectures, assignments, or concepts you'd like explained."
            )
            return {"status": "conversational", "answer": friendly}

        # Clarification needed
        if resolution.status != "resolved":
            return {
                "status": "clarification_needed",
                "message": build_clarification_message(resolution.candidates),
                "candidates": resolution.candidates,
            }

        # Resolved path - get context and retrieve
        thread = (
            get_or_create_thread(db, student_id, resolution.session_id)
            if resolution.session_id is not None
            else None
        )
        context = get_context_for_prompt(db, thread) if thread is not None else None

        retrieved = retrieve(
            question,
            session_id=resolution.session_id,
            top_k=ANSWER_TOP_K,
            min_similarity=ANSWER_MIN_SIMILARITY,
        )

        # Build citations
        citations = []
        if retrieved:
            for chunk in retrieved:
                source_type = chunk.get("source_type")
                if source_type == "lecture":
                    from app.models.lecture_file import LectureFile
                    lecture_file = db.get(LectureFile, chunk["source_file_id"])
                    if lecture_file:
                        citations.append({
                            "source_type": "lecture",
                            "filename": lecture_file.original_filename,
                            "session_title": lecture_file.session.title,
                            "location": f"Slide {chunk['slide_number']} ({chunk['source']})",
                            "snippet": chunk["chunk_text"][:200],
                            "similarity": chunk["similarity"],
                        })
                elif source_type == "notebook":
                    from app.models.unsolved_file import UnsolvedFile
                    unsolved_file = db.get(UnsolvedFile, chunk["source_file_id"])
                    if unsolved_file:
                        citations.append({
                            "source_type": "notebook",
                            "filename": unsolved_file.original_filename,
                            "session_title": unsolved_file.session.title,
                            "location": f"Cell {chunk['cell_index']} ({chunk['cell_type']})",
                            "snippet": chunk["chunk_text"][:200],
                            "similarity": chunk["similarity"],
                        })

        # Short-circuit: no material and no history
        has_history = context is not None and (
            context.get("rolling_summary") is not None
            or bool(context.get("recent_messages"))
        )
        if not retrieved and not has_history:
            no_material_answer = (
                "I couldn't find anything about that in your course material. "
                "Try rephrasing, or ask about a specific topic from your lectures or assignments."
            )
            if thread is not None:
                user_message = add_message(db, thread, MessageRole.user, question)
                assistant_message = add_message(db, thread, MessageRole.assistant, no_material_answer)
                return {
                    "status": "answered",
                    "answer": no_material_answer,
                    "session_id": resolution.session_id,
                    "session_title": resolution.session_title,
                    "citations": citations,
                    "thread_id": thread.id,
                    "user_message_id": user_message.id,
                    "assistant_message_id": assistant_message.id,
                }
            return {
                "status": "answered",
                "answer": no_material_answer,
                "session_id": resolution.session_id,
                "session_title": resolution.session_title,
                "citations": citations,
                "thread_id": None,
                "user_message_id": None,
                "assistant_message_id": None,
            }

        # Generate answer
        prompt = build_scope_safe_prompt(retrieved, question, conversation_history=context)
        parts: list[str] = []
        try:
            for text in call_llm_stream(prompt, purpose="student_chat"):
                parts.append(text)
        except Exception as exc:  # noqa: BLE001
            logger.error("MCP ask_course_assistant: generation failed for student=%s: %s", student_id, exc)
            return {"status": "error", "message": STUDENT_CHAT_UNAVAILABLE_MESSAGE}

        answer = "".join(parts)

        # Persist to thread if available
        if thread is not None:
            user_message = add_message(db, thread, MessageRole.user, question)
            assistant_message = add_message(db, thread, MessageRole.assistant, answer)
            return {
                "status": "answered",
                "answer": answer,
                "session_id": resolution.session_id,
                "session_title": resolution.session_title,
                "citations": citations,
                "thread_id": thread.id,
                "user_message_id": user_message.id,
                "assistant_message_id": assistant_message.id,
            }

        return {
            "status": "answered",
            "answer": answer,
            "session_id": resolution.session_id,
            "session_title": resolution.session_title,
            "citations": citations,
            "thread_id": None,
            "user_message_id": None,
            "assistant_message_id": None,
        }

    except Exception as exc:  # noqa: BLE001
        logger.error("MCP ask_course_assistant failed: %s", exc)
        return {"status": "error", "message": str(exc)}
    finally:
        db.close()


def register(server: MCPServer) -> None:
    """Register this module's tools on *server*."""
    server.add_tool(
        ask_course_assistant,
        name="ask_course_assistant",
        description=(
            "Ask the course assistant a question about course material (lectures, "
            "assignments, concepts). Returns the full answer with citations and "
            "session resolution. May return clarification_needed if the question "
            "is ambiguous between multiple sessions. Maintains conversation "
            "history when session_id is provided."
        ),
    )
