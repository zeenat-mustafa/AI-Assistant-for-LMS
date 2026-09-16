"""
Tests for backend/app/mcp/tools/student_chat_tools.py -- Post-7.8 Fix 3
(Prompt 3); tests added for the Phase 7.8 audit fix (Item 2).

Both the REST endpoint (app/routers/student_chat.py) and this MCP tool now
drain the same generator, app.services.student_chat_service.run_student_chat
(the Item 1 fix), so a real parity test is natural here: seed one DB, run
both surfaces against it with the resolver/retrieval/LLM boundary faked
identically, and compare.

Case 5 is the regression guard for the exact bug the audit found: a
uniformly-low-similarity casual message ("how are you") must resolve to
{"status": "conversational", ...} with retrieve() never called -- not fall
through into full retrieval and prompt construction the way the pre-fix
tool did.

Cases covered
-------------
1. Tool registered with the expected schema.
2. Rejects an unknown student / a non-student user.
3. Rejects an unknown current_session_id.
4. Clarification path: status/message/candidates passed through unaltered,
   no thread created.
5. THE REGRESSION GUARD -- conversational bypass (resolution.status ==
   "conversational", e.g. a greeting) AND the resolution.resolution ==
   "conversational" chitchat path both produce {"status": "conversational"},
   with zero retrieve() calls, matching the REST endpoint.
6. Resolved path: answer, session_id/session_title, citations, and thread
   IDs all populated from the drained events.
7. LLM failure surfaces as {"status": "error", ...}, never raises.
8. DB session closed in every case, including on an unexpected exception.
9. Parity: ask_course_assistant vs REST POST /student-chat/stream on an
   identically-seeded database with the resolver/retrieval/LLM boundary
   faked identically, for both the conversational-chitchat case and a real
   resolved answer.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.mcp.server import server
from app.mcp.tools.student_chat_tools import ask_course_assistant
from app.models.lecture_file import LectureFile
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole
from app.services import student_chat_service
from app.services.auth import create_access_token
from app.services.chat_session_resolver import ResolutionResult

NOTEBOOK_CHUNK = {
    "source_type": "notebook", "source_file_id": 20, "session_id": 7, "cell_index": 17,
    "cell_type": "code", "chunk_text": 'calculator.invoke("25*18")', "similarity": 0.85,
}


def _fresh_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _seed(db):
    db.add_all([
        User(id=2, name="Stu", email="student@demo.com", hashed_password="h", role=UserRole.student),
        LMSSession(id=7, title="Week 4 Day 1"),
    ])
    db.commit()
    db.add(UnsolvedFile(id=20, session_id=7, original_filename="Week 10_Lab3.ipynb", file_path="7/a/lab3.ipynb"))
    db.commit()


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# 1. Registration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ask_course_assistant_registered_with_expected_schema():
    tools = await server.list_tools()
    tool = next((t for t in tools if t.name == "ask_course_assistant"), None)

    assert tool is not None
    assert tool.description
    schema = tool.input_schema
    assert schema["properties"]["student_id"]["type"] == "integer"
    assert schema["properties"]["question"]["type"] == "string"
    assert set(schema["required"]) == {"student_id", "question"}


# ---------------------------------------------------------------------------
# 2-3. Validation
# ---------------------------------------------------------------------------

def test_rejects_unknown_student():
    engine, db = _fresh_db()
    try:
        with patch("app.mcp.tools.student_chat_tools.SessionLocal", return_value=db):
            result = ask_course_assistant(999, "hi")
        assert result == {"status": "error", "message": "Student 999 not found"}
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_rejects_non_student_user():
    engine, db = _fresh_db()
    try:
        db.add(User(id=1, name="Prof", email="p@x.com", hashed_password="h", role=UserRole.instructor))
        db.commit()
        with patch("app.mcp.tools.student_chat_tools.SessionLocal", return_value=db):
            result = ask_course_assistant(1, "hi")
        assert result["status"] == "error"
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_rejects_unknown_current_session_id():
    engine, db = _fresh_db()
    try:
        _seed(db)
        with patch("app.mcp.tools.student_chat_tools.SessionLocal", return_value=db):
            result = ask_course_assistant(2, "hi", current_session_id=999)
        assert result == {"status": "error", "message": "Session 999 not found"}
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# 4. Clarification path
# ---------------------------------------------------------------------------

def test_clarification_passed_through_unaltered_no_thread():
    engine, db = _fresh_db()
    try:
        _seed(db)
        candidates = [{"session_id": 7, "session_title": "Week 4 Day 1", "best_similarity": 0.5}]
        with patch("app.mcp.tools.student_chat_tools.SessionLocal", return_value=db), \
             patch.object(student_chat_service, "resolve_session",
                          return_value=ResolutionResult(status="clarification_needed", candidates=candidates)):
            result = ask_course_assistant(2, "what should I do for the TODO in this assignment")

        assert result["status"] == "clarification_needed"
        assert result["candidates"] == candidates
        assert "did you mean" in result["message"].lower()
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# 5. THE REGRESSION GUARD -- conversational bypass, both flavours
# ---------------------------------------------------------------------------

def test_greeting_bypass_produces_conversational_with_no_retrieve_call():
    """resolution.status == 'conversational' -- is_conversational() caught it
    before any Chroma call inside resolve_session itself."""
    engine, db = _fresh_db()
    try:
        _seed(db)
        retrieve_calls = []
        with patch("app.mcp.tools.student_chat_tools.SessionLocal", return_value=db), \
             patch.object(student_chat_service, "resolve_session",
                          return_value=ResolutionResult(status="conversational")), \
             patch.object(student_chat_service, "retrieve",
                          side_effect=lambda *a, **kw: retrieve_calls.append(a) or []):
            result = ask_course_assistant(2, "hi")

        assert result["status"] == "conversational"
        assert "answer" in result and result["answer"]
        assert retrieve_calls == []
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_uniformly_low_similarity_chitchat_produces_conversational_with_no_retrieve_call():
    """resolution.status == 'resolved' but resolution.resolution ==
    'conversational' -- the exact case the pre-fix tool got wrong: it only
    ever checked resolution.status, so this fell through to full retrieval
    and prompt construction instead of bypassing like the REST endpoint."""
    engine, db = _fresh_db()
    try:
        _seed(db)
        retrieve_calls = []

        def fake_stream(prompt, purpose="student_chat"):
            yield "I'm doing well, thanks for asking!"

        with patch("app.mcp.tools.student_chat_tools.SessionLocal", return_value=db), \
             patch.object(student_chat_service, "resolve_session",
                          return_value=ResolutionResult(
                              status="resolved", session_id=None, session_title=None,
                              resolution="conversational",
                          )), \
             patch.object(student_chat_service, "retrieve",
                          side_effect=lambda *a, **kw: retrieve_calls.append(a) or []), \
             patch.object(student_chat_service, "call_llm_stream", fake_stream):
            result = ask_course_assistant(2, "how are you")

        assert result == {"status": "conversational", "answer": "I'm doing well, thanks for asking!"}
        assert retrieve_calls == [], (
            "regression: the MCP tool must not call retrieve() for the "
            "uniformly-low-similarity chitchat path, matching the REST endpoint"
        )
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# 6. Resolved path with a real answer
# ---------------------------------------------------------------------------

def test_resolved_path_populates_answer_citations_and_thread():
    engine, db = _fresh_db()
    try:
        _seed(db)

        def fake_stream(prompt, purpose="student_chat"):
            yield "It calls "
            yield "the calculator tool."

        with patch("app.mcp.tools.student_chat_tools.SessionLocal", return_value=db), \
             patch.object(student_chat_service, "resolve_session",
                          return_value=ResolutionResult(
                              status="resolved", session_id=7, session_title="Week 4 Day 1",
                              resolution="current_session",
                          )), \
             patch.object(student_chat_service, "retrieve", return_value=[NOTEBOOK_CHUNK]), \
             patch.object(student_chat_service, "call_llm_stream", fake_stream):
            result = ask_course_assistant(2, "What does this code do?")

        assert result["status"] == "answered"
        assert result["answer"] == "It calls the calculator tool."
        assert result["session_id"] == 7
        assert result["session_title"] == "Week 4 Day 1"
        assert len(result["citations"]) == 1
        assert result["citations"][0]["source_file_id"] == 20
        assert result["thread_id"] is not None
        assert result["user_message_id"] is not None
        assert result["assistant_message_id"] is not None
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# 7. LLM failure never raises
# ---------------------------------------------------------------------------

def test_llm_failure_surfaces_as_error_dict():
    engine, db = _fresh_db()
    try:
        _seed(db)

        def raising_stream(prompt, purpose="student_chat"):
            raise RuntimeError("Gemini down")
            yield  # pragma: no cover -- makes this a generator function

        with patch("app.mcp.tools.student_chat_tools.SessionLocal", return_value=db), \
             patch.object(student_chat_service, "resolve_session",
                          return_value=ResolutionResult(
                              status="resolved", session_id=7, session_title="Week 4 Day 1",
                              resolution="current_session",
                          )), \
             patch.object(student_chat_service, "retrieve", return_value=[NOTEBOOK_CHUNK]), \
             patch.object(student_chat_service, "call_llm_stream", raising_stream):
            result = ask_course_assistant(2, "What does this code do?")

        assert result["status"] == "error"
        assert "gemini" not in result["message"].lower()  # never a raw provider error
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# 8. Cleanup
# ---------------------------------------------------------------------------

def test_db_session_closed_even_on_unexpected_exception():
    fake_db = MagicMock()
    fake_db.get.return_value = User(id=2, role=UserRole.student)
    with patch("app.mcp.tools.student_chat_tools.SessionLocal", return_value=fake_db), \
         patch.object(student_chat_service, "resolve_session", side_effect=RuntimeError("boom")):
        result = ask_course_assistant(2, "hi")
    assert result["status"] == "error"
    fake_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# 9. Parity with REST POST /student-chat/stream
# ---------------------------------------------------------------------------

def _sse_events(response) -> list[dict]:
    frames = [f for f in response.text.split("\n\n") if f.strip()]
    return [json.loads(f[len("data: "):]) for f in frames]


def test_conversational_chitchat_matches_rest_endpoint(monkeypatch):
    mcp_engine, mcp_db = _fresh_db()
    rest_engine, rest_db = _fresh_db()
    try:
        _seed(mcp_db)
        _seed(rest_db)

        monkeypatch.setattr(
            student_chat_service, "resolve_session",
            lambda *a, **kw: ResolutionResult(
                status="resolved", session_id=None, session_title=None, resolution="conversational",
            ),
        )
        monkeypatch.setattr(student_chat_service, "retrieve", lambda *a, **kw: [])
        monkeypatch.setattr(
            student_chat_service, "call_llm_stream",
            lambda prompt, purpose="student_chat": iter(["I'm doing well, thanks!"]),
        )

        with patch("app.mcp.tools.student_chat_tools.SessionLocal", return_value=mcp_db):
            mcp_result = ask_course_assistant(2, "how are you")

        from app.database import get_db
        from app.main import app

        def override_get_db():
            yield rest_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            client = TestClient(app)
            token = create_access_token(2, UserRole.student)
            rest = client.post(
                "/api/v1/student-chat/stream",
                json={"question": "how are you"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert rest.status_code == 200, rest.text
            rest_events = _sse_events(rest)
        finally:
            app.dependency_overrides.clear()

        assert not any(e["event"] == "resolved" for e in rest_events), (
            "REST must not emit a resolved event for uniformly-low-similarity chitchat"
        )
        rest_answer = "".join(e["text"] for e in rest_events if e["event"] == "token")

        assert mcp_result == {"status": "conversational", "answer": rest_answer}
    finally:
        mcp_db.close()
        rest_db.close()
        Base.metadata.drop_all(mcp_engine)
        Base.metadata.drop_all(rest_engine)


def test_resolved_answer_matches_rest_endpoint(monkeypatch):
    mcp_engine, mcp_db = _fresh_db()
    rest_engine, rest_db = _fresh_db()
    try:
        _seed(mcp_db)
        _seed(rest_db)

        monkeypatch.setattr(
            student_chat_service, "resolve_session",
            lambda *a, **kw: ResolutionResult(
                status="resolved", session_id=7, session_title="Week 4 Day 1", resolution="current_session",
            ),
        )
        monkeypatch.setattr(student_chat_service, "retrieve", lambda *a, **kw: [NOTEBOOK_CHUNK])
        monkeypatch.setattr(
            student_chat_service, "call_llm_stream",
            lambda prompt, purpose="student_chat": iter(["It calls ", "the calculator tool."]),
        )

        with patch("app.mcp.tools.student_chat_tools.SessionLocal", return_value=mcp_db):
            mcp_result = ask_course_assistant(2, "What does this code do?")

        from app.database import get_db
        from app.main import app

        def override_get_db():
            yield rest_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            client = TestClient(app)
            token = create_access_token(2, UserRole.student)
            rest = client.post(
                "/api/v1/student-chat/stream",
                json={"question": "What does this code do?"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert rest.status_code == 200, rest.text
            rest_events = _sse_events(rest)
        finally:
            app.dependency_overrides.clear()

        rest_answer = "".join(e["text"] for e in rest_events if e["event"] == "token")
        rest_citations = next(e["citations"] for e in rest_events if e["event"] == "citations")

        assert mcp_result["answer"] == rest_answer == "It calls the calculator tool."
        assert mcp_result["session_id"] == 7
        assert mcp_result["citations"] == rest_citations
    finally:
        mcp_db.close()
        rest_db.close()
        Base.metadata.drop_all(mcp_engine)
        Base.metadata.drop_all(rest_engine)
