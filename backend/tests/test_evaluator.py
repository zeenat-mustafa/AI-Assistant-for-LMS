"""
Tests for backend/app/services/evaluator.py — Phase 2, Sub-feature 4.

All Gemini API calls are mocked using unittest.mock to prevent consuming real API quota.

Cases covered
─────────────
1. build_evaluation_prompt
   - Formats rubric criteria names and points_possible.
   - Formats student code cells and execution outputs in plain text.
   - Instructs Gemini on JSON structure and no markdown fences.

2. call_gemini_for_evaluation
   - Successful call returns response text.
   - Missing API key raises EvaluationError.
   - Exception during Gemini call raises EvaluationError.

3. parse_evaluation_response
   - Plain JSON string with valid criteria.
   - Code fence stripping (```json ... ``` and ``` ... ```).
   - Points clamped when Gemini overshoots criterion points_possible.
   - Points clamped when Gemini provides negative points.
   - Total score clamped to [0, 10].
   - Criteria count mismatch returns valid=False.
   - Missing rubric criteria returns valid=False.
   - Malformed JSON string returns valid=False.
   - Empty response returns valid=False.

4. evaluate_submission_file
   - Successful evaluation with mocked Gemini response.
   - Unmatched file (matched_unsolved_file_id is None): returns error immediately
     and verifies NO Gemini call is made.
   - Rubric auto-generated on the fly if rubric is missing on matched assignment.
   - Malformed Gemini response returns error without crashing.
   - Gemini API exception returns error without crashing.
   - Nonexistent submission file returns error without crashing.

5. Temporary API Endpoint (POST /api/v1/sessions/{session_id}/submissions/files/{submission_file_id}/evaluate)
   - Instructor can trigger evaluation.
   - Student receives 403 Forbidden.
   - Unauthenticated request receives 401 Unauthorized.
   - Non-existent submission file returns 404.
   - Submission file belonging to another session returns 404.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.submission_file import SubmissionFile
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole
from app.services.auth import create_access_token
from app.services.evaluator import (
    EvaluationError,
    build_evaluation_prompt,
    call_gemini_for_evaluation,
    evaluate_submission_file,
    parse_evaluation_response,
)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture()
def db():
    """In-memory SQLite database for isolated service tests."""
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
    DB with an instructor, student, session, unsolved file with rubric,
    and a submission with matched and unmatched submission files.
    """
    instructor = User(
        id=1,
        name="Prof Turing",
        email="prof@test.com",
        hashed_password="hash",
        role=UserRole.instructor,
    )
    student = User(
        id=2,
        name="Ada Lovelace",
        email="ada@test.com",
        hashed_password="hash",
        role=UserRole.student,
    )
    lms_session = LMSSession(
        id=10,
        title="CS101 Intro to AI",
    )
    rubric_data = {
        "criteria": [
            {"criterion": "Data Preprocessing", "points_possible": 3.0},
            {"criterion": "Model Training", "points_possible": 4.0},
            {"criterion": "Evaluation and Metrics", "points_possible": 3.0},
        ]
    }
    unsolved = UnsolvedFile(
        id=100,
        session_id=10,
        original_filename="hw1.ipynb",
        file_path="10/assignments/hw1.ipynb",
        parsed_requirements_text="Implement model training and evaluation.",
        rubric_json=json.dumps(rubric_data),
    )
    submission = Submission(
        id=200,
        session_id=10,
        student_id=2,
        original_filename="hw1_submission.ipynb",
        uploaded_file_path="10/submissions/2/hw1_submission.ipynb",
    )
    # Matched file
    sub_file_matched = SubmissionFile(
        id=300,
        submission_id=200,
        matched_unsolved_file_id=100,
        original_filename="hw1_submission.ipynb",
        extracted_ipynb_path="10/submissions/2/hw1_submission.ipynb",
    )
    # Unmatched file
    sub_file_unmatched = SubmissionFile(
        id=301,
        submission_id=200,
        matched_unsolved_file_id=None,
        original_filename="extra.ipynb",
        extracted_ipynb_path="10/submissions/2/extra.ipynb",
    )

    db.add_all([
        instructor,
        student,
        lms_session,
        unsolved,
        submission,
        sub_file_matched,
        sub_file_unmatched,
    ])
    db.commit()
    return db


