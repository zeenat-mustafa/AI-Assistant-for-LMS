"""
Submission evaluation service — Phase 2, Sub-feature 4.

Evaluates student notebook submissions against assignment rubrics using Google Gemini.
Extracts code cells and execution outputs, submits them alongside the rubric criteria,
and computes clamped per-criterion points and a total score out of 10.

Key capabilities:
  - EvaluationError: custom exception for Gemini API evaluation failures.
  - build_evaluation_prompt: constructs prompt with rubric and student code/outputs.
  - call_gemini_for_evaluation: invokes Gemini model with safe exception handling.
  - parse_evaluation_response: parses JSON, clamps points per criterion and total score.
  - evaluate_submission_file: orchestrates loading, matching check, rubric retrieval,
    notebook parsing, evaluation, and structured result return.
"""

import json
import logging
import re
from typing import Any

import google.generativeai as genai
from sqlalchemy.orm import Session

from app.config import settings
from app.models.submission_file import SubmissionFile
from app.models.unsolved_file import UnsolvedFile
from app.services import llm_provider
from app.services.notebook import extract_notebook_structure, parse_notebook_file
from app.services.rubric import generate_rubric_for_unsolved_file
from app.services.storage import absolute_path

logger = logging.getLogger(__name__)


class EvaluationError(Exception):
    """Raised when Gemini API call fails during submission evaluation."""
    pass


def build_evaluation_prompt(
    rubric: dict[str, Any],
    unsolved_cells: list[dict[str, Any]],
    submission_cells: list[dict[str, Any]],
) -> str:
    """
    Build a prompt giving Gemini the rubric criteria and the student's actual
    code + cell outputs (plain text), instructing it to award points per criterion
    (never exceeding that criterion's points_possible) with a short explanation each.

    Instructs Gemini to respond with ONLY valid JSON matching the shape:
    {"criteria": [{"criterion": str, "points_possible": number, "points_awarded": number, "explanation": str}, ...]}
    """
    criteria_list = rubric.get("criteria", []) if isinstance(rubric, dict) else rubric
    rubric_formatted = json.dumps({"criteria": criteria_list}, indent=2)
    unsolved_view = _format_cells_for_evaluation(unsolved_cells)
    submission_view = _format_cells_for_evaluation(submission_cells)

    return (
        "You are an expert academic grading assistant. Evaluate the following student's "
        "Jupyter notebook submission against the provided rubric.\n\n"
        "Grading Instructions:\n"
        "1. Inspect corresponding ordered cells and their recorded outputs.\n"
        "2. heuristic_hint is only a rough starting signal and may be wrong in either direction. "
        "Use actual content and notebook flow to decide what student input was expected, including "
        "questions followed by later response cells and informal answer/code prompts.\n"
        "3. Most marks must assess genuinely student-completed work. For pre-written sections, assess "
        "only the rubric's small runs-correctly portion: verify no error output and, where the unsolved "
        "version records output, a submitted output consistent with correct execution.\n"
        "4. Award points ('points_awarded') for each criterion based on correctness and completeness.\n"
        "5. You must NEVER award more than that criterion's 'points_possible', and never award negative points.\n"
        "6. Provide a concise, constructive explanation for the points awarded on each criterion.\n"
        "5. Respond with ONLY valid JSON — absolutely no markdown fences (no ``` or ```json), "
        "and no conversational text before or after.\n\n"
        "Award points in 0.5-point increments only.\n"
        "Required JSON Output Shape:\n"
        "{\n"
        '  "criteria": [\n'
        '    {\n'
        '      "criterion": str,\n'
        '      "points_possible": number,\n'
        '      "points_awarded": number,\n'
        '      "explanation": str\n'
        '    }\n'
        '  ]\n'
        "}\n\n"
        f"Rubric:\n{rubric_formatted}\n\n"
        f"Unsolved Notebook (ordered cells):\n{unsolved_view}\n\n"
        f"Student Submission (corresponding ordered cells and recorded outputs):\n{submission_view}\n"
    )


def _format_cells_for_evaluation(cells: list[dict[str, Any]]) -> str:
    if not cells:
        return "(No cells available.)"
    return "\n\n".join(
        f"--- Cell {index} [{cell.get('type', 'unknown')}] heuristic_hint={bool(cell.get('heuristic_hint'))} ---\n"
        f"{cell.get('content', '')}"
        for index, cell in enumerate(cells, start=1)
    )


def call_gemini_for_evaluation(prompt: str) -> str:
    """
    Send *prompt* to the LLM provider (Gemini, with automatic Groq fallback on
    quota/rate-limit errors) and return the raw response text.
    Raises EvaluationError on failure — never crashes caller.
    """
    try:
        return llm_provider.call_llm(prompt, purpose="fast")
    except llm_provider.LLMProviderError as exc:
        logger.warning("LLM submission evaluation call failed (both providers): %s", exc)
        raise EvaluationError(f"LLM call failed: {exc}") from exc
    except Exception as exc:
        logger.warning("LLM submission evaluation call failed: %s", exc)
        raise EvaluationError(f"LLM call failed: {exc}") from exc


