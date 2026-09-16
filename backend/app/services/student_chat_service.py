"""
Core orchestration for the student course-assistant chat — Phase 7.8 audit
fix (Item 1).

Extracted out of app/routers/student_chat.py so that ONE place decides the
resolution/retrieval/reply branching, instead of two (the REST router and
the MCP tool each reimplementing it). Before this fix, app/mcp/tools/
student_chat_tools.py's ask_course_assistant had its own copy of this
branching that never checked resolution.resolution == "conversational" (the
uniformly-low-similarity chitchat path) — only resolution.status — so a
casual low-similarity message fell through to full retrieval and prompt
construction in the MCP tool instead of the REST endpoint's clean bypass.
Both callers now import run_student_chat and build_citations from here;
neither reimplements any part of the branching.

run_student_chat is a generator yielding the same event-dict vocabulary the
REST endpoint has always framed as SSE (`{"event": ..., ...}`):
    conversational bypass (no session/thread):  token, done
    clarification_needed  (terminal, no thread touched)
    resolved → [citations] → token... → done
    resolved → error   (LLM failure — clean, generic message)

The REST router (app/routers/student_chat.py) turns each yielded dict into
an SSE frame verbatim. The MCP tool (app/mcp/tools/student_chat_tools.py)
drains the generator and collects it into one response dict — same pattern
already used for grade_session_batch (Phase 4) — using the presence/absence
of a "resolved" event to tell a real answer from a conversational bypass,
which is exactly what the SSE wire format itself communicates to any
consumer, frontend included.
"""

import logging
from typing import Iterator

from sqlalchemy.orm import Session

from app.models.conversation import MessageRole
from app.models.lecture_file import LectureFile
from app.models.unsolved_file import UnsolvedFile
from app.services.chat_memory import add_message, get_context_for_prompt, get_or_create_thread
from app.services.chat_safety import build_scope_safe_prompt
from app.services.chat_session_resolver import build_clarification_message, resolve_session
from app.services.embeddings import retrieve
from app.services.llm_provider import call_llm_stream

logger = logging.getLogger(__name__)

ANSWER_TOP_K = 5
ANSWER_MIN_SIMILARITY = 0.35
SNIPPET_CHARS = 200

# Genuinely new, safe message — never a truncation of a raw provider error.
STUDENT_CHAT_UNAVAILABLE_MESSAGE = (
    "The course assistant is temporarily unavailable — it couldn't reach the AI "
    "service. Please try again in a few minutes."
)

CONVERSATIONAL_GREETING = (
    "Hi! Ask me anything about your course material — "
    "lectures, assignments, or concepts you'd like explained."
)

NO_MATERIAL_ANSWER = (
    "I couldn't find anything about that in your course material. "
    "Try rephrasing, or ask about a specific topic from your lectures or assignments."
)


def build_citations(db: Session, retrieved: list[dict]) -> list[dict]:
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