@pytest.fixture(autouse=True)
def mock_notebook_structures(monkeypatch):
    monkeypatch.setattr(
        "app.services.evaluator.extract_notebook_structure",
        lambda _path: {
            "valid": True,
            "cells": [
                {"type": "markdown", "content": "Answer the question below.", "heuristic_hint": False},
                {"type": "code", "content": "# answer here", "heuristic_hint": True},
            ],
            "error": None,
        },
    )


# ===========================================================================
# 1. Prompt Building
# ===========================================================================

def test_build_evaluation_prompt():
    rubric = {
        "criteria": [
            {"criterion": "Model Accuracy", "points_possible": 5.0},
            {"criterion": "Code Quality", "points_possible": 5.0},
        ]
    }
    unsolved_cells = [{"type": "code", "content": "import torch", "heuristic_hint": False}]
    submission_cells = [{"type": "code", "content": "import torch\n[recorded outputs]\n1.0", "heuristic_hint": False}]
    prompt = build_evaluation_prompt(rubric, unsolved_cells, submission_cells)

    assert "Model Accuracy" in prompt
    assert "Code Quality" in prompt
    assert "import torch" in prompt
    assert "points_awarded" in prompt
    assert "ONLY valid JSON" in prompt


# ===========================================================================
# 2. Gemini Calling
# ===========================================================================

def test_call_gemini_missing_api_key():
    with patch("app.services.evaluator.settings.gemini_api_key", ""):
        with pytest.raises(EvaluationError, match="Gemini API key is not configured"):
            call_gemini_for_evaluation("Prompt")


def test_call_gemini_success():
    mock_resp = MagicMock()
    mock_resp.text = '{"criteria": []}'

    with patch("app.services.evaluator.settings.gemini_api_key", "fake-key"):
        with patch("google.generativeai.GenerativeModel") as mock_model_cls:
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_resp
            mock_model_cls.return_value = mock_model

            result = call_gemini_for_evaluation("Prompt")
            assert result == '{"criteria": []}'


def test_call_gemini_failure_raises_evaluation_error():
    with patch("app.services.evaluator.settings.gemini_api_key", "fake-key"):
        with patch("google.generativeai.GenerativeModel") as mock_model_cls:
            mock_model = MagicMock()
            mock_model.generate_content.side_effect = RuntimeError("Service Unavailable")
            mock_model_cls.return_value = mock_model

            with pytest.raises(EvaluationError, match="Gemini call failed"):
                call_gemini_for_evaluation("Prompt")


# ===========================================================================
# 3. Response Parsing and Clamping
# ===========================================================================

def test_parse_evaluation_response_clean_json():
    rubric = {
        "criteria": [
            {"criterion": "Data Preprocessing", "points_possible": 3.0},
            {"criterion": "Model Training", "points_possible": 7.0},
        ]
    }
    raw_json = json.dumps({
        "criteria": [
            {
                "criterion": "Data Preprocessing",
                "points_possible": 3.0,
                "points_awarded": 2.5,
                "explanation": "Missing one column normalization.",
            },
            {
                "criterion": "Model Training",
                "points_possible": 7.0,
                "points_awarded": 6.5,
                "explanation": "Trained accurately with high F1 score.",
            },
        ]
    })
    parsed = parse_evaluation_response(raw_json, rubric)

    assert parsed["valid"] is True
    assert parsed["error"] is None
    assert parsed["total_score"] == 9.0
    assert len(parsed["criteria"]) == 2
    assert parsed["criteria"][0]["points_awarded"] == 2.5
    assert parsed["criteria"][1]["points_awarded"] == 6.5


def test_parse_evaluation_response_rounds_awards_and_total_to_half_points():
    rubric = {"criteria": [
        {"criterion": "A", "points_possible": 5.0},
        {"criterion": "B", "points_possible": 5.0},
    ]}
    raw = json.dumps({"criteria": [
        {"criterion": "A", "points_awarded": 2.26, "explanation": "x"},
        {"criterion": "B", "points_awarded": 4.74, "explanation": "y"},
    ]})
    parsed = parse_evaluation_response(raw, rubric)
    assert parsed["valid"] is True
    assert [row["points_awarded"] for row in parsed["criteria"]] == [2.5, 4.5]
    assert parsed["total_score"] == 7.0


