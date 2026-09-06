"""
Tests for backend/app/mcp/tools/evaluation_tools.py -- Phase 4, Sub-feature 4.4.

evaluate_submission_file's own logic is covered in test_evaluator.py, so
the LLM boundary is mocked here and the automated suite spends zero
Gemini quota. These tests confirm the MCP layer: correct schema, correct
argument order, and 2.4's result dict returned completely unaltered.

A note on the parity test — unlike 4.3, there is NO REST route that
returns raw evaluation output. REST reaches evaluation only through the
grading path, which transforms the result into a persisted Grade
(score + feedback_text + rationale_json). So byte-for-byte payload
parity is not the right assertion here. What must hold instead is that
both surfaces expose the SAME underlying evaluation: the MCP tool's
total_score and criteria must equal the score and rationale_json the
REST grading path persists for the same submission file. That is the
invariant an instructor would notice breaking.

Cases covered
-------------
1. Tool registered; submission_file_id is the only argument.
2. Delegates with db FIRST (order differs between services).
3. DB session closed, including when the service raises.
4-5. "success" and "error" results returned unaltered.
6. Unmatched submission returns 2.4's error without an LLM call.
7. Parity: MCP evaluation == the evaluation REST persists as a Grade.
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
from app.mcp.tools.evaluation_tools import evaluate_submission
from app.models.grade import Grade
from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.submission_file import SubmissionFile
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole
from app.services.auth import create_access_token

_CRITERIA = [
    {
        "criterion": "Loads the dataset",
        "points_possible": 4.0,
        "points_awarded": 3.5,
        "explanation": "Loaded correctly but no validation.",
    },
    {
        "criterion": "Builds the model",
        "points_possible": 6.0,
        "points_awarded": 5.0,
        "explanation": "Model trains and reports accuracy.",
    },
]
_SUCCESS = {"success": True, "total_score": 8.5, "criteria": _CRITERIA}
_UNMATCHED = {"success": False, "error": "not matched to an assignment"}


def _gemini_evaluation_response() -> str:
    return json.dumps({"criteria": _CRITERIA})


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# 1. Registration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_evaluate_submission_registered_with_expected_schema():
    tools = await server.list_tools()
    tool = next((t for t in tools if t.name == "evaluate_submission"), None)

    assert tool is not None, "evaluate_submission should be registered"
    assert tool.description

    schema = tool.input_schema
    assert schema["properties"]["submission_file_id"]["type"] == "integer"
    assert schema["required"] == ["submission_file_id"]
    # 2.4 takes no other parameters — no force/rubric id.
    assert set(schema["properties"]) == {"submission_file_id"}


# ---------------------------------------------------------------------------
# 2-3. Delegation and session handling
# ---------------------------------------------------------------------------

def test_delegates_with_db_first():
    fake_db = MagicMock()
    with patch(
        "app.mcp.tools.evaluation_tools.SessionLocal", return_value=fake_db
    ), patch(
        "app.mcp.tools.evaluation_tools.evaluate_submission_file",
        return_value=_SUCCESS,
    ) as mock_service:
        evaluate_submission(300)

    args, _kwargs = mock_service.call_args
    # db FIRST, then the id — same order as rubric's service, unlike
    # match_instruction_to_session which takes db last.
    assert args[0] is fake_db
    assert args[1] == 300
    fake_db.close.assert_called_once()


def test_db_session_closed_even_if_service_raises():
    fake_db = MagicMock()
    with patch(
        "app.mcp.tools.evaluation_tools.SessionLocal", return_value=fake_db
    ), patch(
        "app.mcp.tools.evaluation_tools.evaluate_submission_file",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError):
            evaluate_submission(300)

    fake_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# 4-5. Result passed through unaltered
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expected", [_SUCCESS, _UNMATCHED])
def test_returns_service_result_unaltered(expected):
    with patch(
        "app.mcp.tools.evaluation_tools.evaluate_submission_file",
        return_value=expected,
    ):
        result = evaluate_submission(300)

    assert result == expected, (
        "the MCP tool must return 2.4's dict as-is — reshaping it here would "
        "let the MCP and REST interfaces drift apart"
    )


# ---------------------------------------------------------------------------
# 6-7. Against a real (in-memory) database
# ---------------------------------------------------------------------------

@pytest.fixture()
def parity_env(monkeypatch):
    """
    One in-memory DB shared by both surfaces: an assignment with a cached
    rubric, plus a matched, ungraded submission file. Only the LLM and
    notebook-reading boundaries are mocked; evaluator, feedback and the
    grading pipeline all run for real.
    """
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

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
        # Unmatched file for the no-LLM error path.
        SubmissionFile(id=301, submission_id=200, matched_unsolved_file_id=None,
                       original_filename="mystery.ipynb",
                       extracted_ipynb_path="10/submissions/2/mystery.ipynb",
                       graded=False),
    ])
    db.commit()

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
        lambda _prompt: _gemini_evaluation_response(),
    )

    from app.database import get_db
    from app.main import app

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    token = create_access_token(1, UserRole.instructor)

    yield db, client, token

    app.dependency_overrides.clear()
    db.close()
    Base.metadata.drop_all(engine)


def test_unmatched_submission_returns_error_without_llm_call(parity_env):
    db, _client, _token = parity_env
    with patch("app.mcp.tools.evaluation_tools.SessionLocal", return_value=db), \
         patch("app.services.evaluator.call_gemini_for_evaluation") as mock_llm:
        result = evaluate_submission(301)

    assert result == {"success": False, "error": "not matched to an assignment"}
    mock_llm.assert_not_called()


def test_mcp_evaluation_matches_what_rest_grading_persists(parity_env):
    """
    REST has no raw-evaluation endpoint, so parity is asserted against the
    evaluation REST actually persists: the Grade's score and rationale_json
    must equal the MCP tool's total_score and criteria for the same file.
    """
    db, client, token = parity_env

    # MCP surface (read-only — writes no Grade).
    with patch("app.mcp.tools.evaluation_tools.SessionLocal", return_value=db):
        mcp_result = evaluate_submission(300)

    assert mcp_result["success"] is True
    assert db.query(Grade).count() == 0, "evaluation must not persist a Grade"

    # REST surface: the grading path, which runs the same evaluation and
    # persists it.
    rest = client.post(
        "/api/v1/sessions/10/submissions/files/300/grade",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rest.status_code == 200, rest.text
    assert rest.json()["success"] is True

    grade = db.query(Grade).filter(Grade.submission_file_id == 300).one()
    persisted_criteria = json.loads(grade.rationale_json)

    assert mcp_result["total_score"] == grade.score, (
        "MCP and REST must surface the same score for the same submission"
    )
    assert mcp_result["criteria"] == persisted_criteria, (
        "MCP's criteria must match the rationale REST stores on the Grade"
    )
