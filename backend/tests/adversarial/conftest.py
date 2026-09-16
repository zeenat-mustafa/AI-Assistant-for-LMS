"""
Shared fixtures for driving the REAL student chat endpoint (Phase 7.5) from
the adversarial suites.

Everything below the HTTP layer is real: the router, SSE framing, session
resolver, Chroma retrieval, 7.4 memory (including summarization), the
scope-safe prompt, and streaming production Gemini via call_llm_stream.
Storage is a backup-API scratch copy of backend/lms.db upgraded to head —
never the real lms.db, so fabricated history (false-memory cases) and repeated
runs never land in a real student's threads.

The only interception is observational: call_llm_stream is wrapped to record
the exact prompt it was given (then delegates to the real function unchanged),
so transcripts can show the memory the model actually saw.

Tier confirmation works exactly as before: TestClient runs the app in-process,
so caplog sees llm_provider's "LLM call served by gemini primary" lines —
including from the streaming path, which logs the same line on completion.
"""

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
REAL_DB_PATH = BACKEND_DIR / "lms.db"
ENDPOINT = "/api/v1/student-chat/stream"
DEMO_STUDENT_EMAIL = "student@demo.com"


@pytest.fixture(scope="module")
def endpoint_session_factory(tmp_path_factory):
    if not REAL_DB_PATH.exists():
        pytest.skip("backend/lms.db not present in this environment")
    scratch = tmp_path_factory.mktemp("student_chat_endpoint") / "scratch_lms.db"
    src = sqlite3.connect(str(REAL_DB_PATH))
    dst = sqlite3.connect(str(scratch))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()

    env = dict(os.environ, DATABASE_URL=f"sqlite:///{scratch.as_posix()}")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    engine = create_engine(f"sqlite:///{scratch.as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture(scope="module")
def endpoint_client(endpoint_session_factory):
    from app.database import get_db
    from app.main import app

    def override_get_db():
        db = endpoint_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="module")
