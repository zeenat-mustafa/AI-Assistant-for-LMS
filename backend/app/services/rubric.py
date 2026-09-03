"""
Rubric generation service — Phase 2, Sub-feature 3.

Generates 3–6 gradable criteria worth 10 marks total from an unsolved
assignment file's requirements text using Google Gemini.

Key capabilities:
  - build_rubric_prompt: creates strict prompt for Gemini requiring pure JSON.
  - call_gemini_for_rubric: invokes Gemini with safety exception handling.
  - parse_rubric_response: strips markdown fences, parses JSON, rescales to 10 pts.
  - generate_rubric_for_unsolved_file: orchestrates cache check, generation, retry,
    and database persistence.
"""

import json
import logging
import re
from typing import Any

import google.generativeai as genai
from sqlalchemy.orm import Session

from app.config import settings
from app.models.unsolved_file import UnsolvedFile

logger = logging.getLogger(__name__)


class RubricGenerationError(Exception):
    """Raised when Gemini API call fails during rubric generation."""
    pass


def build_rubric_prompt(requirements_text: str) -> str:
    """
    Build a prompt instructing Gemini to read assignment requirements and
    generate 3-6 gradable criteria totaling 10 marks.
    """
    return (
        "You are an expert academic grading assistant. Read the following assignment "
        "instructions and requirements carefully, then generate 3 to 6 gradable criteria "
        "worth 10 marks total, distributed across them based on their importance.\n\n"
        "Instructions:\n"
        "- Respond with ONLY valid JSON.\n"
        "- Do NOT include any markdown fences (no ``` or ```json).\n"
        "- Do NOT include any introductory or explanatory text.\n"
        "- The output must match this exact JSON shape:\n"
        '{"criteria": [{"criterion": str, "points_possible": number}, ...]}\n'
        "- The sum of points_possible across all criteria must equal exactly 10.\n\n"
        "Assignment Requirements:\n"
        f"{requirements_text.strip()}\n"
    )


def call_gemini_for_rubric(prompt: str) -> str:
    """
    Call Gemini using GEMINI_API_KEY and GEMINI_FAST_MODEL from settings.
    Raises RubricGenerationError on any failure — never crashes caller.
    """
    if not settings.gemini_api_key:
        raise RubricGenerationError("Gemini API key is not configured in settings.")

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_fast_model)
        response = model.generate_content(prompt)
        if not response or not response.text:
            raise RubricGenerationError("Gemini returned an empty response.")
        return response.text
    except Exception as exc:
        logger.warning("Gemini rubric generation call failed: %s", exc)
        raise RubricGenerationError(f"Gemini call failed: {exc}") from exc


def parse_rubric_response(raw_text: str) -> dict[str, Any]:
    """
    Strip markdown code fences if present, parse as JSON, and validate criteria.
    If points don't sum to exactly 10 (allow 0.01 tolerance), proportionally
    rescale them so they do.

    Returns
    -------
    {"valid": bool, "criteria": list[dict], "error": str | None}
    """
    if not raw_text or not raw_text.strip():
        return {"valid": False, "criteria": [], "error": "Empty response from Gemini."}

    text = raw_text.strip()

    # Strip markdown code fences if present (e.g. ```json ... ``` or ``` ... ```)
    if "```" in text:
        # Match content between ```json ... ``` or ``` ... ```
        fence_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
        match = re.search(fence_pattern, text, re.IGNORECASE)
        if match:
            text = match.group(1).strip()
        else:
            # Fallback: remove leading/trailing fence lines
            lines = text.splitlines()
            cleaned_lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(cleaned_lines).strip()

    # Attempt to locate the first JSON object if surrounded by extra text
    if not text.startswith("{"):
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"valid": False, "criteria": [], "error": f"Invalid JSON: {exc}"}

    # Extract criteria list
    if isinstance(data, dict):
        criteria_list = data.get("criteria")
    elif isinstance(data, list):
        criteria_list = data
    else:
        return {
            "valid": False,
            "criteria": [],
            "error": "JSON root must be an object with 'criteria' key or a list.",
        }

    if not isinstance(criteria_list, list) or len(criteria_list) == 0:
        return {
            "valid": False,
            "criteria": [],
            "error": "Rubric criteria must be a non-empty list.",
        }

    validated_criteria: list[dict[str, Any]] = []
    for idx, item in enumerate(criteria_list):
        if not isinstance(item, dict):
            return {
                "valid": False,
                "criteria": [],
                "error": f"Item at index {idx} is not a JSON object.",
            }
        if "criterion" not in item or "points_possible" not in item:
            return {
                "valid": False,
                "criteria": [],
                "error": f"Item at index {idx} missing 'criterion' or 'points_possible'.",
            }

        crit_str = str(item["criterion"]).strip()
        if not crit_str:
            return {
                "valid": False,
                "criteria": [],
                "error": f"Item at index {idx} has an empty criterion name.",
            }

        try:
            pts = float(item["points_possible"])
        except (ValueError, TypeError):
            return {
                "valid": False,
                "criteria": [],
                "error": f"Item at index {idx} has invalid points_possible: {item['points_possible']}",
            }

        if pts <= 0:
            return {
                "valid": False,
                "criteria": [],
                "error": f"Item at index {idx} has non-positive points_possible: {pts}",
            }

        validated_criteria.append({"criterion": crit_str, "points_possible": pts})

    total_points = sum(c["points_possible"] for c in validated_criteria)
    if total_points <= 0:
        return {
            "valid": False,
            "criteria": [],
            "error": "Total points possible must be greater than zero.",
        }

    # If points don't sum to exactly 10 (tolerance 0.01), proportionally rescale
    if abs(total_points - 10.0) > 0.01:
        scale = 10.0 / total_points
        for c in validated_criteria:
            c["points_possible"] = round(c["points_possible"] * scale, 2)

        # Fix minor rounding discrepancies on the highest-scoring criterion
        rounding_diff = round(10.0 - sum(c["points_possible"] for c in validated_criteria), 2)
        if abs(rounding_diff) > 0:
            max_idx = max(
                range(len(validated_criteria)),
                key=lambda i: validated_criteria[i]["points_possible"],
            )
            validated_criteria[max_idx]["points_possible"] = round(
                validated_criteria[max_idx]["points_possible"] + rounding_diff, 2
            )
    else:
        for c in validated_criteria:
            c["points_possible"] = round(c["points_possible"], 2)

    return {"valid": True, "criteria": validated_criteria, "error": None}


