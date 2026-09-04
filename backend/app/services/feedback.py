"""
Feedback generation and Grade persistence — Phase 2, Sub-feature 5.

Converts the structured evaluation result produced by Sub-feature 4
(evaluate_submission_file) into a human-readable feedback string and a
persisted Grade record — without making any additional LLM calls.

The per-criterion explanation text generated during evaluation is already
detailed enough to assemble real, useful feedback directly.

Public API
──────────
    build_feedback_text(evaluation_result)          → str
    build_rationale_json(evaluation_result)         → list[dict]
    persist_grade(db, submission_file_id, result)   → dict
    generate_feedback_and_persist(db, submission_file_id) → dict
"""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── 1. build_feedback_text ────────────────────────────────────────────────────

def build_feedback_text(evaluation_result: dict) -> str:
    """
    Assemble a human-readable feedback string from an evaluation result.

    Format
    ------
    Score: {total_score}/10.
    - {criterion}: {points_awarded}/{points_possible} — {explanation}
    - ...

    Parameters
    ----------
    evaluation_result:
        Dict with at minimum ``"total_score"`` (float) and
        ``"criteria"`` (list of criterion dicts).

    Returns
    -------
    str
        Multi-line feedback string.  If ``criteria`` is empty, returns
        just the opening score line.
    """
    total_score = evaluation_result.get("total_score", 0.0)
    criteria = evaluation_result.get("criteria", [])

    lines = [f"Score: {total_score}/10."]

    for c in criteria:
        criterion = c.get("criterion", "")
        points_awarded = c.get("points_awarded", 0.0)
        points_possible = c.get("points_possible", 0.0)
        explanation = c.get("explanation", "")
        lines.append(
            f"- {criterion}: {points_awarded}/{points_possible} — {explanation}"
        )

    return "\n".join(lines)


# ── 2. build_rationale_json ───────────────────────────────────────────────────

