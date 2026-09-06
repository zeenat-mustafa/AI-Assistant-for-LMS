"""
Tests for backend/app/mcp/tools/grading_tools.py -- Phase 4, Sub-feature 4.5.

The pipeline's own logic is covered in test_grading_pipeline.py, so the
LLM boundary is mocked here and the automated suite spends zero Gemini
quota. These tests confirm the MCP layer: schemas (including the
optional student_id), argument order, results returned unaltered, and
crucially that grade_session DRAINS the generator rather than consuming
it partially.

Parity: unlike 4.4, byte-for-byte comparison IS natural here, because
REST's POST /sessions/{id}/grade returns the same {"events", "summary"}
shape grade_session does. The wrinkle is that grading is stateful — the
second surface to run would find nothing left ungraded — so the parity
test seeds TWO identically-populated fresh databases and runs one
surface against each with a deterministic mocked LLM.

Cases covered
-------------
1-2. Both tools registered; student_id optional and nullable.
3-4. Delegation with db FIRST; student_id passed through.
5. The generator is fully drained, not partially consumed.
6. summary is the final event, and echoed at the top level.
7. Empty batch returns a zero-count summary.
8. DB sessions are closed, including on failure.
9. Results returned unaltered.
10. Byte-for-byte parity with REST's batch endpoint.
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
from app.mcp.tools.grading_tools import grade_session, grade_submission_file
from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.submission_file import SubmissionFile
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole
from app.services.auth import create_access_token

_SINGLE_RESULT = {
    "submission_file_id": 300,
    "student_id": 2,
    "filename": "hw1.ipynb",
    "success": True,
    "score": 8.5,
}

_CRITERIA = [
    {"criterion": "Loads the dataset", "points_possible": 4.0,
     "points_awarded": 3.5, "explanation": "Loaded."},
    {"criterion": "Builds the model", "points_possible": 6.0,
     "points_awarded": 5.0, "explanation": "Trains."},
]


def _batch_events():
    return [
        {"event": "checking", "student_id": 2, "filename": "hw1.ipynb"},
        {"event": "graded", "student_id": 2, "filename": "hw1.ipynb", "score": 8.5},
        {"event": "summary", "total": 1, "graded": 1, "failed": 0, "failures": []},
    ]


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# 1-2. Registration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_grade_submission_file_registered_with_expected_schema():
    tools = await server.list_tools()
    tool = next((t for t in tools if t.name == "grade_submission_file"), None)

    assert tool is not None
    assert tool.description
    schema = tool.input_schema
    assert schema["properties"]["submission_file_id"]["type"] == "integer"
    assert schema["required"] == ["submission_file_id"]


@pytest.mark.anyio
async def test_grade_session_registered_with_optional_student_id():
    tools = await server.list_tools()
    tool = next((t for t in tools if t.name == "grade_session"), None)

    assert tool is not None
    assert tool.description
    schema = tool.input_schema
    assert schema["properties"]["session_id"]["type"] == "integer"
    # student_id is optional and nullable, defaulting to None.
    assert schema["required"] == ["session_id"]
    assert schema["properties"]["student_id"]["default"] is None
    types = {opt.get("type") for opt in schema["properties"]["student_id"]["anyOf"]}
    assert types == {"integer", "null"}


# ---------------------------------------------------------------------------
# 3-4. Delegation
# ---------------------------------------------------------------------------

def test_grade_submission_file_delegates_with_db_first():
    fake_db = MagicMock()
    with patch(
        "app.mcp.tools.grading_tools.SessionLocal", return_value=fake_db
    ), patch(
        "app.mcp.tools.grading_tools.grade_single_submission_file",
        return_value=_SINGLE_RESULT,
    ) as mock_service:
        result = grade_submission_file(300)

    args, _kwargs = mock_service.call_args
    assert args[0] is fake_db
    assert args[1] == 300
    assert result == _SINGLE_RESULT
    fake_db.close.assert_called_once()


@pytest.mark.parametrize("student_id", [None, 7])
def test_grade_session_passes_student_id_through(student_id):
    fake_db = MagicMock()
    with patch(
        "app.mcp.tools.grading_tools.SessionLocal", return_value=fake_db
    ), patch(
        "app.mcp.tools.grading_tools.grade_session_batch",
        return_value=iter(_batch_events()),
    ) as mock_batch:
        grade_session(10, student_id=student_id)

    args, kwargs = mock_batch.call_args
    assert args[0] is fake_db
    assert args[1] == 10
    assert kwargs["student_id"] == student_id
    fake_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# 5-7. Draining behaviour
# ---------------------------------------------------------------------------

def test_generator_is_fully_drained_not_partially_consumed():
    """
    The whole point of the drain decision: every yielded event must reach
    the caller, and the generator must run to exhaustion (so any cleanup
    after its final yield actually executes).
    """
    exhausted = []

    def fake_batch(db, session_id, student_id=None):
        for event in _batch_events():
            yield event
        exhausted.append(True)  # only reached if the generator is drained

    with patch("app.mcp.tools.grading_tools.SessionLocal", return_value=MagicMock()), \
         patch("app.mcp.tools.grading_tools.grade_session_batch", fake_batch):
        result = grade_session(10)

    assert exhausted == [True], "generator was not run to exhaustion"
    assert result["events"] == _batch_events()
    assert [e["event"] for e in result["events"]] == ["checking", "graded", "summary"]


def test_summary_is_final_event_and_echoed_at_top_level():
    with patch("app.mcp.tools.grading_tools.SessionLocal", return_value=MagicMock()), \
         patch("app.mcp.tools.grading_tools.grade_session_batch",
               return_value=iter(_batch_events())):
        result = grade_session(10)

    assert result["summary"] == result["events"][-1]
    assert result["summary"]["event"] == "summary"


def test_empty_batch_returns_zero_count_summary():
    """A pipeline yielding nothing at all still gets a well-formed summary."""
    with patch("app.mcp.tools.grading_tools.SessionLocal", return_value=MagicMock()), \
         patch("app.mcp.tools.grading_tools.grade_session_batch", return_value=iter([])):
        result = grade_session(10)

    assert result["events"] == []
    assert result["summary"] == {
        "event": "summary", "total": 0, "graded": 0, "failed": 0, "failures": [],
    }


# ---------------------------------------------------------------------------
# 8. Cleanup on failure
# ---------------------------------------------------------------------------

def test_db_sessions_closed_even_if_pipeline_raises():
    fake_db = MagicMock()
    with patch("app.mcp.tools.grading_tools.SessionLocal", return_value=fake_db), \
         patch("app.mcp.tools.grading_tools.grade_single_submission_file",
               side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            grade_submission_file(300)
    fake_db.close.assert_called_once()

    fake_db2 = MagicMock()
    with patch("app.mcp.tools.grading_tools.SessionLocal", return_value=fake_db2), \
         patch("app.mcp.tools.grading_tools.grade_session_batch",
               side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            grade_session(10)
    fake_db2.close.assert_called_once()


# ---------------------------------------------------------------------------
# 9. Failure result passed through unaltered
# ---------------------------------------------------------------------------

def test_single_file_failure_returned_unaltered():
    failure = {
        "submission_file_id": 301, "student_id": 2, "filename": "mystery.ipynb",
        "success": False, "error": "not matched to an assignment",
    }
    with patch("app.mcp.tools.grading_tools.SessionLocal", return_value=MagicMock()), \
         patch("app.mcp.tools.grading_tools.grade_single_submission_file",
               return_value=failure):
        assert grade_submission_file(301) == failure


# ---------------------------------------------------------------------------
# 10. Parity with REST's batch endpoint
# ---------------------------------------------------------------------------

def _seed(db):
    db.add_all([
        User(id=1, name="Prof", email="prof@x.com", hashed_password="h",
             role=UserRole.instructor),
        User(id=2, name="Alice", email="alice@x.com", hashed_password="h",
             role=UserRole.student),
        LMSSession(id=10, title="DS101", instructor_id=1),
        UnsolvedFile(
            id=100, session_id=10, original_filename="hw1.ipynb",
            file_path="10/assignments/hw1.ipynb",
            parsed_requirements_text="Do the thing.",
            rubric_json=json.dumps({"criteria": [
                {"criterion": "Loads the dataset", "points_possible": 4.0},
                {"criterion": "Builds the model", "points_possible": 6.0},
            ]}),
        ),
        Submission(id=200, session_id=10, student_id=2,
                   original_filename="hw1.ipynb",
                   uploaded_file_path="10/submissions/2/hw1.ipynb"),
        SubmissionFile(id=300, submission_id=200, matched_unsolved_file_id=100,
                       original_filename="hw1.ipynb",
                       extracted_ipynb_path="10/submissions/2/hw1.ipynb",
                       graded=False),
    ])
    db.commit()


def _fresh_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    _seed(db)
    return engine, db


def test_grade_session_matches_rest_batch_endpoint_exactly(monkeypatch):
    """
    Grading is stateful — whichever surface runs second would find nothing
    ungraded — so each surface gets its own identically-seeded database
    and a deterministic mocked LLM, making the payloads directly
    comparable.
    """
    import app.models  # noqa: F401

    mock_nb = {"valid": True, "code_cells": [{"source": "x = 1", "outputs": []}]}
    mock_structure = {
        "valid": True,
        "cells": [{"type": "code", "content": "x = 1", "heuristic_hint": False}],
    }
    monkeypatch.setattr("app.services.evaluator.parse_notebook_file", lambda _p: mock_nb)
    monkeypatch.setattr(
        "app.services.evaluator.extract_notebook_structure", lambda _p: mock_structure
    )
    monkeypatch.setattr(
        "app.services.evaluator.call_gemini_for_evaluation",
        lambda _prompt: json.dumps({"criteria": _CRITERIA}),
    )

    mcp_engine, mcp_db = _fresh_db()
    rest_engine, rest_db = _fresh_db()

    try:
        with patch("app.mcp.tools.grading_tools.SessionLocal", return_value=mcp_db):
            mcp_body = grade_session(10)

        from app.database import get_db
        from app.main import app

        def override_get_db():
            yield rest_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            client = TestClient(app)
            token = create_access_token(1, UserRole.instructor)
            rest = client.post(
                "/api/v1/sessions/10/grade",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert rest.status_code == 200, rest.text
            rest_body = rest.json()
        finally:
            app.dependency_overrides.clear()

        assert mcp_body == rest_body, (
            "MCP grade_session and REST POST /sessions/{id}/grade must return "
            f"identical payloads.\nMCP={mcp_body}\nREST={rest_body}"
        )
        # And it actually did the work, rather than both being empty.
        assert mcp_body["summary"]["total"] == 1
        assert mcp_body["summary"]["graded"] == 1
    finally:
        mcp_db.close()
        rest_db.close()
        Base.metadata.drop_all(mcp_engine)
        Base.metadata.drop_all(rest_engine)