def test_parse_evaluation_response_with_markdown_fences():
    rubric = {
        "criteria": [
            {"criterion": "Task 1", "points_possible": 10.0},
        ]
    }
    raw_fenced = (
        "```json\n"
        "{\n"
        '  "criteria": [\n'
        '    {"criterion": "Task 1", "points_possible": 10.0, "points_awarded": 8.0, "explanation": "Well done."}\n'
        "  ]\n"
        "}\n"
        "```"
    )
    parsed = parse_evaluation_response(raw_fenced, rubric)

    assert parsed["valid"] is True
    assert parsed["total_score"] == 8.0
    assert parsed["criteria"][0]["points_awarded"] == 8.0
    assert parsed["criteria"][0]["explanation"] == "Well done."


def test_parse_evaluation_response_points_clamped_if_gemini_overshoots():
    rubric = {
        "criteria": [
            {"criterion": "Data Cleaning", "points_possible": 3.0},
            {"criterion": "Model Accuracy", "points_possible": 7.0},
        ]
    }
    # Gemini awards 5.0 on a 3.0 point criterion and 9.5 on a 7.0 point criterion
    raw_json = json.dumps({
        "criteria": [
            {
                "criterion": "Data Cleaning",
                "points_possible": 3.0,
                "points_awarded": 5.0,
                "explanation": "Extra credit cleaning.",
            },
            {
                "criterion": "Model Accuracy",
                "points_possible": 7.0,
                "points_awarded": 9.5,
                "explanation": "Outstanding accuracy.",
            },
        ]
    })
    parsed = parse_evaluation_response(raw_json, rubric)

    assert parsed["valid"] is True
    # Clamped to 3.0 and 7.0 respectively
    assert parsed["criteria"][0]["points_awarded"] == 3.0
    assert parsed["criteria"][1]["points_awarded"] == 7.0
    assert parsed["total_score"] == 10.0


def test_parse_evaluation_response_points_clamped_if_negative():
    rubric = {
        "criteria": [
            {"criterion": "Unit Tests", "points_possible": 5.0},
        ]
    }
    raw_json = json.dumps({
        "criteria": [
            {
                "criterion": "Unit Tests",
                "points_possible": 5.0,
                "points_awarded": -3.0,
                "explanation": "Failed tests completely.",
            },
        ]
    })
    parsed = parse_evaluation_response(raw_json, rubric)

    assert parsed["valid"] is True
    assert parsed["criteria"][0]["points_awarded"] == 0.0
    assert parsed["total_score"] == 0.0


def test_parse_evaluation_response_total_score_clamped_to_10():
    rubric = {
        "criteria": [
            {"criterion": "Part A", "points_possible": 6.0},
            {"criterion": "Part B", "points_possible": 6.0},
        ]
    }
    raw_json = json.dumps({
        "criteria": [
            {"criterion": "Part A", "points_possible": 6.0, "points_awarded": 6.0, "explanation": "A"},
            {"criterion": "Part B", "points_possible": 6.0, "points_awarded": 6.0, "explanation": "B"},
        ]
    })
    parsed = parse_evaluation_response(raw_json, rubric)

    assert parsed["valid"] is True
    # Sum is 12, but total_score is clamped to 10.0
    assert parsed["total_score"] == 10.0


def test_parse_evaluation_response_criteria_count_mismatch():
    rubric = {
        "criteria": [
            {"criterion": "C1", "points_possible": 5.0},
            {"criterion": "C2", "points_possible": 5.0},
        ]
    }
    raw_json = json.dumps({
        "criteria": [
            {"criterion": "C1", "points_possible": 5.0, "points_awarded": 4.0, "explanation": "Good"},
        ]
    })
    parsed = parse_evaluation_response(raw_json, rubric)

    assert parsed["valid"] is False
    assert "count mismatch" in parsed["error"]


def test_parse_evaluation_response_malformed_json():
    rubric = {"criteria": [{"criterion": "C1", "points_possible": 10.0}]}
    parsed = parse_evaluation_response("not valid json at all", rubric)
    assert parsed["valid"] is False
    assert "Invalid JSON" in parsed["error"]


def test_parse_evaluation_response_empty_string():
    rubric = {"criteria": [{"criterion": "C1", "points_possible": 10.0}]}
    parsed = parse_evaluation_response("", rubric)
    assert parsed["valid"] is False
    assert "Empty response" in parsed["error"]