def parse_evaluation_response(raw_text: str, rubric: dict[str, Any]) -> dict[str, Any]:
    """
    Strip markdown fences, parse JSON, and validate against the expected rubric.

    Validation rules:
      - Must contain the same criteria count and names as the rubric.
      - Clamps each criterion's points_awarded between 0 and points_possible.
      - Computes total_score = sum(points_awarded), clamped to [0.0, 10.0].

    Returns
    -------
    {"valid": bool, "criteria": [...], "total_score": float, "error": str | None}
    """
    if not raw_text or not raw_text.strip():
        return {
            "valid": False,
            "criteria": [],
            "total_score": 0.0,
            "error": "Empty response from Gemini.",
        }

    text = raw_text.strip()

    # Strip markdown code fences if present (e.g. ```json ... ``` or ``` ... ```)
    if "```" in text:
        fence_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
        match = re.search(fence_pattern, text, re.IGNORECASE)
        if match:
            text = match.group(1).strip()
        else:
            lines = text.splitlines()
            cleaned_lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(cleaned_lines).strip()

    # Locate JSON object
    if not text.startswith("{"):
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "valid": False,
            "criteria": [],
            "total_score": 0.0,
            "error": f"Invalid JSON: {exc}",
        }

    if isinstance(data, dict):
        evaluated_criteria = data.get("criteria")
    elif isinstance(data, list):
        evaluated_criteria = data
    else:
        return {
            "valid": False,
            "criteria": [],
            "total_score": 0.0,
            "error": "JSON root must be an object with 'criteria' key or a list.",
        }

    if not isinstance(evaluated_criteria, list):
        return {
            "valid": False,
            "criteria": [],
            "total_score": 0.0,
            "error": "'criteria' must be a list.",
        }

    rubric_criteria = rubric.get("criteria", []) if isinstance(rubric, dict) else rubric
    if not isinstance(rubric_criteria, list):
        rubric_criteria = []

    if len(evaluated_criteria) != len(rubric_criteria):
        return {
            "valid": False,
            "criteria": [],
            "total_score": 0.0,
            "error": (
                f"Criteria count mismatch: expected {len(rubric_criteria)}, "
                f"got {len(evaluated_criteria)}."
            ),
        }

    # Index evaluated criteria by normalized criterion name
    evaluated_map: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(evaluated_criteria):
        if not isinstance(item, dict) or "criterion" not in item:
            return {
                "valid": False,
                "criteria": [],
                "total_score": 0.0,
                "error": f"Evaluated item at index {idx} is invalid or missing 'criterion'.",
            }
        crit_name = str(item["criterion"]).strip()
        evaluated_map[crit_name.lower()] = item

    # Match each expected rubric criterion and clamp points
    validated_criteria: list[dict[str, Any]] = []
    running_total = 0.0

    for idx, expected in enumerate(rubric_criteria):
        expected_name = str(expected.get("criterion", "")).strip()
        matched = evaluated_map.get(expected_name.lower())

        # Fallback to positional match if count matches and names were slightly altered
        if matched is None and idx < len(evaluated_criteria):
            candidate = evaluated_criteria[idx]
            if isinstance(candidate, dict):
                matched = candidate

        if matched is None:
            return {
                "valid": False,
                "criteria": [],
                "total_score": 0.0,
                "error": f"Missing evaluation for rubric criterion: '{expected_name}'.",
            }

        points_possible = float(expected.get("points_possible", 0.0))

        raw_awarded = matched.get("points_awarded", 0.0)
        try:
            points_awarded = float(raw_awarded)
        except (ValueError, TypeError):
            points_awarded = 0.0

        # Clamp and normalize every awarded score to a half-point increment.
        points_awarded = max(0.0, min(points_possible, points_awarded))
        points_awarded = _round_to_half(points_awarded)
        points_awarded = max(0.0, min(points_possible, points_awarded))

        explanation = str(matched.get("explanation", "")).strip()
        running_total += points_awarded

        validated_criteria.append({
            "criterion": expected_name,
            "points_possible": _round_to_half(points_possible),
            "points_awarded": points_awarded,
            "explanation": explanation,
        })

    # Clamp total score between 0.0 and 10.0
    total_score = _round_to_half(max(0.0, min(10.0, running_total)))

    return {
        "valid": True,
        "criteria": validated_criteria,
        "total_score": total_score,
        "error": None,
    }


def _round_to_half(value: float) -> float:
    return round(value * 2) / 2