def demo_student(endpoint_session_factory):
    """The documented demo student; JWT minted via the app's own function."""
    from app.models.user import User, UserRole
    from app.services.auth import create_access_token

    db = endpoint_session_factory()
    try:
        student_id = db.query(User).filter_by(email=DEMO_STUDENT_EMAIL).one().id
    finally:
        db.close()
    token = create_access_token(student_id, UserRole.student)
    return {"id": student_id, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture()
def reset_threads(endpoint_session_factory, demo_student):
    """Delete the demo student's threads (all, or for given sessions) in the SCRATCH copy."""
    from app.models.conversation import ConversationThread

    def reset(*session_ids):
        db = endpoint_session_factory()
        try:
            query = db.query(ConversationThread).filter_by(student_id=demo_student["id"])
            if session_ids:
                query = query.filter(ConversationThread.lms_session_id.in_(session_ids))
            for thread in query.all():
                db.delete(thread)
            db.commit()
        finally:
            db.close()

    return reset


@pytest.fixture()
def seed_messages(endpoint_session_factory, demo_student):
    """Write messages straight into the demo student's thread (scratch only)."""
    from app.services.chat_memory import add_message, get_or_create_thread

    def seed(session_id, messages):
        db = endpoint_session_factory()
        try:
            thread = get_or_create_thread(db, demo_student["id"], session_id)
            for role, content in messages:
                add_message(db, thread, role, content)
        finally:
            db.close()

    return seed


@pytest.fixture()
def thread_state(endpoint_session_factory, demo_student):
    """Read back (rolling_summary, summarized_through_message_id, [(role, content)])."""
    from app.models.conversation import ConversationMessage, ConversationThread

    def state(session_id):
        db = endpoint_session_factory()
        try:
            thread = db.query(ConversationThread).filter_by(
                student_id=demo_student["id"], lms_session_id=session_id,
            ).one_or_none()
            if thread is None:
                return None
            messages = [
                (m.role.value, m.content)
                for m in db.query(ConversationMessage).filter_by(thread_id=thread.id).order_by(ConversationMessage.id)
            ]
            return {
                "rolling_summary": thread.rolling_summary,
                "summarized_through_message_id": thread.summarized_through_message_id,
                "messages": messages,
            }
        finally:
            db.close()

    return state


@pytest.fixture()
def ask(endpoint_client, demo_student, caplog, monkeypatch):
    """
    ask(question, current_session_id=None) -> turn dict.

    Sends one question through the real endpoint and parses the real SSE
    stream. Paced by ADVERSARIAL_CALL_DELAY_S. Does not assert anything
    itself beyond HTTP 200 — callers decide what must hold.
    """
    from app.routers import student_chat

    real_stream = student_chat.call_llm_stream
    captured: dict = {}

    def recording_stream(prompt, purpose="fast"):
        captured["prompt"] = prompt
        yield from real_stream(prompt, purpose)

    monkeypatch.setattr(student_chat, "call_llm_stream", recording_stream)
    caplog.set_level(logging.INFO, logger="app.services.llm_provider")
    delay = float(os.environ.get("ADVERSARIAL_CALL_DELAY_S", "0"))

    def _ask(question: str, current_session_id: int | None = None) -> dict:
        time.sleep(delay)
        captured.clear()
        start = len(caplog.records)
        body = {"question": question}
        if current_session_id is not None:
            body["current_session_id"] = current_session_id

        events: list[dict] = []
        response = endpoint_client.post(ENDPOINT, json=body, headers=demo_student["headers"])
        assert response.status_code == 200, response.text
        for frame in response.text.split("\n\n"):
            if frame.strip():
                assert frame.startswith("data: "), frame
                events.append(json.loads(frame[len("data: "):]))

        tiers = [
            ("primary" if "gemini primary" in r.message else "fallback", r.message.split("purpose=")[-1])
            for r in caplog.records[start:]
            if "LLM call served by" in r.message
        ]
        by_kind = {e["event"]: e for e in events if e["event"] != "token"}
        prompt = captured.get("prompt")
        memory_block = None
        if prompt and "Conversation So Far" in prompt:
            memory_block = prompt[prompt.index("Conversation So Far"):prompt.index("Retrieved Course Material")].strip()
        return {
            "question": question,
            "current_session_id": current_session_id,
            "kinds": [e["event"] for e in events],
            "events": events,
            "answer": "".join(e["text"] for e in events if e["event"] == "token"),
            "resolved": by_kind.get("resolved"),
            "clarification": by_kind.get("clarification_needed"),
            "citations": (by_kind.get("citations") or {}).get("citations", []),
            "error": by_kind.get("error"),
            "tiers": tiers,
            "prompt": prompt,
            "memory_block": memory_block,
        }

    return _ask


@pytest.fixture(scope="module")
def write_turn_report():
    """write(path, title, entries) — render turn dicts (plus optional notes) as markdown."""

    def write(path: Path, title: str, entries: list[dict]) -> None:
        lines = [f"# {title}\n"]
        for entry in entries:
            lines.append(f"## {entry['label']}\n")
            if "note" in entry:
                lines.append(f"{entry['note']}\n")
            turn = entry.get("turn")
            if turn is None:
                continue
            lines.append(f"**Question:** {turn['question']!r}  \n"
                         f"**current_session_id sent:** {turn['current_session_id']}  \n"
                         f"**Events:** {turn['kinds'][:2]} … {turn['kinds'][-1:]} ({len(turn['kinds'])} total)  \n"
                         f"**Tiers:** {turn['tiers']}\n")
            if turn["resolved"]:
                lines.append(f"**Resolved:** {turn['resolved']}\n")
            if turn["clarification"]:
                lines.append(f"**Clarification:** {turn['clarification']}\n")
            if turn["citations"]:
                cites = [
                    f"{c.get('filename')} "
                    f"({'slide ' + str(c.get('slide_number')) if c.get('source_type') == 'lecture' else 'cell ' + str(c.get('cell_index'))}) "
                    f"sim={c.get('similarity')}"
                    for c in turn["citations"]
                ]
                lines.append(f"**Citations:** {cites}\n")
            if turn["memory_block"]:
                lines.append(f"**Memory the model saw:**\n\n```\n{turn['memory_block']}\n```\n")
            if turn["error"]:
                lines.append(f"**Error event:** {turn['error']}\n")
            lines.append(f"**Answer:**\n\n```\n{turn['answer']}\n```\n")
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nTranscript report written to {path}")

    return write