def run_student_chat(
    db: Session,
    student_id: int,
    question: str,
    current_session_id: int | None,
) -> Iterator[dict]:
    """
    Resolve *question* to a session (or not) and yield the reply as a
    sequence of event dicts. The single source of truth for every branch:
    greeting bypass, uniformly-low-similarity chitchat bypass, clarification,
    no-material short-circuit, and the full retrieval-augmented answer.

    Caller contract: *current_session_id*, if not None, must already be
    confirmed to exist (both callers do this themselves, since the right
    failure shape differs — an HTTP 404 for the REST router, an error dict
    for the MCP tool).
    """
    resolution = resolve_session(db, student_id, question, current_session_id)

    # ── Greeting / small-talk bypass (fast path, no Chroma call at all) ──────
    if resolution.status == "conversational":
        yield {"event": "token", "text": CONVERSATIONAL_GREETING}
        yield {"event": "done", "thread_id": None,
               "user_message_id": None, "assistant_message_id": None}
        return

    if resolution.status != "resolved":
        yield {
            "event": "clarification_needed",
            "message": build_clarification_message(resolution.candidates),
            "candidates": resolution.candidates,
        }
        return

    # ── resolution.status == "resolved" from here down ────────────────────
    # resolution.resolution can be:
    #   "current_session" / "redirected" / "broad_search" — real course Q
    #   "conversational" — uniformly low similarity, not a course question

    if resolution.resolution == "conversational":
        # No resolved/citations events — the question isn't about course
        # content. Straight to the LLM for a normal reply, no retrieval, no
        # thread — under the same scope-safety rules as every other reply.
        prompt = build_scope_safe_prompt([], question, conversation_history=None)
        parts: list[str] = []
        try:
            for text in call_llm_stream(prompt, purpose="student_chat"):
                parts.append(text)
                yield {"event": "token", "text": text}
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "student_chat: generation failed for conversational question student=%s: %s",
                student_id, exc,
            )
            yield {"event": "error", "message": STUDENT_CHAT_UNAVAILABLE_MESSAGE}
            return
        yield {"event": "done", "thread_id": None,
               "user_message_id": None, "assistant_message_id": None}
        return

    # ── Real course-content question from here down ───────────────────────
    yield {
        "event": "resolved",
        "session_id": resolution.session_id,
        "session_title": resolution.session_title,
        "resolution": resolution.resolution,
    }

    thread = get_or_create_thread(db, student_id, resolution.session_id) if resolution.session_id is not None else None
    context = get_context_for_prompt(db, thread) if thread is not None else None
    # session_id=None here triggers cross-session retrieval for general/
    # multi-session topics (resolution.session_id is None when broad_search
    # resolved without a clear single winner).
    retrieved = retrieve(
        question, session_id=resolution.session_id,
        top_k=ANSWER_TOP_K, min_similarity=ANSWER_MIN_SIMILARITY,
    )

    # Only emit citations when real material was retrieved — suppressed on
    # the no-material path (handled below) and implicitly on the paths above
    # that already returned.
    if retrieved:
        yield {"event": "citations", "citations": build_citations(db, retrieved)}

    # ── Short-circuit: nothing retrieved above the similarity threshold ──
    # Only skip to the canned message when there is also no conversation
    # history. If history exists, proceed to the full LLM call — the model
    # can answer follow-ups using context alone, even with weak retrieval.
    has_history = context is not None and (
        context.get("rolling_summary") is not None
        or bool(context.get("recent_messages"))
    )
    if not retrieved and not has_history:
        yield {"event": "token", "text": NO_MATERIAL_ANSWER}
        if thread is not None:
            user_message = add_message(db, thread, MessageRole.user, question)
            assistant_message = add_message(db, thread, MessageRole.assistant, NO_MATERIAL_ANSWER)
            yield {
                "event": "done",
                "thread_id": thread.id,
                "user_message_id": user_message.id,
                "assistant_message_id": assistant_message.id,
            }
        else:
            yield {"event": "done", "thread_id": None,
                   "user_message_id": None, "assistant_message_id": None}
        return

    prompt = build_scope_safe_prompt(retrieved, question, conversation_history=context)

    parts: list[str] = []
    try:
        for text in call_llm_stream(prompt, purpose="student_chat"):
            parts.append(text)
            yield {"event": "token", "text": text}
    except Exception as exc:  # noqa: BLE001 — never surface raw provider errors
        logger.error(
            "student_chat: generation failed for student=%s session=%s: %s",
            student_id, resolution.session_id, exc,
        )
        yield {"event": "error", "message": STUDENT_CHAT_UNAVAILABLE_MESSAGE}
        return

    if thread is not None:
        user_message = add_message(db, thread, MessageRole.user, question)
        assistant_message = add_message(db, thread, MessageRole.assistant, "".join(parts))
        yield {
            "event": "done",
            "thread_id": thread.id,
            "user_message_id": user_message.id,
            "assistant_message_id": assistant_message.id,
        }
    else:
        yield {"event": "done", "thread_id": None,
               "user_message_id": None, "assistant_message_id": None}
