"""
Tests for backend/app/services/feedback.py — Phase 2, Sub-feature 5.

All Gemini API calls are mocked so no real API quota is consumed.

Cases covered
─────────────
1. build_feedback_text
   - Correct opening line format ("Score: X/10.")
   - One line per criterion in order with correct format
   - Empty criteria list returns only the opening score line
   - Zero score formatted correctly

2. build_rationale_json
   - Valid criteria list passes through with correct types
   - Raises ValueError for non-list input
   - Raises ValueError when a required key is missing
   - Raises ValueError for non-string criterion name
   - Raises ValueError for non-numeric points values
   - Raises ValueError for non-string explanation
   - Empty list returns empty list (valid)

3. persist_grade
   - Creates a new Grade row when none exists
   - Sets SubmissionFile.graded = True after persistence
   - Grade row contains correct score, feedback_text, rationale_json
   - Re-grading the same submission_file_id OVERWRITES (no duplicate row)
   - Overwrite updates score, feedback_text, rationale_json, graded_at
   - Returns dict with grade_id, score, feedback_text

4. generate_feedback_and_persist
   - Successful evaluation creates and returns grade with success=True
   - Failed evaluation (unmatched file) returns success=False and NO Grade row created
   - Failed evaluation (Gemini error) returns success=False and NO Grade row created
   - SubmissionFile.graded is False before and True after successful persist

5. TEMP endpoint (POST /api/v1/sessions/{session_id}/submissions/files/{id}/evaluate)
   - Instructor receives success response with grade_id, score, feedback_text
   - Student receives 403 Forbidden
   - Unauthenticated request receives 401
   - Non-existent submission file returns 404
   - Submission file in wrong session returns 404
   - Re-calling the endpoint for the same file returns success (overwrite, not error)
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.grade import Grade
from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.submission_file import SubmissionFile
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole
from app.services.auth import create_access_token
from app.services.feedback import (
    build_feedback_text,
    build_rationale_json,
    generate_feedback_and_persist,
    persist_grade,
)


# ===========================================================================
# Shared fixtures
# ===========================================================================

@pytest.fixture()
def db():
    """Fresh in-memory SQLite database using the real ORM models."""
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def seeded_db(db):
    """
    DB with instructor, student, session, unsolved file with rubric,
    a submission, and two SubmissionFile rows:
      - sf_matched (id=300): matched_unsolved_file_id=100
      - sf_unmatched (id=301): matched_unsolved_file_id=None
    """
    rubric_data = {
        "criteria": [
            {"criterion": "Data Preprocessing", "points_possible": 3.0},
            {"criterion": "Model Training", "points_possible": 4.0},
            {"criterion": "Evaluation and Metrics", "points_possible": 3.0},
        ]
    }
    db.add_all([
        User(id=1, name="Prof T", email="prof@t.com", hashed_password="h", role=UserRole.instructor),
        User(id=2, name="Ada L", email="ada@t.com", hashed_password="h", role=UserRole.student),
        LMSSession(id=10, title="CS101"),
        UnsolvedFile(
            id=100, session_id=10, original_filename="hw1.ipynb",
            file_path="10/assignments/hw1.ipynb",
            parsed_requirements_text="Implement model training.",
            rubric_json=json.dumps(rubric_data),
        ),
        Submission(
            id=200, session_id=10, student_id=2,
            original_filename="hw1_sub.ipynb",
            uploaded_file_path="10/submissions/2/hw1_sub.ipynb",
        ),
        SubmissionFile(
            id=300, submission_id=200,
            matched_unsolved_file_id=100,
            original_filename="hw1_sub.ipynb",
            extracted_ipynb_path="10/submissions/2/hw1_sub.ipynb",
        ),
        SubmissionFile(
            id=301, submission_id=200,
            matched_unsolved_file_id=None,
            original_filename="extra.ipynb",
            extracted_ipynb_path="10/submissions/2/extra.ipynb",
        ),
    ])
    db.commit()
    return db


# Shared evaluation result used across multiple tests
GOOD_EVAL_RESULT = {
    "success": True,
    "total_score": 8.5,
    "criteria": [
        {
            "criterion": "Data Preprocessing",
            "points_possible": 3.0,
            "points_awarded": 2.5,
            "explanation": "Missing one normalisation step.",
        },
        {
            "criterion": "Model Training",
            "points_possible": 4.0,
            "points_awarded": 3.5,
            "explanation": "Model trained correctly with good hyperparameters.",
        },
        {
            "criterion": "Evaluation and Metrics",
            "points_possible": 3.0,
            "points_awarded": 2.5,
            "explanation": "Accuracy reported but confusion matrix missing.",
        },
    ],
}


# ===========================================================================
# 1. build_feedback_text
# ===========================================================================

class TestBuildFeedbackText:

    def test_opening_line_format(self):
        result = build_feedback_text({"total_score": 7.5, "criteria": []})
        assert result.startswith("Score: 7.5/10.")

    def test_one_line_per_criterion_in_order(self):
        text = build_feedback_text(GOOD_EVAL_RESULT)
        lines = text.splitlines()
        assert lines[0] == "Score: 8.5/10."
        assert lines[1] == "- Data Preprocessing: 2.5/3.0 — Missing one normalisation step."
        assert lines[2] == "- Model Training: 3.5/4.0 — Model trained correctly with good hyperparameters."
        assert lines[3] == "- Evaluation and Metrics: 2.5/3.0 — Accuracy reported but confusion matrix missing."

    def test_empty_criteria_returns_only_score_line(self):
        text = build_feedback_text({"total_score": 5.0, "criteria": []})
        assert text == "Score: 5.0/10."
        assert "\n" not in text

    def test_zero_score_formatted_correctly(self):
        text = build_feedback_text({"total_score": 0.0, "criteria": []})
        assert text == "Score: 0.0/10."

    def test_criterion_order_preserved(self):
        eval_result = {
            "total_score": 10.0,
            "criteria": [
                {"criterion": "Z Criterion", "points_possible": 5.0, "points_awarded": 5.0, "explanation": "Perfect."},
                {"criterion": "A Criterion", "points_possible": 5.0, "points_awarded": 5.0, "explanation": "Also perfect."},
            ],
        }
        text = build_feedback_text(eval_result)
        lines = text.splitlines()
        assert "Z Criterion" in lines[1]
        assert "A Criterion" in lines[2]


# ===========================================================================
# 2. build_rationale_json
# ===========================================================================

class TestBuildRationaleJson:

    def test_valid_criteria_passthrough(self):
        result = build_rationale_json(GOOD_EVAL_RESULT)
        assert len(result) == 3
        assert result[0]["criterion"] == "Data Preprocessing"
        assert result[0]["points_possible"] == 3.0
        assert result[0]["points_awarded"] == 2.5
        assert result[0]["explanation"] == "Missing one normalisation step."

    def test_types_are_correct(self):
        result = build_rationale_json(GOOD_EVAL_RESULT)
        for entry in result:
            assert isinstance(entry["criterion"], str)
            assert isinstance(entry["points_possible"], float)
            assert isinstance(entry["points_awarded"], float)
            assert isinstance(entry["explanation"], str)

    def test_empty_criteria_returns_empty_list(self):
        result = build_rationale_json({"total_score": 0.0, "criteria": []})
        assert result == []

    def test_raises_on_non_list_criteria(self):
        with pytest.raises(ValueError, match="must be a list"):
            build_rationale_json({"criteria": "not a list"})

    def test_raises_on_missing_key(self):
        bad_result = {
            "criteria": [
                {"criterion": "C1", "points_possible": 5.0, "points_awarded": 3.0},
                # missing "explanation"
            ]
        }
        with pytest.raises(ValueError, match="explanation"):
            build_rationale_json(bad_result)

    def test_raises_on_empty_criterion_name(self):
        bad_result = {
            "criteria": [
                {"criterion": "  ", "points_possible": 5.0, "points_awarded": 3.0, "explanation": "ok"},
            ]
        }
        with pytest.raises(ValueError, match="non-empty string"):
            build_rationale_json(bad_result)

    def test_raises_on_non_numeric_points(self):
        bad_result = {
            "criteria": [
                {"criterion": "C1", "points_possible": "five", "points_awarded": 3.0, "explanation": "ok"},
            ]
        }
        with pytest.raises(ValueError, match="numeric"):
            build_rationale_json(bad_result)

    def test_raises_on_non_string_explanation(self):
        bad_result = {
            "criteria": [
                {"criterion": "C1", "points_possible": 5.0, "points_awarded": 3.0, "explanation": 42},
            ]
        }
        with pytest.raises(ValueError, match="explanation.*string"):
            build_rationale_json(bad_result)

    def test_raises_on_non_dict_entry(self):
        bad_result = {"criteria": ["not a dict"]}
        with pytest.raises(ValueError, match="dict"):
            build_rationale_json(bad_result)


# ===========================================================================
# 3. persist_grade
# ===========================================================================

class TestPersistGrade:

    def test_creates_new_grade_row(self, seeded_db):
        result = persist_grade(seeded_db, 300, GOOD_EVAL_RESULT)

        assert result["grade_id"] is not None
        assert result["score"] == 8.5
        assert result["feedback_text"].startswith("Score: 8.5/10.")

        grade = seeded_db.query(Grade).filter(Grade.submission_file_id == 300).first()
        assert grade is not None
        assert grade.score == 8.5

    def test_sets_submission_file_graded_true(self, seeded_db):
        sf = seeded_db.get(SubmissionFile, 300)
        assert sf.graded is False

        persist_grade(seeded_db, 300, GOOD_EVAL_RESULT)
        seeded_db.refresh(sf)

        assert sf.graded is True

    def test_grade_row_has_correct_fields(self, seeded_db):
        persist_grade(seeded_db, 300, GOOD_EVAL_RESULT)

        grade = seeded_db.query(Grade).filter(Grade.submission_file_id == 300).first()
        assert grade.score == 8.5
        assert "Score: 8.5/10." in grade.feedback_text
        assert "Data Preprocessing" in grade.feedback_text

        rationale = json.loads(grade.rationale_json)
        assert len(rationale) == 3
        assert rationale[0]["criterion"] == "Data Preprocessing"
        assert rationale[0]["points_awarded"] == 2.5
        assert rationale[0]["points_possible"] == 3.0
        assert "normalisation" in rationale[0]["explanation"]

    def test_rationale_json_has_all_four_keys_per_entry(self, seeded_db):
        persist_grade(seeded_db, 300, GOOD_EVAL_RESULT)

        grade = seeded_db.query(Grade).filter(Grade.submission_file_id == 300).first()
        rationale = json.loads(grade.rationale_json)
        for entry in rationale:
            assert "criterion" in entry
            assert "points_possible" in entry
            assert "points_awarded" in entry
            assert "explanation" in entry

    def test_regrade_overwrites_existing_row_no_duplicate(self, seeded_db):
        # First grade
        persist_grade(seeded_db, 300, GOOD_EVAL_RESULT)
        first_count = seeded_db.query(Grade).filter(Grade.submission_file_id == 300).count()
        assert first_count == 1

        # Re-grade with different score
        new_result = {**GOOD_EVAL_RESULT, "total_score": 6.0}
        persist_grade(seeded_db, 300, new_result)
        second_count = seeded_db.query(Grade).filter(Grade.submission_file_id == 300).count()
        assert second_count == 1  # still exactly one row, not two

        grade = seeded_db.query(Grade).filter(Grade.submission_file_id == 300).first()
        assert grade.score == 6.0  # overwritten

    def test_regrade_updates_feedback_and_rationale(self, seeded_db):
        persist_grade(seeded_db, 300, GOOD_EVAL_RESULT)

        updated_result = {
            "success": True,
            "total_score": 5.0,
            "criteria": [
                {
                    "criterion": "Data Preprocessing",
                    "points_possible": 3.0,
                    "points_awarded": 1.5,
                    "explanation": "Significant preprocessing issues.",
                },
                {
                    "criterion": "Model Training",
                    "points_possible": 4.0,
                    "points_awarded": 2.0,
                    "explanation": "Model did not converge.",
                },
                {
                    "criterion": "Evaluation and Metrics",
                    "points_possible": 3.0,
                    "points_awarded": 1.5,
                    "explanation": "Incomplete evaluation.",
                },
            ],
        }
        persist_grade(seeded_db, 300, updated_result)

        grade = seeded_db.query(Grade).filter(Grade.submission_file_id == 300).first()
        assert "Score: 5.0/10." in grade.feedback_text
        assert "Significant preprocessing issues." in grade.feedback_text
        rationale = json.loads(grade.rationale_json)
        assert rationale[0]["points_awarded"] == 1.5

    def test_returns_dict_with_required_keys(self, seeded_db):
        result = persist_grade(seeded_db, 300, GOOD_EVAL_RESULT)
        assert "grade_id" in result
        assert "score" in result
        assert "feedback_text" in result
        assert isinstance(result["grade_id"], int)
        assert isinstance(result["score"], float)
        assert isinstance(result["feedback_text"], str)

    def test_graded_at_is_recent_utc(self, seeded_db):
        before = datetime.now(timezone.utc)
        persist_grade(seeded_db, 300, GOOD_EVAL_RESULT)
        after = datetime.now(timezone.utc)

        grade = seeded_db.query(Grade).filter(Grade.submission_file_id == 300).first()
        # graded_at must fall within the test window
        assert before <= grade.graded_at.replace(tzinfo=timezone.utc) <= after


# ===========================================================================
# 4. generate_feedback_and_persist
# ===========================================================================

# evaluate_submission_file calls extract_notebook_structure on the real file
# path before reaching Gemini; patch it here (same pattern as test_evaluator.py)
# so tests don't require real .ipynb files on disk.
_MOCK_STRUCTURE = {
    "valid": True,
    "cells": [
        {"type": "markdown", "content": "Answer the question below.", "heuristic_hint": False},
        {"type": "code", "content": "# answer here", "heuristic_hint": True},
    ],
    "error": None,
}


class TestGenerateFeedbackAndPersist:

    @pytest.fixture(autouse=True)
    def mock_notebook_structures(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.evaluator.extract_notebook_structure",
            lambda _path: _MOCK_STRUCTURE,
        )

    def test_successful_evaluation_creates_grade(self, seeded_db):
        mock_nb = {"valid": True, "code_cells": [{"source": "x = 1", "outputs": []}]}
        gemini_response = json.dumps({
            "criteria": [
                {"criterion": "Data Preprocessing", "points_possible": 3.0, "points_awarded": 3.0, "explanation": "Clean"},
                {"criterion": "Model Training", "points_possible": 4.0, "points_awarded": 4.0, "explanation": "Trained"},
                {"criterion": "Evaluation and Metrics", "points_possible": 3.0, "points_awarded": 3.0, "explanation": "Evaluated"},
            ]
        })

        with patch("app.services.evaluator.parse_notebook_file", return_value=mock_nb):
            with patch("app.services.evaluator.call_gemini_for_evaluation", return_value=gemini_response):
                result = generate_feedback_and_persist(seeded_db, submission_file_id=300)

        assert result["success"] is True
        assert "grade_id" in result
        assert result["score"] == 10.0
        assert "Score: 10.0/10." in result["feedback_text"]

        # Grade row must exist in DB
        grade = seeded_db.query(Grade).filter(Grade.submission_file_id == 300).first()
        assert grade is not None
        assert grade.score == 10.0

    def test_unmatched_file_returns_failure_no_grade_row(self, seeded_db):
        """
        SubmissionFile 301 has matched_unsolved_file_id=None.
        evaluate_submission_file returns success=False immediately.
        No Grade row should be created.
        """
        result = generate_feedback_and_persist(seeded_db, submission_file_id=301)

        assert result["success"] is False
        assert result["error"] == "not matched to an assignment"

        grade = seeded_db.query(Grade).filter(Grade.submission_file_id == 301).first()
        assert grade is None

    def test_gemini_error_returns_failure_no_grade_row(self, seeded_db):
        from app.services.evaluator import EvaluationError

        mock_nb = {"valid": True, "code_cells": [{"source": "x = 1", "outputs": []}]}

        with patch("app.services.evaluator.parse_notebook_file", return_value=mock_nb):
            with patch(
                "app.services.evaluator.call_gemini_for_evaluation",
                side_effect=EvaluationError("Quota exceeded"),
            ):
                result = generate_feedback_and_persist(seeded_db, submission_file_id=300)

        assert result["success"] is False
        assert "Quota exceeded" in result["error"]

        grade = seeded_db.query(Grade).filter(Grade.submission_file_id == 300).first()
        assert grade is None

    def test_submission_file_graded_false_before_true_after(self, seeded_db):
        sf = seeded_db.get(SubmissionFile, 300)
        assert sf.graded is False

        mock_nb = {"valid": True, "code_cells": [{"source": "x = 1", "outputs": []}]}
        gemini_response = json.dumps({
            "criteria": [
                {"criterion": "Data Preprocessing", "points_possible": 3.0, "points_awarded": 2.5, "explanation": "ok"},
                {"criterion": "Model Training", "points_possible": 4.0, "points_awarded": 3.5, "explanation": "ok"},
                {"criterion": "Evaluation and Metrics", "points_possible": 3.0, "points_awarded": 2.5, "explanation": "ok"},
            ]
        })

        with patch("app.services.evaluator.parse_notebook_file", return_value=mock_nb):
            with patch("app.services.evaluator.call_gemini_for_evaluation", return_value=gemini_response):
                generate_feedback_and_persist(seeded_db, submission_file_id=300)

        seeded_db.refresh(sf)
        assert sf.graded is True

    def test_nonexistent_submission_file_returns_failure(self, seeded_db):
        result = generate_feedback_and_persist(seeded_db, submission_file_id=99999)
        assert result["success"] is False
        assert "not found" in result["error"]

        # Definitely no grade row
        grade = seeded_db.query(Grade).filter(Grade.submission_file_id == 99999).first()
        assert grade is None


# ===========================================================================
# 5. TEMP endpoint via TestClient
# ===========================================================================

@pytest.fixture()
def client_with_data(db):
    """
    FastAPI TestClient backed by in-memory DB.
    Seeded with instructor, student, session, unsolved file with rubric,
    and a matched SubmissionFile ready for evaluation.
    """
    from app.database import get_db
    from app.main import app

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    rubric_data = {
        "criteria": [
            {"criterion": "Code Quality", "points_possible": 10.0},
        ]
    }
    db.add_all([
        User(id=1, name="Prof T", email="prof@t.com", hashed_password="h", role=UserRole.instructor),
        User(id=2, name="Ada L", email="ada@t.com", hashed_password="h", role=UserRole.student),
        LMSSession(id=10, title="CS101"),
        UnsolvedFile(
            id=100, session_id=10, original_filename="hw1.ipynb",
            file_path="10/assignments/hw1.ipynb",
            parsed_requirements_text="Implement model.",
            rubric_json=json.dumps(rubric_data),
        ),
        Submission(
            id=200, session_id=10, student_id=2,
            original_filename="hw1_sub.ipynb",
            uploaded_file_path="10/submissions/2/hw1_sub.ipynb",
        ),
        SubmissionFile(
            id=300, submission_id=200,
            matched_unsolved_file_id=100,
            original_filename="hw1_sub.ipynb",
            extracted_ipynb_path="10/submissions/2/hw1_sub.ipynb",
        ),
    ])
    db.commit()

    instructor_token = create_access_token(1, UserRole.instructor)
    student_token = create_access_token(2, UserRole.student)

    with TestClient(app) as c:
        yield c, instructor_token, student_token, db

    app.dependency_overrides.clear()


# Shared mock for a successful grade-and-persist result
_MOCK_PERSIST_RESULT = {
    "success": True,
    "grade_id": 1,
    "score": 9.0,
    "feedback_text": "Score: 9.0/10.\n- Code Quality: 9.0/10.0 — Excellent code.",
}


class TestTempEvaluateEndpoint:

    def test_unauthenticated_returns_401(self, client_with_data):
        c, *_ = client_with_data
        res = c.post("/api/v1/sessions/10/submissions/files/300/evaluate")
        assert res.status_code == 401

    def test_student_returns_403(self, client_with_data):
        c, _, student_token, *_ = client_with_data
        res = c.post(
            "/api/v1/sessions/10/submissions/files/300/evaluate",
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert res.status_code == 403

    def test_nonexistent_submission_file_returns_404(self, client_with_data):
        c, instructor_token, *_ = client_with_data
        res = c.post(
            "/api/v1/sessions/10/submissions/files/999/evaluate",
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert res.status_code == 404

    def test_submission_file_in_wrong_session_returns_404(self, client_with_data):
        c, instructor_token, _, db = client_with_data
        # Add a second session and a submission file belonging to it
        db.add(LMSSession(id=99, title="Other Session"))
        db.add(Submission(
            id=999, session_id=99, student_id=2,
            original_filename="other.ipynb",
            uploaded_file_path="99/submissions/2/other.ipynb",
        ))
        db.add(SubmissionFile(
            id=999, submission_id=999,
            matched_unsolved_file_id=None,
            original_filename="other.ipynb",
            extracted_ipynb_path="99/submissions/2/other.ipynb",
        ))
        db.commit()

        # File 999 belongs to session 99, not session 10
        res = c.post(
            "/api/v1/sessions/10/submissions/files/999/evaluate",
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert res.status_code == 404

    def test_instructor_receives_success_response_with_grade(self, client_with_data):
        c, instructor_token, *_ = client_with_data

        with patch(
            "app.services.feedback.generate_feedback_and_persist",
            return_value=_MOCK_PERSIST_RESULT,
        ):
            res = c.post(
                "/api/v1/sessions/10/submissions/files/300/evaluate",
                headers={"Authorization": f"Bearer {instructor_token}"},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["grade_id"] == 1
        assert data["score"] == 9.0
        assert "Score: 9.0/10." in data["feedback_text"]

    def test_regrading_same_file_returns_success_not_error(self, client_with_data):
        """
        Calling the endpoint twice for the same submission_file_id must succeed
        both times — the second call overwrites the first grade.
        """
        c, instructor_token, *_ = client_with_data

        with patch(
            "app.services.feedback.generate_feedback_and_persist",
            return_value=_MOCK_PERSIST_RESULT,
        ):
            res1 = c.post(
                "/api/v1/sessions/10/submissions/files/300/evaluate",
                headers={"Authorization": f"Bearer {instructor_token}"},
            )
            res2 = c.post(
                "/api/v1/sessions/10/submissions/files/300/evaluate",
                headers={"Authorization": f"Bearer {instructor_token}"},
            )

        assert res1.status_code == 200
        assert res2.status_code == 200
        assert res2.json()["success"] is True

    def test_grade_report_shows_real_data_after_grading(self, client_with_data):
        """
        After a successful evaluate call, GET /grades returns the real
        score and feedback instead of an empty students list.
        Verifies the Phase 1 grade-report endpoint is fully wired.
        """
        c, instructor_token, *_ = client_with_data

        # Confirm empty before grading
        res_before = c.get(
            "/api/v1/sessions/10/grades",
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert res_before.status_code == 200
        before_data = res_before.json()
        # Student has a submission but no grades yet
        student_summary = next(
            (s for s in before_data["students"] if s["student_id"] == 2), None
        )
        assert student_summary is not None
        assert student_summary["combined_score"] is None
        assert student_summary["per_file"] == []

        # Grade the submission
        with patch(
            "app.services.feedback.generate_feedback_and_persist",
            return_value=_MOCK_PERSIST_RESULT,
        ) as mock_fn:
            # Bypass the mock to actually call the real persist path
            mock_fn.side_effect = lambda db, sfid: _real_persist(db, sfid)
            c.post(
                "/api/v1/sessions/10/submissions/files/300/evaluate",
                headers={"Authorization": f"Bearer {instructor_token}"},
            )

        # Confirm populated after grading
        res_after = c.get(
            "/api/v1/sessions/10/grades",
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert res_after.status_code == 200
        after_data = res_after.json()
        student_summary_after = next(
            (s for s in after_data["students"] if s["student_id"] == 2), None
        )
        assert student_summary_after is not None
        assert student_summary_after["combined_score"] is not None
        assert len(student_summary_after["per_file"]) == 1
        per_file = student_summary_after["per_file"][0]
        assert per_file["score"] == _MOCK_PERSIST_RESULT["score"]
        assert per_file["feedback_text"] == _MOCK_PERSIST_RESULT["feedback_text"]


def _real_persist(db, submission_file_id: int) -> dict:
    """Helper: bypasses evaluate_submission_file, calls persist_grade directly."""
    eval_result = {
        "success": True,
        "total_score": _MOCK_PERSIST_RESULT["score"],
        "criteria": [
            {
                "criterion": "Code Quality",
                "points_possible": 10.0,
                "points_awarded": _MOCK_PERSIST_RESULT["score"],
                "explanation": "Excellent code.",
            }
        ],
    }
    result = persist_grade(db, submission_file_id, eval_result)
    return {
        "success": True,
        "grade_id": result["grade_id"],
        "score": result["score"],
        "feedback_text": result["feedback_text"],
    }
