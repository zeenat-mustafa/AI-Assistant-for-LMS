"""
Phase 4, Sub-feature 4.3: rubric-generation MCP tools.

Thin MCP wrapper over Phase 2.3's rubric service. No rubric logic lives
here — the tool opens a database session, delegates to the existing
generate_rubric_for_unsolved_file, and returns its result dict untouched,
so the MCP interface and the REST generate-rubric endpoint can never
drift apart. Same register(server) pattern as session_tools.py (4.2).

Note the argument order of the underlying function: it takes ``db``
FIRST (unlike session_matcher's, which takes it last).
"""

import logging

from mcp.server import MCPServer

from app.database import SessionLocal
from app.services.rubric import generate_rubric_for_unsolved_file

logger = logging.getLogger(__name__)


def generate_rubric(unsolved_file_id: int, force: bool = False) -> dict:
    """
    Generate (or fetch the cached) grading rubric for an assignment file.

    Returns Phase 2.3's result dict exactly as-is — one of:
      {"success": True, "rubric": {"criteria": [...]}}
      {"success": True, "rubric": {...}, "warning": str}
      {"success": False, "error": str}

    Caching: a rubric is generated once per assignment file and reused for
    every student, so a second call without force returns the stored
    rubric and makes no LLM request. Pass force=True to regenerate.

    The "warning" key appears ONLY on a force regeneration that invalidates
    existing grades: those Grade.rationale_json entries still reference the
    previous rubric's criteria. Nothing is re-graded and no rows change —
    the count is surfaced so the instructor knows. The key is omitted
    entirely (not empty) when force=False or when nothing is stale.

    Unlike the REST endpoint, this tool addresses the file directly by id
    and does not verify it belongs to a particular session.
    """
    db = SessionLocal()
    try:
        result = generate_rubric_for_unsolved_file(db, unsolved_file_id, force=force)
    finally:
        db.close()

    logger.info(
        "MCP generate_rubric: unsolved_file_id=%d force=%s -> success=%s%s",
        unsolved_file_id,
        force,
        result.get("success"),
        " (with staleness warning)" if "warning" in result else "",
    )
    return result


def register(server: MCPServer) -> None:
    """Register this module's tools on *server*."""
    server.add_tool(
        generate_rubric,
        name="generate_rubric",
        description=(
            "Generate the grading rubric for one assignment file, or return "
            "the cached rubric if it already has one. Rubrics are worth 10 "
            "points, generated once per assignment file and reused for every "
            "student, so repeat calls are free unless force=true. Use "
            "force=true to regenerate; if any submissions were already graded "
            "against the previous rubric the result includes a 'warning' "
            "noting those grades are now stale (nothing is re-graded "
            "automatically)."
        ),
    )