# ===========================================================================
# 4. Submission File Evaluation Orchestrator
# ===========================================================================

def test_evaluate_submission_file_success(seeded_db):
    mock_nb = {
        "valid": True,
        "code_cells": [
            {"source": "x = 1\ny = 2\nprint(x + y)", "outputs": ["3"]},
            {"source": "def train(): return True", "outputs": []},
        ],
    }
    gemini_eval_json = json.dumps({
        "criteria": [
            {"criterion": "Data Preprocessing", "points_possible": 3.0, "points_awarded": 3.0, "explanation": "Cleaned"},
            {"criterion": "Model Training", "points_possible": 4.0, "points_awarded": 3.5, "explanation": "Trained"},
            {"criterion": "Evaluation and Metrics", "points_possible": 3.0, "points_awarded": 2.5, "explanation": "Evaluated"},
        ]
    })

    with patch("app.services.evaluator.parse_notebook_file", return_value=mock_nb):
        with patch("app.services.evaluator.call_gemini_for_evaluation", return_value=gemini_eval_json) as mock_gemini:
            result = evaluate_submission_file(seeded_db, submission_file_id=300)

            assert result["success"] is True
            assert result["total_score"] == 9.0
            assert len(result["criteria"]) == 3
            assert mock_gemini.call_count == 1


def test_evaluate_submission_file_unmatched_returns_error_immediately(seeded_db):
    """
    If matched_unsolved_file_id is None, return error immediately — do not call Gemini.
    """
    with patch("app.services.evaluator.call_gemini_for_evaluation") as mock_gemini:
        result = evaluate_submission_file(seeded_db, submission_file_id=301)

        assert result["success"] is False
        assert result["error"] == "not matched to an assignment"
        # Verify NO Gemini call is made
        mock_gemini.assert_not_called()


def test_evaluate_submission_file_rubric_autogenerated_on_the_fly(seeded_db):
    """
    If matched UnsolvedFile has no rubric generated, generate it on the fly.
    """
    # Clear existing rubric on unsolved file 100
    unsolved = seeded_db.get(UnsolvedFile, 100)
    unsolved.rubric_json = None
    seeded_db.commit()

    generated_rubric = {
        "criteria": [
            {"criterion": "Criterion A", "points_possible": 5.0},
            {"criterion": "Criterion B", "points_possible": 5.0},
        ]
    }
    mock_nb = {
        "valid": True,
        "code_cells": [{"source": "print('hello')", "outputs": ["hello"]}],
    }
    gemini_eval_json = json.dumps({
        "criteria": [
            {"criterion": "Criterion A", "points_possible": 5.0, "points_awarded": 4.5, "explanation": "Good"},
            {"criterion": "Criterion B", "points_possible": 5.0, "points_awarded": 4.0, "explanation": "Solid"},
        ]
    })

    with patch(
        "app.services.evaluator.generate_rubric_for_unsolved_file",
        return_value={"success": True, "rubric": generated_rubric},
    ) as mock_gen_rubric:
        with patch("app.services.evaluator.parse_notebook_file", return_value=mock_nb):
            with patch("app.services.evaluator.call_gemini_for_evaluation", return_value=gemini_eval_json):
                result = evaluate_submission_file(seeded_db, submission_file_id=300)

                assert mock_gen_rubric.call_count == 1
                assert result["success"] is True
                assert result["total_score"] == 8.5
                assert len(result["criteria"]) == 2


def test_evaluate_submission_file_malformed_gemini_response(seeded_db):
    mock_nb = {"valid": True, "code_cells": [{"source": "x = 1", "outputs": []}]}

    with patch("app.services.evaluator.parse_notebook_file", return_value=mock_nb):
        with patch("app.services.evaluator.call_gemini_for_evaluation", return_value="Malformed Text"):
            result = evaluate_submission_file(seeded_db, submission_file_id=300)

            assert result["success"] is False
            assert "error" in result


def test_evaluate_submission_file_gemini_exception_handled(seeded_db):
    mock_nb = {"valid": True, "code_cells": [{"source": "x = 1", "outputs": []}]}

    with patch("app.services.evaluator.parse_notebook_file", return_value=mock_nb):
        with patch(
            "app.services.evaluator.call_gemini_for_evaluation",
            side_effect=EvaluationError("Gemini API rate limit"),
        ):
            result = evaluate_submission_file(seeded_db, submission_file_id=300)

            assert result["success"] is False
            assert "Gemini API rate limit" in result["error"]


