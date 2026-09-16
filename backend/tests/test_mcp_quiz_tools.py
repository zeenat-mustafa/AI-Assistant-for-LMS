"""
Tests for backend/app/mcp/tools/quiz_tools.py -- Post-7.8 Fix 3 (Prompt 3);
tests added for the Phase 7.8 audit fix (Item 2).

Unlike lecture_tools.py, both quiz tools genuinely delegate to the real
service layer (quiz_generator.generate_quiz / submit_quiz_attempt), so the
usual Phase 4 delegation-mock convention applies directly, plus a real-DB
parity test against the REST endpoints with the same mocked LLM boundary.

Cases covered
-------------
1-2. Both tools registered with the expected schema.
3. generate_quiz delegates to quiz_generator.generate_quiz with a real DB
   session, and formats the result without leaking correct_option_index.
4-6. generate_quiz's three QuizGenerator exceptions map to the documented
   error_type values, never raising.
7. submit_quiz delegates to quiz_generator.submit_quiz_attempt and formats
   the scored result, including correct_option_index (allowed post-submit).
8. submit_quiz enforces per-attempt ownership (403-equivalent error dict).
9. submit_quiz's already-submitted case maps to its documented error_type.
10. DB sessions are closed, including on failure, for both tools.
11. Parity: generate_quiz + submit_quiz vs REST /quiz/generate + /quiz/{id}/submit
    on identically-seeded databases with the same mocked LLM call, for the
    "session" scope type.
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
from app.mcp.tools.quiz_tools import generate_quiz, submit_quiz
from app.models.quiz_attempt import QuizAttempt
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole
from app.services import quiz_generator
from app.services.auth import create_access_token

_QUESTIONS = [
    {"question": f"Q{i}?", "options": ["A", "B", "C", "D"], "correct_option_index": i % 4,
     "source_citation": "Week 1 Day 1 · lec.pptx · slide 1"}
    for i in range(5)
]


def _fresh_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _make_attempt(student_id=2, submitted=False):
    attempt = MagicMock(spec=QuizAttempt)
    attempt.id = 41
    attempt.student_id = student_id
    attempt.scope_type = "session"
    attempt.scope_detail = json.dumps({"session_id": 1})
    attempt.questions_json = json.dumps(_QUESTIONS)
    attempt.max_score = 5
    attempt.score = 3
    attempt.created_at.isoformat.return_value = "2026-09-16T10:00:00"
    if submitted:
        attempt.student_answers_json = json.dumps([0, 1, 2, 3, 0])
        attempt.submitted_at.isoformat.return_value = "2026-09-16T10:05:00"
    return attempt


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# 1-2. Registration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_generate_quiz_registered_with_expected_schema():
    tools = await server.list_tools()
    tool = next((t for t in tools if t.name == "generate_quiz"), None)

    assert tool is not None
    assert tool.description
    schema = tool.input_schema
    assert schema["properties"]["student_id"]["type"] == "integer"
    assert schema["properties"]["scope_type"]["type"] == "string"
    assert schema["properties"]["scope_detail"]["type"] == "object"
    assert set(schema["required"]) == {"student_id", "scope_type", "scope_detail"}


@pytest.mark.anyio
async def test_submit_quiz_registered_with_expected_schema():
    tools = await server.list_tools()
    tool = next((t for t in tools if t.name == "submit_quiz"), None)

    assert tool is not None
    assert tool.description
    schema = tool.input_schema
    assert schema["properties"]["attempt_id"]["type"] == "integer"
    assert schema["properties"]["student_id"]["type"] == "integer"
    assert schema["properties"]["answers"]["type"] == "array"
    assert set(schema["required"]) == {"attempt_id", "student_id", "answers"}


# ---------------------------------------------------------------------------
# 3. generate_quiz delegation + no answer leak
# ---------------------------------------------------------------------------

def test_generate_quiz_delegates_and_never_leaks_correct_option_index():
    fake_db = MagicMock()
    fake_db.get.return_value = User(id=2, role=UserRole.student)
    attempt = _make_attempt()

    with patch("app.mcp.tools.quiz_tools.SessionLocal", return_value=fake_db), \
         patch.object(quiz_generator, "generate_quiz", return_value=attempt) as mock_gen:
        result = generate_quiz(2, "session", {"session_id": 1})

    args, _ = mock_gen.call_args
    assert args[0] is fake_db
    assert args[1] == 2
    assert args[2] == "session"
    assert args[3] == {"session_id": 1}

    assert result["id"] == 41
    assert len(result["questions"]) == 5
    for q in result["questions"]:
        assert set(q.keys()) == {"question", "options", "source_citation"}
    fake_db.close.assert_called_once()


def test_generate_quiz_rejects_unknown_student():
    fake_db = MagicMock()
    fake_db.get.return_value = None
    with patch("app.mcp.tools.quiz_tools.SessionLocal", return_value=fake_db):
        result = generate_quiz(999, "session", {"session_id": 1})
    assert result == {"error": "Student 999 not found", "error_type": "not_found"}
    fake_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# 4-6. Error mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exc_cls,error_type", [
    (quiz_generator.QuizScopeNotFoundError, "not_found"),
    (quiz_generator.QuizMaterialError, "invalid_material"),
    (quiz_generator.QuizGenerationError, "generation_failed"),
])
def test_generate_quiz_maps_each_service_exception(exc_cls, error_type):
    fake_db = MagicMock()
    fake_db.get.return_value = User(id=2, role=UserRole.student)
    with patch("app.mcp.tools.quiz_tools.SessionLocal", return_value=fake_db), \
         patch.object(quiz_generator, "generate_quiz", side_effect=exc_cls("boom")):
        result = generate_quiz(2, "session", {"session_id": 1})
    assert result == {"error": "boom", "error_type": error_type}
    fake_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# 7. submit_quiz delegation + scored shape
# ---------------------------------------------------------------------------

def test_submit_quiz_delegates_and_returns_scored_result():
    fake_db = MagicMock()
    fake_db.get.side_effect = [User(id=2, role=UserRole.student), _make_attempt(submitted=True)]

    with patch("app.mcp.tools.quiz_tools.SessionLocal", return_value=fake_db), \
         patch.object(quiz_generator, "submit_quiz_attempt") as mock_submit:
        result = submit_quiz(41, 2, [0, 1, 2, 3, 0])

    mock_submit.assert_called_once()
    args, _ = mock_submit.call_args
    assert args[0] is fake_db
    assert args[2] == [0, 1, 2, 3, 0]

    assert result["attempt_id"] == 41
    assert result["score"] == 3
    assert len(result["questions"]) == 5
    assert all("correct_option_index" in q for q in result["questions"])
    fake_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# 8-9. Ownership + already-submitted
# ---------------------------------------------------------------------------

def test_submit_quiz_rejects_another_students_attempt():
    fake_db = MagicMock()
    fake_db.get.side_effect = [User(id=2, role=UserRole.student), _make_attempt(student_id=99)]
    with patch("app.mcp.tools.quiz_tools.SessionLocal", return_value=fake_db):
        result = submit_quiz(41, 2, [0, 0, 0, 0, 0])
    assert result == {
        "error": "You can only submit your own quiz attempts", "error_type": "forbidden",
    }


def test_submit_quiz_maps_already_submitted():
    fake_db = MagicMock()
    fake_db.get.side_effect = [User(id=2, role=UserRole.student), _make_attempt()]
    with patch("app.mcp.tools.quiz_tools.SessionLocal", return_value=fake_db), \
         patch.object(quiz_generator, "submit_quiz_attempt",
                      side_effect=quiz_generator.QuizAlreadySubmittedError()):
        result = submit_quiz(41, 2, [0, 0, 0, 0, 0])
    assert result["error_type"] == "already_submitted"


# ---------------------------------------------------------------------------
# 10. Cleanup on failure
# ---------------------------------------------------------------------------

def test_db_sessions_closed_even_if_service_raises():
    fake_db = MagicMock()
    fake_db.get.return_value = User(id=2, role=UserRole.student)
    with patch("app.mcp.tools.quiz_tools.SessionLocal", return_value=fake_db), \
         patch.object(quiz_generator, "generate_quiz", side_effect=RuntimeError("boom")):
        result = generate_quiz(2, "session", {"session_id": 1})
    assert result["error_type"] == "generation_failed"
    fake_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# 11. Parity with REST /quiz/generate + /quiz/{id}/submit
# ---------------------------------------------------------------------------

def _seed(db):
    db.add_all([
        User(id=1, name="Prof", email="prof@x.com", hashed_password="h", role=UserRole.instructor),
        User(id=2, name="Alice", email="alice@x.com", hashed_password="h", role=UserRole.student),
        LMSSession(id=1, title="Week 1 Day 1", instructor_id=1),
        UnsolvedFile(
            id=10, session_id=1, original_filename="lab.ipynb",
            file_path="1/assignments/lab.ipynb",
            parsed_requirements_text="# Do the thing\nprint('hello')",
        ),
    ])
    db.commit()


def test_generate_and_submit_quiz_matches_rest_endpoints(monkeypatch):
    """Same mocked-LLM-boundary parity pattern as test_mcp_grading_tools.py's
    grade_session parity test: identically-seeded fresh databases, one real
    LLM call mocked identically for both surfaces."""
    import app.models  # noqa: F401

    raw_llm_response = json.dumps({
        "questions": [
            {
                "question": f"Question {i}?",
                "options": ["A", "B", "C", "D"],
                "correct_option_index": 0,
                "source_id": "S1",
            }
            for i in range(5)
        ],
    })
    monkeypatch.setattr("app.services.llm_provider.call_llm", lambda *a, **kw: raw_llm_response)
    # Both surfaces call the SAME quiz_generator.generate_quiz, which reads
    # notebook material straight from disk -- mock that one shared read
    # point instead of writing a real file, matching the grading-tools
    # parity precedent of mocking the LLM/extraction boundary, not disk I/O.
    fake_structure = {
        "valid": True,
        "cells": [
            {"type": "markdown", "content": "# Lab\n" + ("Explain the concept. " * 40), "heuristic_hint": False},
            {"type": "code", "content": "print('hello world')\n" * 20, "heuristic_hint": False},
        ],
    }
    monkeypatch.setattr("app.services.quiz_generator.extract_notebook_structure", lambda *a, **kw: fake_structure)
    # Options are shuffled with an unseeded rng.Random() per call, so two
    # independent generate_quiz calls would land correct_option_index in
    # different slots even given identical material -- disable the shuffle
    # for this test so [0,0,0,0,0] is comparably "all correct" on both sides.
    monkeypatch.setattr("app.services.quiz_generator.shuffle_question_options", lambda questions, rng: questions)

    mcp_engine, mcp_db = _fresh_db()
    rest_engine, rest_db = _fresh_db()
    try:
        _seed(mcp_db)
        _seed(rest_db)

        with patch("app.mcp.tools.quiz_tools.SessionLocal", return_value=mcp_db):
            mcp_gen = generate_quiz(2, "session", {"session_id": 1})
        assert "error" not in mcp_gen, mcp_gen

        from app.database import get_db
        from app.main import app

        def override_get_db():
            yield rest_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            client = TestClient(app)
            token = create_access_token(2, UserRole.student)
            rest_gen_resp = client.post(
                "/api/v1/quiz/generate",
                json={"scope_type": "session", "session_id": 1},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert rest_gen_resp.status_code == 201, rest_gen_resp.text
            rest_gen = rest_gen_resp.json()

            # Neither surface's generate response may carry the answer.
            assert "correct_option_index" not in json.dumps(mcp_gen)
            assert "correct_option_index" not in json.dumps(rest_gen)
            assert len(mcp_gen["questions"]) == len(rest_gen["questions"]) == 5

            with patch("app.mcp.tools.quiz_tools.SessionLocal", return_value=mcp_db):
                mcp_result = submit_quiz(mcp_gen["id"], 2, [0, 0, 0, 0, 0])

            rest_submit_resp = client.post(
                f"/api/v1/quiz/{rest_gen['id']}/submit",
                json={"answers": [0, 0, 0, 0, 0]},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert rest_submit_resp.status_code == 200, rest_submit_resp.text
            rest_result = rest_submit_resp.json()
        finally:
            app.dependency_overrides.clear()

        assert mcp_result["score"] == rest_result["score"]
        assert mcp_result["max_score"] == rest_result["max_score"] == 5
        assert [q["is_correct"] for q in mcp_result["questions"]] == [q["is_correct"] for q in rest_result["questions"]]
    finally:
        mcp_db.close()
        rest_db.close()
        Base.metadata.drop_all(mcp_engine)
        Base.metadata.drop_all(rest_engine)
