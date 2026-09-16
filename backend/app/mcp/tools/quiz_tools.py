"""
Post-7.8 Fix 3 (Prompt 3): quiz MCP tools.

Thin MCP wrappers over Phase 7.6's quiz generation and submission services.
No quiz logic lives here — each tool opens a database session, delegates to
the existing service functions, and returns results unaltered. Same
register(server) pattern as Phase 4 tools.

Quiz types
──────────
Supports the same scope types as the REST endpoints:
  - assignment_file: quiz on one unsolved file
  - session: quiz on one session's material
  - multiple_sessions: quiz across multiple sessions
  - topic: quiz on a free-text topic (retrieval-based)
  - uploaded_file: NOT SUPPORTED in MCP (would need bytes parameter)

The uploaded_file scope is omitted since MCP tool parameters can't handle
large file uploads cleanly — use the REST endpoint for that.
"""

import json
import logging

from mcp.server import MCPServer

from app.database import SessionLocal
from app.models.quiz_attempt import QuizAttempt
from app.models.user import User
from app.services import quiz_generator

logger = logging.getLogger(__name__)


def generate_quiz(
    student_id: int,
    scope_type: str,
    scope_detail: dict,
) -> dict:
    """
    Generate a 5-question practice quiz from course material.

    Supported scope types:
      - "assignment_file": scope_detail = {"unsolved_file_id": int}
      - "session": scope_detail = {"session_id": int}
      - "multiple_sessions": scope_detail = {"session_ids": [int, ...]} (2+)
      - "topic": scope_detail = {"topic_text": str}

    Returns the generated quiz attempt on success:
      {"id": int, "questions": [{"question": str, "options": [str, ...],
       "source_citation": str}, ...], "max_score": int, "created_at": str}

    Or an error dict:
      {"error": str, "error_type": "not_found" | "invalid_material" | "generation_failed"}

    Never raises: all errors surface as error dicts, matching Phase 4's pattern.
    The quiz is NOT submitted yet — use submit_quiz to score it.
    """
    db = SessionLocal()
    try:
        # Validate student exists
        student = db.get(User, student_id)
        if student is None or student.role != "student":
            return {"error": f"Student {student_id} not found", "error_type": "not_found"}

        # Call the service
        try:
            attempt = quiz_generator.generate_quiz(db, student_id, scope_type, scope_detail)
        except quiz_generator.QuizScopeNotFoundError as exc:
            return {"error": str(exc), "error_type": "not_found"}
        except quiz_generator.QuizMaterialError as exc:
            return {"error": str(exc), "error_type": "invalid_material"}
        except quiz_generator.QuizGenerationError as exc:
            return {"error": str(exc), "error_type": "generation_failed"}

        # Format response (same as router's _attempt_out, but dict not Pydantic)
        questions_data = json.loads(attempt.questions_json)
        result = {
            "id": attempt.id,
            "scope_type": attempt.scope_type,
            "scope_detail": json.loads(attempt.scope_detail),
            "questions": [
                {
                    "question": q["question"],
                    "options": q["options"],
                    "source_citation": q["source_citation"],
                }
                for q in questions_data
            ],
            "max_score": attempt.max_score,
            "created_at": attempt.created_at.isoformat(),
        }

    except Exception as exc:  # noqa: BLE001
        logger.error("MCP generate_quiz failed: %s", exc)
        return {"error": str(exc), "error_type": "generation_failed"}
    finally:
        db.close()

    logger.info(
        "MCP generate_quiz: student_id=%d scope_type=%s -> attempt_id=%d",
        student_id, scope_type, result["id"]
    )
    return result


def submit_quiz(attempt_id: int, student_id: int, answers: list[int]) -> dict:
    """
    Submit answers to a practice quiz and get the scored result.

    ``answers`` must be exactly 5 integers (0-3), one per question, in order.
    Returns the scored result on success:
      {"attempt_id": int, "score": int, "max_score": int, "questions": [
       {"question": str, "options": [str, ...], "correct_option_index": int,
        "student_answer_index": int, "is_correct": bool, "source_citation": str},
       ...], "submitted_at": str}

    Or an error dict:
      {"error": str, "error_type": "not_found" | "forbidden" | "already_submitted"}

    Never raises. A quiz can only be scored once — a second submit returns
    already_submitted error.
    """
    db = SessionLocal()
    try:
        # Validate student exists
        student = db.get(User, student_id)
        if student is None or student.role != "student":
            return {"error": f"Student {student_id} not found", "error_type": "not_found"}

        # Get attempt
        attempt = db.get(QuizAttempt, attempt_id)
        if attempt is None:
            return {"error": f"Quiz attempt {attempt_id} not found", "error_type": "not_found"}

        # Ownership check
        if attempt.student_id != student_id:
            return {
                "error": "You can only submit your own quiz attempts",
                "error_type": "forbidden",
            }

        # Submit
        try:
            quiz_generator.submit_quiz_attempt(db, attempt, answers)
        except quiz_generator.QuizAlreadySubmittedError:
            return {
                "error": "This quiz attempt was already submitted and can't be re-scored",
                "error_type": "already_submitted",
            }

        # Format result (same as router's _result_out, but dict not Pydantic)
        questions_data = json.loads(attempt.questions_json)
        answers_data = json.loads(attempt.student_answers_json)
        result = {
            "attempt_id": attempt.id,
            "scope_type": attempt.scope_type,
            "scope_detail": json.loads(attempt.scope_detail),
            "score": attempt.score,
            "max_score": attempt.max_score,
            "questions": [
                {
                    "question": q["question"],
                    "options": q["options"],
                    "source_citation": q["source_citation"],
                    "correct_option_index": q["correct_option_index"],
                    "student_answer_index": a,
                    "is_correct": q["correct_option_index"] == a,
                }
                for q, a in zip(questions_data, answers_data)
            ],
            "created_at": attempt.created_at.isoformat(),
            "submitted_at": attempt.submitted_at.isoformat(),
        }

    except Exception as exc:  # noqa: BLE001
        logger.error("MCP submit_quiz failed: %s", exc)
        return {"error": str(exc), "error_type": "submission_failed"}
    finally:
        db.close()

    logger.info(
        "MCP submit_quiz: attempt_id=%d student_id=%d -> score=%d/%d",
        attempt_id, student_id, result["score"], result["max_score"]
    )
    return result


def register(server: MCPServer) -> None:
    """Register this module's tools on *server*."""
    server.add_tool(
        generate_quiz,
        name="generate_quiz",
        description=(
            "Generate a 5-question practice quiz from course material. Scope "
            "types: assignment_file (one file), session (one session), "
            "multiple_sessions (2+ sessions), or topic (free-text). Returns "
            "the quiz attempt with questions. NOT submitted yet — use "
            "submit_quiz to score it. Never affects real grades."
        ),
    )
    server.add_tool(
        submit_quiz,
        name="submit_quiz",
        description=(
            "Submit answers to a practice quiz and get the scored result. "
            "Answers must be exactly 5 integers (0-3), one per question. "
            "Can only be scored once — second submit returns error. Never "
            "affects real grades."
        ),
    )