def test_evaluate_submission_file_nonexistent_id(seeded_db):
    result = evaluate_submission_file(seeded_db, submission_file_id=99999)
    assert result["success"] is False
    assert "not found" in result["error"]


def test_evaluate_submission_file_unreadable_notebook(seeded_db):
    with patch("app.services.evaluator.parse_notebook_file", return_value={"valid": False, "error": "File corrupted"}):
        result = evaluate_submission_file(seeded_db, submission_file_id=300)
        assert result["success"] is False
        assert "File corrupted" in result["error"]


# ===========================================================================
# 5. Temporary Test Endpoint (POST .../submissions/files/{id}/evaluate)
# ===========================================================================

@pytest.fixture()
def client_with_data(db):
    """Sets up a FastAPI TestClient wired to the in-memory test DB."""
    from app.database import get_db
    from app.main import app

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    instructor = User(
        id=1,
        name="Prof Turing",
        email="prof@test.com",
        hashed_password="hash",
        role=UserRole.instructor,
    )
    student = User(
        id=2,
        name="Ada Lovelace",
        email="ada@test.com",
        hashed_password="hash",
        role=UserRole.student,
    )
    lms_session = LMSSession(
        id=10,
        title="CS101 Intro to AI",
    )
    rubric_data = {
        "criteria": [
            {"criterion": "Code Quality", "points_possible": 10.0},
        ]
    }
    unsolved = UnsolvedFile(
        id=100,
        session_id=10,
        original_filename="hw1.ipynb",
        file_path="10/assignments/hw1.ipynb",
        parsed_requirements_text="Implement model.",
        rubric_json=json.dumps(rubric_data),
    )
    submission = Submission(
        id=200,
        session_id=10,
        student_id=2,
        original_filename="hw1_sub.ipynb",
        uploaded_file_path="10/submissions/2/hw1_sub.ipynb",
    )
    sub_file = SubmissionFile(
        id=300,
        submission_id=200,
        matched_unsolved_file_id=100,
        original_filename="hw1_sub.ipynb",
        extracted_ipynb_path="10/submissions/2/hw1_sub.ipynb",
    )

    db.add_all([instructor, student, lms_session, unsolved, submission, sub_file])
    db.commit()

    instructor_token = create_access_token(1, UserRole.instructor)
    student_token = create_access_token(2, UserRole.student)

    with TestClient(app) as test_client:
        yield test_client, instructor_token, student_token

    app.dependency_overrides.clear()


def test_temporary_evaluation_endpoint(client_with_data):
    client, instructor_token, student_token = client_with_data

    # 1. Unauthenticated request
    res_unauth = client.post(
        "/api/v1/sessions/10/submissions/files/300/evaluate"
    )
    assert res_unauth.status_code == 401

    # 2. Student cannot call this (instructor only)
    res_student = client.post(
        "/api/v1/sessions/10/submissions/files/300/evaluate",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert res_student.status_code == 403

    # 3. Nonexistent file returns 404
    res_notfound = client.post(
        "/api/v1/sessions/10/submissions/files/999/evaluate",
        headers={"Authorization": f"Bearer {instructor_token}"},
    )
    assert res_notfound.status_code == 404

    # 4. Instructor calls successfully — endpoint now calls generate_feedback_and_persist
    # which returns {success, grade_id, score, feedback_text} (Sub-feature 5 shape).
    mock_grade = {
        "success": True,
        "grade_id": 1,
        "score": 9.5,
        "feedback_text": (
            "Score: 9.5/10.\n"
            "- Code Quality: 9.5/10.0 — Clean and well structured code."
        ),
    }
    with patch("app.services.feedback.generate_feedback_and_persist", return_value=mock_grade):
        res_instructor = client.post(
            "/api/v1/sessions/10/submissions/files/300/evaluate",
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert res_instructor.status_code == 200
        data = res_instructor.json()
        assert data["success"] is True
        assert data["grade_id"] == 1
        assert data["score"] == 9.5
        assert "Score: 9.5/10." in data["feedback_text"]
