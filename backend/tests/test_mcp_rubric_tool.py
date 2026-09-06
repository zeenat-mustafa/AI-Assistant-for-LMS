"""
Tests for backend/app/mcp/tools/rubric_tools.py -- Phase 4, Sub-feature 4.3.

generate_rubric_for_unsolved_file's own logic is covered in
test_rubric.py, so the LLM boundary is mocked here (no real Gemini quota
is spent by the automated suite). These tests confirm the MCP layer: the
tool is registered with the right schema and default, delegates with the
right argument order, and returns 2.3's result dict completely
unaltered — including the force-regenerate "warning" from 2.9.

The parity test is the important one: it drives the SAME input through
both the MCP tool and the REST generate-rubric endpoint and asserts the
two results are identical. If either surface ever reshaped the payload,
an instructor would get different answers depending on which interface
they used.

Cases covered
-------------
1. Tool registered; force defaults to False and is not required.
2. Delegates with db FIRST (this function's order differs from
   session_matcher's) and force passed through.
3. DB session closed, including when the service raises.
4-6. "success" / "warning" / "error" results returned unaltered.
7. Warning key omitted entirely when nothing is stale.
8. MCP output is byte-for-byte identical to the REST endpoint's.
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
from app.mcp.tools.rubric_tools import generate_rubric
from app.models.grade import Grade
from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.submission_file import SubmissionFile
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole
from app.services.auth import create_access_token

_RUBRIC = {"criteria": [{"criterion": "Correctness", "points_possible": 10.0}]}
_SUCCESS = {"success": True, "rubric": _RUBRIC}
_WITH_WARNING = {
    "success": True,
    "rubric": _RUBRIC,
    "warning": "2 existing grade(s) reference the previous rubric version and are now stale.",
}
_FAILURE = {"success": False, "error": "Unsolved assignment file with id 999 not found."}


def _gemini_rubric_response() -> str:
    return json.dumps({
        "criteria": [
            {"criterion": "Loads the dataset", "points_possible": 4.0},
            {"criterion": "Builds the model", "points_possible": 6.0},
        ]
    })


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# 1. Registration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_generate_rubric_registered_with_expected_schema():
    tools = await server.list_tools()
    tool = next((t for t in tools if t.name == "generate_rubric"), None)

    assert tool is not None, "generate_rubric should be registered on the server"
    assert tool.description

    schema = tool.input_schema
    assert schema["properties"]["unsolved_file_id"]["type"] == "integer"
    assert schema["properties"]["force"]["type"] == "boolean"
    # force is optional and defaults to False, matching the service signature.
    assert schema["properties"]["force"]["default"] is False
    assert schema["required"] == ["unsolved_file_id"]


# ---------------------------------------------------------------------------
# 2-3. Delegation and session handling
# ---------------------------------------------------------------------------

def test_delegates_with_db_first_and_force_passed_through():
    fake_db = MagicMock()
    with patch(
        "app.mcp.tools.rubric_tools.SessionLocal", return_value=fake_db
    ), patch(
        "app.mcp.tools.rubric_tools.generate_rubric_for_unsolved_file",
        return_value=_SUCCESS,
    ) as mock_service:
        generate_rubric(8, force=True)

    args, kwargs = mock_service.call_args
    # This service takes db FIRST — unlike match_instruction_to_session.
    assert args[0] is fake_db
    assert args[1] == 8
    assert kwargs["force"] is True
    fake_db.close.assert_called_once()


def test_db_session_closed_even_if_service_raises():
    fake_db = MagicMock()
    with patch(
        "app.mcp.tools.rubric_tools.SessionLocal", return_value=fake_db
    ), patch(
        "app.mcp.tools.rubric_tools.generate_rubric_for_unsolved_file",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError):
            generate_rubric(8)

    fake_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# 4-7. Result passed through unaltered
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expected", [_SUCCESS, _WITH_WARNING, _FAILURE])
def test_returns_service_result_unaltered(expected):
    with patch(
        "app.mcp.tools.rubric_tools.generate_rubric_for_unsolved_file",
        return_value=expected,
    ):
        result = generate_rubric(8, force=True)

    assert result == expected, (
        "the MCP tool must return 2.3's dict as-is — reshaping it here would "
        "let the MCP and REST interfaces drift apart"
    )


def test_warning_key_absent_when_nothing_is_stale():
    """2.9 omits "warning" entirely (not empty) when no grades go stale."""
    with patch(
        "app.mcp.tools.rubric_tools.generate_rubric_for_unsolved_file",
        return_value=_SUCCESS,
    ):
        result = generate_rubric(8)

    assert "warning" not in result


# ---------------------------------------------------------------------------
# 8. Parity with the REST endpoint
# ---------------------------------------------------------------------------

@pytest.fixture()
def parity_env(monkeypatch):
    """
    One in-memory DB shared by both surfaces, seeded with an assignment
    file that already has a rubric AND a grade against it — so a
    force=True call exercises the staleness-warning path on both.
    """
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    db = TestingSession()

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
            rubric_json=json.dumps({"criteria": [{"criterion": "Old", "points_possible": 10.0}]}),
        ),
        Submission(id=200, session_id=10, student_id=2,
                   original_filename="hw1.ipynb",
                   uploaded_file_path="10/submissions/2/hw1.ipynb"),
        SubmissionFile(id=300, submission_id=200, matched_unsolved_file_id=100,
                       original_filename="hw1.ipynb",
                       extracted_ipynb_path="10/submissions/2/hw1.ipynb",
                       graded=True),
    ])
    db.commit()
    db.add(Grade(submission_file_id=300, score=8.0, feedback_text="ok",
                 rationale_json=None))
    db.commit()

    # Mock only the LLM + notebook-reading boundaries; the rubric service
    # itself runs for real on both surfaces.
    monkeypatch.setattr(
        "app.services.rubric.extract_notebook_structure",
        lambda _path: {
            "valid": True,
            "cells": [{"type": "markdown", "content": "Do it.", "heuristic_hint": False}],
        },
    )
    monkeypatch.setattr(
        "app.services.rubric.call_gemini_for_rubric",
        lambda _prompt: _gemini_rubric_response(),
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


def test_mcp_output_matches_rest_endpoint_exactly(parity_env):
    db, client, token = parity_env

    # REST surface, force=True so the staleness warning is exercised.
    rest = client.post(
        "/api/v1/sessions/10/assignments/100/generate-rubric?force=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rest.status_code == 200
    rest_body = rest.json()

    # MCP surface, same input, same DB.
    with patch("app.mcp.tools.rubric_tools.SessionLocal", return_value=db):
        mcp_body = generate_rubric(100, force=True)

    assert mcp_body == rest_body, (
        "MCP and REST must return identical payloads for the same input; "
        f"MCP={mcp_body} REST={rest_body}"
    )
    # And the case that matters most is actually covered here.
    assert "warning" in rest_body
    assert rest_body["warning"] == (
        "1 existing grade(s) reference the previous rubric version and are now stale."
    )
