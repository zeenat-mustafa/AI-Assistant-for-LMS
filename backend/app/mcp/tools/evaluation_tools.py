"""
Phase 4, Sub-feature 4.4: submission-evaluation MCP tools.

Thin MCP wrapper over Phase 2.4's evaluator. No evaluation logic lives
here — the tool opens a database session, delegates to the existing
evaluate_submission_file, and returns its result dict untouched. Same
register(server) pattern as session_tools.py (4.2) and rubric_tools.py
(4.3).

Argument order, verified rather than assumed (it differs between
services): this one takes ``db`` FIRST, then submission_file_id, and
takes no other parameters.
"""

import logging

from mcp.server import MCPServer

from app.database import SessionLocal
from app.services.evaluator import evaluate_submission_file

logger = logging.getLogger(__name__)


def evaluate_submission(submission_file_id: int) -> dict:
    """
    Evaluate one student notebook against its matched assignment's rubric.

    Returns Phase 2.4's result dict exactly as-is — one of:
      {"success": True, "total_score": float, "criteria": [
          {"criterion": str, "points_possible": float,
           "points_awarded": float, "explanation": str}, ...]}
      {"success": False, "error": str}

    Scores are in 0.5-point increments and the criteria sum to 10.

    Two behaviours worth knowing:
      - If the assignment has no rubric yet, one is generated on the fly
        (and cached for every later student), so a first call against a
        fresh assignment costs two LLM requests rather than one.
      - This does NOT write a Grade row. It is read-only with respect to
        grading; persisting a grade is the grading pipeline's job (see
        the batch grading tool in 4.5). Rubric generation is the one
        side effect, since the generated rubric is cached.

    A submission file that never matched an assignment returns
    {"success": False, "error": "not matched to an assignment"} without
    making any LLM call — a normal outcome for an unmatched upload, not
    a crash.
    """
    db = SessionLocal()
    try:
        result = evaluate_submission_file(db, submission_file_id)
    finally:
        db.close()

    if result.get("success"):
        logger.info(
            "MCP evaluate_submission: submission_file_id=%d -> %.1f/10 across %d criteria",
            submission_file_id,
            result.get("total_score", 0.0),
            len(result.get("criteria") or []),
        )
    else:
        logger.info(
            "MCP evaluate_submission: submission_file_id=%d -> failed: %s",
            submission_file_id, result.get("error"),
        )
    return result


def register(server: MCPServer) -> None:
    """Register this module's tools on *server*."""
    server.add_tool(
        evaluate_submission,
        name="evaluate_submission",
        description=(
            "Evaluate one student notebook against its matched assignment's "
            "rubric, returning a total score out of 10 plus a "
            "criterion-by-criterion breakdown with points awarded and an "
            "explanation for each. Generates the assignment's rubric first if "
            "it doesn't have one yet. This does not record a grade — it "
            "reports what the evaluation found."
        ),
    )