def build_rationale_json(evaluation_result: dict) -> list[dict]:
    """
    Validate and return the criteria list in the exact shape stored in
    ``Grade.rationale_json``.

    Expected shape per entry:
        {"criterion": str, "points_possible": float,
         "points_awarded": float, "explanation": str}

    Parameters
    ----------
    evaluation_result:
        Dict containing a ``"criteria"`` list.

    Returns
    -------
    list[dict]
        Validated criteria list, ready to be JSON-serialised into
        ``Grade.rationale_json``.

    Raises
    ------
    ValueError
        If any entry is missing a required key or has a wrong type.
        This should never happen if Sub-feature 4 is working correctly,
        but we fail loudly rather than silently persisting garbage.
    """
    criteria = evaluation_result.get("criteria", [])

    if not isinstance(criteria, list):
        raise ValueError(
            f"'criteria' must be a list, got {type(criteria).__name__}."
        )

    validated: list[dict] = []
    required_keys = {"criterion", "points_possible", "points_awarded", "explanation"}

    for idx, item in enumerate(criteria):
        if not isinstance(item, dict):
            raise ValueError(
                f"Criterion at index {idx} must be a dict, got {type(item).__name__}."
            )

        missing = required_keys - item.keys()
        if missing:
            raise ValueError(
                f"Criterion at index {idx} is missing required key(s): "
                f"{', '.join(sorted(missing))}."
            )

        # Type coercion with strict checks
        criterion = item["criterion"]
        if not isinstance(criterion, str) or not criterion.strip():
            raise ValueError(
                f"Criterion at index {idx}: 'criterion' must be a non-empty string."
            )

        try:
            points_possible = float(item["points_possible"])
            points_awarded = float(item["points_awarded"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Criterion at index {idx}: 'points_possible'/'points_awarded' "
                f"must be numeric. ({exc})"
            ) from exc

        explanation = item["explanation"]
        if not isinstance(explanation, str):
            raise ValueError(
                f"Criterion at index {idx}: 'explanation' must be a string."
            )

        validated.append({
            "criterion": criterion.strip(),
            "points_possible": points_possible,
            "points_awarded": points_awarded,
            "explanation": explanation.strip(),
        })

    return validated


# ── 3. persist_grade ──────────────────────────────────────────────────────────

def persist_grade(
    db: Session,
    submission_file_id: int,
    evaluation_result: dict,
) -> dict:
    """
    Create or overwrite the Grade record for a SubmissionFile.

    Re-grading overwrites the existing row in place (no versioning in MVP),
    consistent with the ``unique=True`` constraint on
    ``Grade.submission_file_id``.

    Steps
    -----
    1. Build ``feedback_text`` and ``rationale_json`` from the result.
    2. Load any existing Grade row for ``submission_file_id``.
    3. If found, update it in place; otherwise create a new row.
    4. Set ``SubmissionFile.graded = True``.
    5. Commit and return ``{"grade_id", "score", "feedback_text"}``.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    submission_file_id:
        PK of the SubmissionFile being graded.
    evaluation_result:
        The ``{"success": True, "total_score": float, "criteria": [...]}``
        dict returned by ``evaluate_submission_file``.

    Returns
    -------
    dict
        ``{"grade_id": int, "score": float, "feedback_text": str}``
    """
    # Deferred imports to avoid circular imports at module load time.
    from app.models.grade import Grade
    from app.models.submission_file import SubmissionFile

    feedback_text = build_feedback_text(evaluation_result)
    rationale_list = build_rationale_json(evaluation_result)
    rationale_json_str = json.dumps(rationale_list)

    score = float(evaluation_result.get("total_score", 0.0))
    now = datetime.now(timezone.utc)

    # Check for an existing Grade row (unique on submission_file_id).
    existing: Grade | None = (
        db.query(Grade)
        .filter(Grade.submission_file_id == submission_file_id)
        .first()
    )

    if existing:
        logger.info(
            "persist_grade: overwriting existing Grade %d for "
            "SubmissionFile %d (re-grade).",
            existing.id, submission_file_id,
        )
        existing.score = score
        existing.feedback_text = feedback_text
        existing.rationale_json = rationale_json_str
        existing.graded_at = now
        grade = existing
    else:
        grade = Grade(
            submission_file_id=submission_file_id,
            score=score,
            feedback_text=feedback_text,
            rationale_json=rationale_json_str,
            graded_at=now,
        )
        db.add(grade)
        logger.info(
            "persist_grade: created new Grade for SubmissionFile %d.",
            submission_file_id,
        )

    # Mark the SubmissionFile as graded.
    sub_file: SubmissionFile | None = db.get(SubmissionFile, submission_file_id)
    if sub_file is not None:
        sub_file.graded = True

    db.commit()
    if existing:
        db.refresh(grade)
    else:
        db.refresh(grade)

    logger.info(
        "persist_grade: committed Grade %d — score %.1f/10 for "
        "SubmissionFile %d.",
        grade.id, score, submission_file_id,
    )

    return {
        "grade_id": grade.id,
        "score": grade.score,
        "feedback_text": grade.feedback_text,
    }


# ── 4. generate_feedback_and_persist ─────────────────────────────────────────

def generate_feedback_and_persist(db: Session, submission_file_id: int) -> dict:
    """
    Top-level orchestrator for Sub-feature 5.

    1. Calls ``evaluate_submission_file`` (Sub-feature 4).
    2. On failure, returns the failure result unchanged — no Grade row
       is created or modified.
    3. On success, calls ``persist_grade`` and returns:
       ``{"success": True, "grade_id": int, "score": float,
          "feedback_text": str}``

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    submission_file_id:
        PK of the SubmissionFile to evaluate and grade.

    Returns
    -------
    dict
        Success: ``{"success": True, "grade_id", "score", "feedback_text"}``
        Failure: ``{"success": False, "error": str}``
    """
    from app.services.evaluator import evaluate_submission_file

    logger.info(
        "generate_feedback_and_persist: evaluating SubmissionFile %d.",
        submission_file_id,
    )

    evaluation_result = evaluate_submission_file(db, submission_file_id)

    if not evaluation_result.get("success"):
        logger.warning(
            "generate_feedback_and_persist: evaluation failed for "
            "SubmissionFile %d — %s. No Grade row created.",
            submission_file_id,
            evaluation_result.get("error"),
        )
        return evaluation_result

    try:
        persisted = persist_grade(db, submission_file_id, evaluation_result)
    except Exception as exc:
        logger.error(
            "generate_feedback_and_persist: persist_grade failed for "
            "SubmissionFile %d: %s",
            submission_file_id, exc, exc_info=True,
        )
        return {"success": False, "error": f"Grade persistence failed: {exc}"}

    return {
        "success": True,
        "grade_id": persisted["grade_id"],
        "score": persisted["score"],
        "feedback_text": persisted["feedback_text"],
    }
