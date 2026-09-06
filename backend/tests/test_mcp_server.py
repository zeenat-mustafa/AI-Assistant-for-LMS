"""
Tests for backend/app/mcp/server.py -- Phase 4, Sub-feature 4.1.

Scaffold-level only: the server instantiates, the one `ping` tool is
registered, and it is callable through the server's real dispatch path
(not just by calling the underlying Python function). The grading tools
land in 4.2-4.5 and get their own tests.

Async tests use the anyio pytest plugin, which ships with anyio (already
a transitive dependency via FastAPI/mcp) — no new test dependency.

Cases covered
-------------
1. Server instantiates with the expected name/version.
2. `ping` is registered, and is the ONLY tool for now.
3. `ping` takes no arguments (empty input schema).
4. `ping` returns the expected string via server.call_tool dispatch.
5. `ping` returns the same string when called directly as a function.
6. The MCP process resolves the same config/database the REST app uses.
"""

import pytest

from app.mcp.server import describe_backing_services, ping, server

_EXPECTED_PONG = "pong - AI Assistant for LMS MCP server is running"


@pytest.fixture
def anyio_backend():
    """Run anyio tests on asyncio only (trio isn't a project dependency)."""
    return "asyncio"


# ---------------------------------------------------------------------------
# 1. Instantiation
# ---------------------------------------------------------------------------

def test_server_instantiates_with_expected_identity():
    assert server.name == "ai-assistant-for-lms"
    assert server.version == "0.1.0"


# ---------------------------------------------------------------------------
# 2-3. Tool registration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ping_is_registered_and_is_the_only_tool():
    tools = await server.list_tools()
    names = [t.name for t in tools]
    assert names == ["ping"], (
        "4.1 registers exactly one scaffold tool; the real grading tools "
        f"arrive in 4.2-4.5. Found: {names}"
    )


@pytest.mark.anyio
async def test_ping_takes_no_arguments():
    tools = await server.list_tools()
    ping_tool = next(t for t in tools if t.name == "ping")
    assert ping_tool.description
    assert ping_tool.input_schema.get("properties") == {}


# ---------------------------------------------------------------------------
# 4-5. Invocation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ping_callable_through_server_dispatch():
    """Goes through the same path a real MCP client's call_tool request takes."""
    result = await server.call_tool("ping", {})

    assert result.is_error is False
    texts = [block.text for block in result.content if block.type == "text"]
    assert _EXPECTED_PONG in texts


def test_ping_function_returns_expected_string():
    assert ping() == _EXPECTED_PONG


# ---------------------------------------------------------------------------
# 6. Backing services
# ---------------------------------------------------------------------------

def test_mcp_process_resolves_the_same_config_and_database():
    """
    The scaffold's real point: confirm app.config/app.database import and
    resolve inside the MCP server's process, so 4.2+ tools can open
    sessions the same way the FastAPI routers do. Nothing is queried.
    """
    info = describe_backing_services()

    assert info["database_url"]
    assert info["session_factory"] == "sessionmaker"
    # Same database the REST app talks to, not a second one.
    assert "lms.db" in info["engine_url"]
