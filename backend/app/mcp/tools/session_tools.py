"""
Phase 4, Sub-feature 4.2: session-matching MCP tools.

Thin MCP wrapper over Phase 3.1's session_matcher. No matching logic lives
here — the tool opens a database session, delegates to the existing
match_instruction_to_session, and returns its result dict untouched, so
the MCP interface and the REST /chat endpoint can never drift apart in
how they resolve an instruction.

Registration pattern
────────────────────
This module exposes register(server) rather than importing the server
instance and decorating with @server.tool. Registration therefore flows
one way (server -> tools), which avoids the circular import that
``from app.mcp.server import server`` here would create, and keeps
server.py a thin registry as 4.3-4.5 add more domain tool modules.
"""

import logging

from mcp.server import MCPServer

from app.database import SessionLocal
from app.services.session_matcher import match_instruction_to_session

logger = logging.getLogger(__name__)


def match_session(instruction: str, instructor_id: int) -> dict:
    """
    Resolve a free-text instruction to one of that instructor's sessions.

    Returns Phase 3.1's result dict exactly as-is — one of:
      {"status": "matched", "session_id": int, "session_title": str,
       "confidence": float | None}
      {"status": "ambiguous", "candidates": [{"session_id", "session_title",
       "confidence"}, ...]}
      {"status": "no_match"}

    "ambiguous" and "no_match" are normal outcomes, not errors: the matcher
    never force-matches on a close call, so the caller is expected to ask
    the instructor which session they meant rather than guessing.
    """
    db = SessionLocal()
    try:
        result = match_instruction_to_session(instruction, instructor_id, db)
    finally:
        db.close()

    logger.info(
        "MCP match_session: instructor=%d instruction=%r -> %s",
        instructor_id, instruction, result.get("status"),
    )
    return result


def register(server: MCPServer) -> None:
    """Register this module's tools on *server*."""
    server.add_tool(
        match_session,
        name="match_session",
        description=(
            "Resolve a free-text grading instruction (e.g. 'grade week 8 day 3') "
            "to one of an instructor's existing sessions. Returns status "
            "'matched' with the session id/title/confidence, 'ambiguous' with "
            "candidate sessions when more than one plausibly fits, or "
            "'no_match'. Ambiguous and no_match are normal outcomes — ask the "
            "instructor which session they meant rather than guessing."
        ),
    )
