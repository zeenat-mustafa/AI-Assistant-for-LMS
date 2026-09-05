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

from sqlalchemy.orm import Session

from app.models.unsolved_file import UnsolvedFile
from app.services import llm_provider
from app.services.notebook import extract_notebook_structure
from app.services.storage import absolute_path

logger = logging.getLogger(__name__)


class RubricGenerationError(Exception):
    """Raised when Gemini API call fails during rubric generation."""
    pass


def build_rubric_prompt(cells: list[dict[str, Any]]) -> str:
    """Build a fairness-aware rubric prompt from the ordered notebook cells."""
    notebook_view = _format_cells_for_prompt(cells)
    return (
        "You are an expert academic grading assistant. Read this unsolved Jupyter "
        "notebook in its original order and generate a fair rubric with 3 to 6 criteria.\n\n"
        "Instructions:\n"
        "- Each cell's heuristic_hint is only a rough starting signal and may be wrong "
        "in either direction. Read the actual content and flow; never treat the hint as authoritative.\n"
        "- Determine which sections genuinely require student work. This includes a question "
        "in one markdown cell whose answer belongs in a later response cell, informal "
        "comments asking for answer/code regardless of wording, TODO-style blanks, and other contextual patterns.\n"
        "- Put the majority of the 10 marks on work that genuinely requires completion. Across "
        "all pre-written/scaffolding sections, award at most 1 to 2 marks total, only for "
        "checks such as 'runs without error'.\n"
        "- Use 0.5-point increments only.\n"
        "- Respond with ONLY valid JSON.\n"
        "- Do NOT include any markdown fences (no ``` or ```json).\n"
        "- Do NOT include any introductory or explanatory text.\n"
        "- The output must match this exact JSON shape:\n"
        '{"criteria": [{"criterion": str, "points_possible": number}, ...]}\n'
        "- The sum of points_possible across all criteria must equal exactly 10.\n\n"
        "Unsolved Notebook (ordered cells):\n"
        f"{notebook_view}\n"
    )


def _format_cells_for_prompt(cells: list[dict[str, Any]]) -> str:
    if not cells:
        return "(Notebook contains no markdown or code cells.)"
    return "\n\n".join(
        f"--- Cell {index} [{cell.get('type', 'unknown')}] heuristic_hint={bool(cell.get('heuristic_hint'))} ---\n"
        f"{cell.get('content', '')}"
        for index, cell in enumerate(cells, start=1)
    )


def call_gemini_for_rubric(prompt: str) -> str:
    """
    Send *prompt* to the LLM provider (Gemini, with automatic Groq fallback on
    quota/rate-limit errors) and return the raw response text.
    Raises RubricGenerationError on any failure — never crashes caller.
    """
    try:
        return llm_provider.call_llm(prompt, purpose="fast")
    except llm_provider.LLMProviderError as exc:
        logger.warning("LLM rubric generation call failed (both providers): %s", exc)
        raise RubricGenerationError(f"LLM call failed: {exc}") from exc
    except Exception as exc:
        logger.warning("LLM rubric generation call failed: %s", exc)
        raise RubricGenerationError(f"LLM call failed: {exc}") from exc


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

    # Normalise first, then restrict allocations to half-point increments.
    scale = 10.0 / total_points
    for criterion in validated_criteria:
        criterion["points_possible"] = _round_to_half(criterion["points_possible"] * scale)
    largest_index = max(
        range(len(validated_criteria)), key=lambda i: validated_criteria[i]["points_possible"]
    )
    remainder = round(10.0 - sum(c["points_possible"] for c in validated_criteria), 1)
    validated_criteria[largest_index]["points_possible"] = round(
        validated_criteria[largest_index]["points_possible"] + remainder, 1
    )

    return {"valid": True, "criteria": validated_criteria, "error": None}


def _round_to_half(value: float) -> float:
    return round(value * 2) / 2


def generate_rubric_for_unsolved_file(
    db: Session, unsolved_file_id: int, force: bool = False
) -> dict[str, Any]:
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
        if not force and unsolved.rubric_generated and unsolved.rubric_json:
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

        structure = extract_notebook_structure(str(absolute_path(unsolved.file_path)))
        if not structure["valid"]:
            return {
                "success": False,
                "error": f"Could not extract assignment notebook structure: {structure['error']}",
            }
        if not structure["cells"]:
            return {"success": False, "error": "assignment notebook contains no markdown or code cells"}

        prompt = build_rubric_prompt(structure["cells"])

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
