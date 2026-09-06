"""
Entry point for the MCP server (Phase 4, Sub-feature 4.1).

    cd backend
    python -m app.mcp

``python -m app.mcp`` is used rather than a standalone script because the
server imports ``app.config`` / ``app.database`` by absolute package path,
exactly as the FastAPI app does — running it as a module keeps ``backend/``
on sys.path so those imports resolve identically in both processes, and it
mirrors how the REST app is started (``python -m uvicorn app.main:app``).

The server speaks JSON-RPC over stdio and blocks with no banner or port;
that is expected. It is normally launched by an MCP client rather than by
hand — see the README's "MCP Server" section for a client config example.
"""

from app.mcp.server import main

main()
