"""
Tests for backend/app/mcp/tools/session_tools.py -- Phase 4, Sub-feature 4.2.

match_instruction_to_session's own matching logic is already covered in
test_session_matcher.py, so it is mocked at the boundary here. These tests
confirm the MCP layer specifically: the tool is registered with the right
schema, it delegates to the real function with the right arguments and a
real DB session, and it returns 3.1's result dict completely unaltered.

That last point is the one worth guarding: if the MCP tool ever reshaped
or wrapped the result, the MCP interface and the REST /chat endpoint would
silently diverge in how they resolve the same instruction.

Cases covered
-------------
1. Tool registered, with typed required arguments in its schema.
2. Delegates to match_instruction_to_session with the caller's arguments.
3. Passes a real DB session, and closes it afterwards.
4-6. Returns "matched" / "ambiguous" / "no_match" dicts byte-for-byte.
7. A DB session is closed even when the matcher raises.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.mcp.server import server
from app.mcp.tools.session_tools import match_session

_MATCHED = {
    "status": "matched",
    "session_id": 3,
    "session_title": "Week 2 Day 1",
    "confidence": 1.0,
}
_AMBIGUOUS = {
    "status": "ambiguous",
    "candidates": [
        {"session_id": 1, "session_title": "Week 1 Day 1", "confidence": 0.61},
        {"session_id": 2, "session_title": "Week 1 Day 2", "confidence": 0.61},
    ],
}
_NO_MATCH = {"status": "no_match"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# 1. Registration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_match_session_registered_with_expected_schema():
    tools = await server.list_tools()
    tool = next((t for t in tools if t.name == "match_session"), None)

    assert tool is not None, "match_session should be registered on the server"
    assert tool.description

    schema = tool.input_schema
    assert schema["properties"]["instruction"]["type"] == "string"
    assert schema["properties"]["instructor_id"]["type"] == "integer"
    assert set(schema["required"]) == {"instruction", "instructor_id"}


# ---------------------------------------------------------------------------
# 2-3. Delegation
# ---------------------------------------------------------------------------

def test_delegates_to_session_matcher_with_caller_arguments():
    with patch(
        "app.mcp.tools.session_tools.match_instruction_to_session",
        return_value=_MATCHED,
    ) as mock_match:
        match_session("grade week 2 day 1", 1)

    mock_match.assert_called_once()
    args, _kwargs = mock_match.call_args
    assert args[0] == "grade week 2 day 1"
    assert args[1] == 1
    assert args[2] is not None, "a DB session should be passed through"


def test_opens_and_closes_a_db_session():
    fake_db = MagicMock()
    with patch(
        "app.mcp.tools.session_tools.SessionLocal", return_value=fake_db
    ), patch(
        "app.mcp.tools.session_tools.match_instruction_to_session",
        return_value=_MATCHED,
    ) as mock_match:
        match_session("grade week 2 day 1", 1)

    assert mock_match.call_args[0][2] is fake_db
    fake_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# 4-6. Result shape passed through unaltered
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expected", [_MATCHED, _AMBIGUOUS, _NO_MATCH])
def test_returns_matcher_result_unaltered(expected):
    with patch(
        "app.mcp.tools.session_tools.match_instruction_to_session",
        return_value=expected,
    ):
        result = match_session("some instruction", 1)

    assert result == expected, (
        "the MCP tool must return 3.1's dict as-is — reshaping it here would "
        "let the MCP and REST interfaces drift apart"
    )


# ---------------------------------------------------------------------------
# 7. Cleanup on failure
# ---------------------------------------------------------------------------

def test_db_session_closed_even_if_matcher_raises():
    fake_db = MagicMock()
    with patch(
        "app.mcp.tools.session_tools.SessionLocal", return_value=fake_db
    ), patch(
        "app.mcp.tools.session_tools.match_instruction_to_session",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError):
            match_session("some instruction", 1)

    fake_db.close.assert_called_once()
