"""
Phase 4, Sub-feature 4.1: MCP server scaffold.

Exposes this project over the Model Context Protocol so an MCP client
(Claude Desktop, an IDE, or the SDK's own client) can drive grading
directly, alongside — not instead of — the existing FastAPI REST API.
server.py itself stays a thin registry: it builds the MCPServer, keeps the
`ping` diagnostic, and asks each domain tool module under app/mcp/tools/
to register itself. The real grading tools land there — session matching
in 4.2, rubric/evaluation/grading in 4.3-4.5.

Running it
──────────
    cd backend
    python -m app.mcp

That starts the server on stdio and blocks, which is correct: an MCP
stdio server has no port and no banner — it waits for a client to speak
JSON-RPC on its stdin. Use an MCP client to talk to it (see
tests/test_mcp_server.py and the README's "MCP Server" section).

Transport choice
────────────────
stdio, the standard transport for a locally-run MCP server launched as a
subprocess by its client — which is how this project's server is meant
to be used (an instructor's MCP client spawns it on their machine). The
SDK also offers "sse" and "streamable-http" via the same
``server.run(transport=...)`` call, so if a remote/hosted MCP endpoint is
ever needed it is a one-line change here, not a redesign. The Phase 5
Next.js frontend does NOT need that — it talks to the REST API.

SDK note
────────
Written against mcp 2.x, where the high-level server class is
``MCPServer`` (it was called ``FastMCP`` in 1.x).
"""

import logging

from mcp.server import MCPServer

# Imported to confirm the MCP server process can reach the same settings and
# database engine the REST app uses — 4.2+ tools will open sessions with
# SessionLocal exactly as the FastAPI routers do via get_db(). Nothing is
# queried here; this scaffold only establishes the pattern.
from app.config import settings
from app.database import SessionLocal, engine
from app.mcp.tools import session_tools

logger = logging.getLogger(__name__)

server = MCPServer(
    name="ai-assistant-for-lms",
    version="0.1.0",
    instructions=(
        "Instructor-directed AI grading assistant for Jupyter notebook "
        "assignments. Resolve an instruction to a session with match_session "
        "first; rubric, evaluation and grading tools land in Phase 4.3-4.5."
    ),
)

# Domain tool modules register themselves here (see app/mcp/tools/).
session_tools.register(server)


@server.tool(
    name="ping",
    description=(
        "Connectivity check. Returns a confirmation string proving the MCP "
        "server is running and reachable. Takes no arguments."
    ),
)
def ping() -> str:
    """
    Trivial liveness tool proving the scaffold works end to end.

    Deliberately temporary: it is NOT part of the real tool set and should
    be removed once 4.2+ tools exist, unless it turns out to be useful for
    client-side connection diagnostics.
    """
    logger.info("MCP ping received")
    return "pong - AI Assistant for LMS MCP server is running"


def describe_backing_services() -> dict[str, str]:
    """
    Report what this process resolved for config/DB, without querying.

    Used by the scaffold's tests (and useful when debugging a client that
    launches this server as a subprocess, where the working directory —
    and therefore the SQLite path — is easy to get wrong).
    """
    return {
        "database_url": settings.database_url,
        "engine_url": str(engine.url),
        # SessionLocal is a sessionmaker *instance*, not a class — hence type().
        "session_factory": type(SessionLocal).__name__,
    }


def main() -> None:
    """Start the MCP server on stdio and block until the client disconnects."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("Starting MCP server 'ai-assistant-for-lms' on stdio…")
    server.run(transport="stdio")
