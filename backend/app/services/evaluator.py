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
from app.services.notebook import parse_notebook_file
from app.services.rubric import generate_rubric_for_unsolved_file
from app.services.storage import absolute_path

logger = logging.getLogger(__name__)


class EvaluationError(Exception):
    """Raised when Gemini API call fails during submission evaluation."""
    pass


def build_evaluation_prompt(rubric: dict[str, Any], code_and_outputs_text: str) -> str:
    """
    Build a prompt giving Gemini the rubric criteria and the student's actual
    code + cell outputs (plain text), instructing it to award points per criterion
    (never exceeding that criterion's points_possible) with a short explanation each.

    Instructs Gemini to respond with ONLY valid JSON matching the shape:
    {"criteria": [{"criterion": str, "points_possible": number, "points_awarded": number, "explanation": str}, ...]}
    """
    criteria_list = rubric.get("criteria", []) if isinstance(rubric, dict) else rubric
    rubric_formatted = json.dumps({"criteria": criteria_list}, indent=2)

    return (
        "You are an expert academic grading assistant. Evaluate the following student's "
        "Jupyter notebook submission against the provided rubric.\n\n"
        "Grading Instructions:\n"
        "1. For each criterion in the rubric, inspect the student's actual code and cell outputs.\n"
        "2. Award points ('points_awarded') for each criterion based on correctness and completeness.\n"
        "3. You must NEVER award more than that criterion's 'points_possible', and never award negative points.\n"
        "4. Provide a concise, constructive explanation for the points awarded on each criterion.\n"
        "5. Respond with ONLY valid JSON — absolutely no markdown fences (no ``` or ```json), "
        "and no conversational text before or after.\n\n"
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
        f"Student Submission (Code Cells and Outputs):\n{code_and_outputs_text.strip()}\n"
    )


def call_gemini_for_evaluation(prompt: str) -> str:
    """
    Call Gemini using GEMINI_API_KEY and GEMINI_FAST_MODEL from settings.
    Raises EvaluationError on failure — never crashes caller.
    """
    if not settings.gemini_api_key:
        raise EvaluationError("Gemini API key is not configured in settings.")

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_fast_model)
        response = model.generate_content(prompt)
        if not response or not response.text:
            raise EvaluationError("Gemini returned an empty response.")
        return response.text
    except Exception as exc:
        logger.warning("Gemini submission evaluation call failed: %s", exc)
        raise EvaluationError(f"Gemini call failed: {exc}") from exc


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

        # Clamp points awarded to [0, points_possible]
        points_awarded = max(0.0, min(points_possible, points_awarded))
        points_awarded = round(points_awarded, 2)

        explanation = str(matched.get("explanation", "")).strip()
        running_total += points_awarded

        validated_criteria.append({
            "criterion": expected_name,
            "points_possible": round(points_possible, 2),
            "points_awarded": points_awarded,
            "explanation": explanation,
        })

    # Clamp total score between 0.0 and 10.0
    total_score = round(max(0.0, min(10.0, running_total)), 2)

    return {
        "valid": True,
        "criteria": validated_criteria,
        "total_score": total_score,
        "error": None,
    }


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

        # Parse submission notebook
        abs_ipynb_path = absolute_path(sub_file.extracted_ipynb_path)
        nb_parsed = parse_notebook_file(abs_ipynb_path)
        if not nb_parsed.get("valid"):
            return {
                "success": False,
                "error": f"Failed to parse submission notebook: {nb_parsed.get('error')}",
            }

        # Build plain-text block of code cells and their execution outputs
        code_cells = nb_parsed.get("code_cells", [])
        if not code_cells:
            code_and_outputs_text = "(Notebook contains no code cells.)"
        else:
            blocks: list[str] = []
            for idx, cell in enumerate(code_cells, start=1):
                source = (cell.get("source") or "").strip()
                outputs = cell.get("outputs") or []
                output_str = "\n".join(str(out) for out in outputs if str(out).strip()).strip()

                cell_block = f"--- Cell {idx} [code] ---\n{source}"
                if output_str:
                    cell_block += f"\n[outputs]\n{output_str}"
                blocks.append(cell_block)
            code_and_outputs_text = "\n\n".join(blocks)

        # Build prompt and invoke Gemini
        prompt = build_evaluation_prompt(rubric, code_and_outputs_text)

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