def generate_rubric_for_unsolved_file(db: Session, unsolved_file_id: int) -> dict[str, Any]:
    """
    Orchestrate rubric generation for an UnsolvedFile.

    1. Load UnsolvedFile row.
    2. If rubric_generated is already True, return cached rubric_json immediately.
    3. If parsed_requirements_text is empty/None, return error without calling Gemini.
    4. Call Gemini and parse response.
    5. If parsing fails, retry once with an added strict JSON instruction.
    6. On success, save rubric to rubric_json, set rubric_generated=True, commit,
       and return {"success": True, "rubric": {...}}.
    7. Never crashes the caller.
    """
    try:
        unsolved = db.get(UnsolvedFile, unsolved_file_id)
        if unsolved is None:
            return {
                "success": False,
                "error": f"Unsolved assignment file with id {unsolved_file_id} not found.",
            }

        # Cached reuse check: return immediately if already generated
        if unsolved.rubric_generated and unsolved.rubric_json:
            try:
                cached = (
                    json.loads(unsolved.rubric_json)
                    if isinstance(unsolved.rubric_json, str)
                    else unsolved.rubric_json
                )
                logger.info("Returning cached rubric for unsolved_file %d", unsolved_file_id)
                return {"success": True, "rubric": cached}
            except Exception as exc:
                logger.warning(
                    "Failed to deserialize cached rubric_json for file %d: %s. Regenerating.",
                    unsolved_file_id,
                    exc,
                )

        requirements = (unsolved.parsed_requirements_text or "").strip()
        if not requirements:
            return {
                "success": False,
                "error": "no requirements text available",
            }

        prompt = build_rubric_prompt(requirements)

        # First attempt
        raw_text = ""
        try:
            raw_text = call_gemini_for_rubric(prompt)
        except RubricGenerationError as exc:
            return {"success": False, "error": str(exc)}

        parsed = parse_rubric_response(raw_text)

        # If parsing fails, retry once with explicit directive
        if not parsed["valid"]:
            logger.info(
                "First rubric parse attempt failed for file %d (%s). Retrying once with strict prompt.",
                unsolved_file_id,
                parsed["error"],
            )
            retry_prompt = prompt + "\n\nRespond with valid JSON only, absolutely no markdown."
            try:
                raw_text = call_gemini_for_rubric(retry_prompt)
                parsed = parse_rubric_response(raw_text)
            except RubricGenerationError as exc:
                return {
                    "success": False,
                    "error": f"Retry failed: {exc}",
                }

        if not parsed["valid"]:
            return {
                "success": False,
                "error": parsed["error"] or "Failed to parse rubric response from Gemini.",
            }

        rubric_data = {"criteria": parsed["criteria"]}
        unsolved.rubric_json = json.dumps(rubric_data)
        unsolved.rubric_generated = True
        db.commit()
        db.refresh(unsolved)

        logger.info("Successfully generated and saved rubric for unsolved_file %d", unsolved_file_id)
        return {"success": True, "rubric": rubric_data}

    except Exception as exc:
        logger.error(
            "Unexpected error in generate_rubric_for_unsolved_file(%d): %s",
            unsolved_file_id,
            exc,
            exc_info=True,
        )
        return {"success": False, "error": f"Unexpected error: {exc}"}