def evaluate_submission_file(db: Session, submission_file_id: int) -> dict[str, Any]:
    """
    Orchestrate evaluation of a student SubmissionFile against its matched UnsolvedFile.

    Steps:
      1. Load SubmissionFile row.
      2. If matched_unsolved_file_id is None, return error immediately without calling Gemini.
      3. Load matched UnsolvedFile. If rubric_generated is False, generate rubric first.
      4. Parse submission .ipynb via parse_notebook_file; build text block of code + outputs.
      5. Build evaluation prompt, call Gemini, parse evaluation response.
      6. Return {"success": True, "total_score": float, "criteria": [...]} on success.
      7. On any failure, return {"success": False, "error": str} — never crash caller.
      Note: Does not write to Grade table (deferred to Sub-feature 5).
    """
    try:
        sub_file = db.get(SubmissionFile, submission_file_id)
        if sub_file is None:
            return {
                "success": False,
                "error": f"Submission file with id {submission_file_id} not found.",
            }

        # Matched assignment check
        if sub_file.matched_unsolved_file_id is None:
            return {
                "success": False,
                "error": "not matched to an assignment",
            }

        unsolved = db.get(UnsolvedFile, sub_file.matched_unsolved_file_id)
        if unsolved is None:
            return {
                "success": False,
                "error": f"Matched unsolved file {sub_file.matched_unsolved_file_id} not found.",
            }

        # Rubric generation check / fallback
        rubric: dict[str, Any] | None = None
        if unsolved.rubric_generated and unsolved.rubric_json:
            try:
                rubric = (
                    json.loads(unsolved.rubric_json)
                    if isinstance(unsolved.rubric_json, str)
                    else unsolved.rubric_json
                )
            except Exception as exc:
                logger.warning(
                    "Failed to decode cached rubric for file %d: %s. Regenerating.",
                    unsolved.id, exc,
                )

        if rubric is None or not rubric.get("criteria"):
            logger.info(
                "Rubric missing for unsolved file %d. Generating on the fly.",
                unsolved.id,
            )
            rubric_res = generate_rubric_for_unsolved_file(db, unsolved.id)
            if not rubric_res.get("success"):
                return {
                    "success": False,
                    "error": f"Failed to generate rubric: {rubric_res.get('error')}",
                }
            rubric = rubric_res["rubric"]

        unsolved_structure = extract_notebook_structure(str(absolute_path(unsolved.file_path)))
        if not unsolved_structure.get("valid"):
            return {
                "success": False,
                "error": f"Failed to parse unsolved notebook: {unsolved_structure.get('error')}",
            }

        # Parse submission notebook
        abs_ipynb_path = absolute_path(sub_file.extracted_ipynb_path)
        nb_parsed = parse_notebook_file(abs_ipynb_path)
        if not nb_parsed.get("valid"):
            return {
                "success": False,
                "error": f"Failed to parse submission notebook: {nb_parsed.get('error')}",
            }

        submission_structure = extract_notebook_structure(str(abs_ipynb_path))
        if not submission_structure.get("valid"):
            return {
                "success": False,
                "error": f"Failed to extract submission structure: {submission_structure.get('error')}",
            }
        _append_recorded_outputs(submission_structure["cells"], nb_parsed.get("code_cells", []))
        unsolved_parsed = parse_notebook_file(absolute_path(unsolved.file_path))
        if unsolved_parsed.get("valid"):
            _append_recorded_outputs(unsolved_structure["cells"], unsolved_parsed.get("code_cells", []))

        # Build prompt and invoke Gemini
        prompt = build_evaluation_prompt(
            rubric, unsolved_structure["cells"], submission_structure["cells"]
        )

        try:
            raw_eval_response = call_gemini_for_evaluation(prompt)
        except EvaluationError as exc:
            return {
                "success": False,
                "error": str(exc),
            }

        parsed_eval = parse_evaluation_response(raw_eval_response, rubric)
        if not parsed_eval["valid"]:
            return {
                "success": False,
                "error": parsed_eval["error"] or "Failed to parse Gemini evaluation response.",
            }

        logger.info(
            "Successfully evaluated submission_file %d: score %.2f/10 across %d criteria.",
            submission_file_id,
            parsed_eval["total_score"],
            len(parsed_eval["criteria"]),
        )

        return {
            "success": True,
            "total_score": parsed_eval["total_score"],
            "criteria": parsed_eval["criteria"],
        }

    except Exception as exc:
        logger.error(
            "Unexpected error in evaluate_submission_file(%d): %s",
            submission_file_id,
            exc,
            exc_info=True,
        )
        return {
            "success": False,
            "error": f"Unexpected error: {exc}",
        }


def _append_recorded_outputs(cells: list[dict[str, Any]], code_cells: list[dict[str, Any]]) -> None:
    """Append parsed outputs to matching code-cell content for prompt context."""
    code_iter = iter(code_cells)
    for cell in cells:
        if cell.get("type") != "code":
            continue
        parsed_code = next(code_iter, None)
        if not parsed_code:
            continue
        outputs = [str(item) for item in parsed_code.get("outputs", []) if str(item).strip()]
        if outputs:
            cell["content"] = f"{cell.get('content', '')}\n[recorded outputs]\n" + "\n".join(outputs)
