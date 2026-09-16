"""
Post-7.8 Fix 3 (Prompt 3): student chat MCP tools.

Thin MCP wrapper over Phase 7.5's student Q&A chatbot. No chat logic lives
here — the tool opens a database session and drains
app.services.student_chat_service.run_student_chat, the SAME generator the
REST endpoint (app/routers/student_chat.py) turns into SSE frames. Same
register(server) pattern as Phase 4 tools.

Phase 7.8 audit fix (Item 1): this tool used to reimplement the REST
endpoint's resolution/retrieval/reply branching itself, and that copy never
checked resolution.resolution == "conversational" (the uniformly-low-
similarity chitchat path) — only resolution.status — so a casual message
like "how are you" fell through into full retrieval and prompt construction
here, when the REST endpoint correctly bypasses it into a plain conversational
reply. Delegating to the shared generator makes that class of drift
structurally impossible: there is now exactly one place that decides the
branching, and both callers just consume its output.

Streaming collapse
──────────────────
The REST endpoint streams via SSE (resolved → citations → token... → done),
but an MCP tool call is a single request/response. This tool drains the
entire stream and collects it into one dict — the same "drain a generator
into one return value" pattern Phase 4's grade_session tool established.

Telling a conversational bypass from a real answer, from the event stream
alone: a "resolved" event only appears on a real course-content question
(current_session/redirected/broad_search). Its absence — whether the
question was a plain greeting or uniformly-low-similarity chitchat — is
exactly what the SSE wire format itself communicates to any consumer
(frontend included), so the collector uses the same signal rather than
re-deciding anything.
"""

import logging

from mcp.server import MCPServer

from app.database import SessionLocal
from app.models.session import LMSSession
from app.models.user import User
from app.services.student_chat_service import run_student_chat

logger = logging.getLogger(__name__)


def _collect_student_chat_result(events: list[dict]) -> dict:
    """Turn run_student_chat's yielded event stream into one response dict."""
    if events and events[0]["event"] == "clarification_needed":
        first = events[0]
        return {
            "status": "clarification_needed",
            "message": first["message"],
            "candidates": first["candidates"],
        }

    error_event = next((e for e in events if e["event"] == "error"), None)
    if error_event is not None:
        return {"status": "error", "message": error_event["message"]}

    resolved = next((e for e in events if e["event"] == "resolved"), None)
    citations_event = next((e for e in events if e["event"] == "citations"), None)
    done_event = next((e for e in events if e["event"] == "done"), None)
    answer = "".join(e["text"] for e in events if e["event"] == "token")

    if resolved is None:
        # No resolved event at all -> the conversational bypass (a plain
        # greeting, or uniformly-low-similarity chitchat) -- no session, no
        # retrieval, no thread, exactly as the SSE wire format communicates.
        return {"status": "conversational", "answer": answer}

    return {
        "status": "answered",
        "answer": answer,
        "session_id": resolved["session_id"],
        "session_title": resolved["session_title"],
        "citations": citations_event["citations"] if citations_event else [],
        "thread_id": done_event["thread_id"] if done_event else None,
        "user_message_id": done_event["user_message_id"] if done_event else None,
        "assistant_message_id": done_event["assistant_message_id"] if done_event else None,
    }


def ask_course_assistant(
    student_id: int,
    question: str,
    current_session_id: int | None = None,
) -> dict:
    """
    Ask the course assistant a question and return the full response.

    Wraps Phase 7.5's student chat service (via the shared
    student_chat_service.run_student_chat generator): session resolution,
    scoped retrieval, scope-safe prompting, and LLM streaming, collecting
    the entire response into one return dict instead of streaming it event
    by event.

    Returns one of:
      {"status": "clarification_needed", "message": str, "candidates": [...]}
        — the question was ambiguous, caller should ask which session
      {"status": "conversational", "answer": str}
        — greeting or uniformly-low-similarity chitchat, no retrieval or thread
      {"status": "answered", "answer": str, "session_id": int | None,
       "session_title": str | None, "citations": [...],
       "thread_id": int | None, "user_message_id": int | None,
       "assistant_message_id": int | None}
        — full answer with optional session resolution and thread persistence

    Never raises: errors surface as {"status": "error", "message": str}.
    """
    db = SessionLocal()
    try:
        student = db.get(User, student_id)
        if student is None or student.role != "student":
            return {"status": "error", "message": f"Student {student_id} not found"}

        if current_session_id is not None and db.get(LMSSession, current_session_id) is None:
            return {"status": "error", "message": f"Session {current_session_id} not found"}

        events = list(run_student_chat(db, student_id, question, current_session_id))
        return _collect_student_chat_result(events)

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
