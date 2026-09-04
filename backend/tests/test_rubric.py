"""
Tests for backend/app/services/rubric.py — Phase 2, Sub-feature 3.

All Gemini API calls are mocked using unittest.mock to prevent burning real API
quota during test runs.

Cases covered
─────────────
1. build_rubric_prompt
   - Formats prompt properly and includes requirements text and JSON instructions.

2. parse_rubric_response
   - Plain JSON string with valid criteria summing to 10.
   - Code fence stripping (```json ... ``` and ``` ... ```).
   - Points not summing to 10 (triggers proportional rescaling to exactly 10).
   - Rescaling with float rounding handles discrepancies.
   - Malformed JSON string returns valid=False.
   - Empty response returns valid=False.
   - Missing required keys (criterion, points_possible) returns valid=False.
   - Non-positive points returns valid=False.

3. call_gemini_for_rubric
   - Successful call returns response text.
   - Missing API key raises RubricGenerationError.
   - Exception during Gemini call raises RubricGenerationError.

4. generate_rubric_for_unsolved_file
   - Valid generation: calls Gemini, parses, saves rubric_json, returns success.
   - Cached reuse: when rubric_generated is already True, returns cached rubric
     immediately and confirms NO Gemini call is made.
   - Missing/empty requirements text: returns error immediately without calling Gemini.
   - Malformed Gemini response triggers retry with strict directive; succeeds on retry.
   - Malformed Gemini response retry also fails; returns error without crashing.
   - Unsolved file not found returns error.

5. API Endpoint (POST /api/v1/sessions/{session_id}/assignments/{file_id}/generate-rubric)
   - Instructor can generate rubric.
   - Student receives 403 Forbidden.
   - Cached rubric returned on second call.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole
from app.services.auth import create_access_token
from app.services.rubric import (
    RubricGenerationError,
    build_rubric_prompt,
    call_gemini_for_rubric,
    generate_rubric_for_unsolved_file,
    parse_rubric_response,
)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture()
def db():
    """
    In-memory SQLite database for isolated service tests.
    """
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
    DB with a sample instructor, student, session, and unsolved file.
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
    unsolved = UnsolvedFile(
        id=100,
        session_id=10,
        original_filename="hw1.ipynb",
        file_path="10/assignments/hw1.ipynb",
        parsed_requirements_text=(
            "Task 1: Load and clean the dataset (2 pts).\n"
            "Task 2: Build a linear regression model (5 pts).\n"
            "Task 3: Plot loss curve and evaluate MSE (3 pts)."
        ),
    )
    db.add_all([instructor, student, lms_session, unsolved])
    db.commit()
    return db


@pytest.fixture(autouse=True)
def mock_assignment_structure(monkeypatch):
    """Service tests do not persist fixture notebooks to local storage."""
    monkeypatch.setattr(
        "app.services.rubric.extract_notebook_structure",
        lambda _path: {
            "valid": True,
            "cells": [
                {"type": "markdown", "content": "Implement model and test.", "heuristic_hint": False},
                {"type": "code", "content": "# TODO: student work", "heuristic_hint": True},
            ],
            "error": None,
        },
    )


# ===========================================================================
# 1. Prompt building
# ===========================================================================

def test_build_rubric_prompt():
    cells = [{"type": "markdown", "content": "Task 1: Clean data. Task 2: Fit model.", "heuristic_hint": False}]
    prompt = build_rubric_prompt(cells)
    assert "Task 1: Clean data" in prompt
    assert "criteria" in prompt
    assert "10" in prompt
    assert "ONLY valid JSON" in prompt


# ===========================================================================
# 2. Parsing and Rescaling
# ===========================================================================

def test_parse_rubric_response_clean_json():
    raw = json.dumps({
        "criteria": [
            {"criterion": "Data Preprocessing", "points_possible": 2.0},
            {"criterion": "Model Implementation", "points_possible": 5.0},
            {"criterion": "Evaluation & Visualisation", "points_possible": 3.0},
        ]
    })
    parsed = parse_rubric_response(raw)
    assert parsed["valid"] is True
    assert parsed["error"] is None
    assert len(parsed["criteria"]) == 3
    assert sum(c["points_possible"] for c in parsed["criteria"]) == 10.0


def test_parse_rubric_response_with_markdown_fences():
    inner_json = json.dumps({
        "criteria": [
            {"criterion": "Code Quality", "points_possible": 4},
            {"criterion": "Accuracy", "points_possible": 6},
        ]
    })
    raw = f"```json\n{inner_json}\n```"
    parsed = parse_rubric_response(raw)
    assert parsed["valid"] is True
    assert len(parsed["criteria"]) == 2
    assert sum(c["points_possible"] for c in parsed["criteria"]) == 10.0


def test_parse_rubric_response_rescaling_when_not_summing_to_10():
    # Sums to 20 instead of 10
    raw = json.dumps({
        "criteria": [
            {"criterion": "Criterion A", "points_possible": 10},
            {"criterion": "Criterion B", "points_possible": 10},
        ]
    })
    parsed = parse_rubric_response(raw)
    assert parsed["valid"] is True
    assert sum(c["points_possible"] for c in parsed["criteria"]) == 10.0
    assert parsed["criteria"][0]["points_possible"] == 5.0
    assert parsed["criteria"][1]["points_possible"] == 5.0


def test_parse_rubric_response_rescaling_rounding_adjustment():
    # 3 items of 1 pt each (total 3 pts) -> scaled to 10: 3.33, 3.33, 3.34
    raw = json.dumps({
        "criteria": [
            {"criterion": "A", "points_possible": 1},
            {"criterion": "B", "points_possible": 1},
            {"criterion": "C", "points_possible": 1},
        ]
    })
    parsed = parse_rubric_response(raw)
    assert parsed["valid"] is True
    total = sum(c["points_possible"] for c in parsed["criteria"])
    assert total == 10.0
    assert all((criterion["points_possible"] * 2).is_integer() for criterion in parsed["criteria"])


def test_rubric_prompt_treats_later_response_cells_as_context_not_keyword_proof():
    prompt = build_rubric_prompt([
        {"type": "markdown", "content": "Explain your model choice.", "heuristic_hint": False},
        {"type": "markdown", "content": "Your Analysis:", "heuristic_hint": True},
        {"type": "code", "content": "import pandas as pd", "heuristic_hint": False},
    ])
    assert "heuristic_hint=False" in prompt
    assert "heuristic_hint=True" in prompt
    assert "question in one markdown cell" in prompt
    assert "at most 1 to 2 marks" in prompt


def test_fully_prewritten_notebook_prompt_limits_runs_correctly_weight():
    prompt = build_rubric_prompt([
        {"type": "markdown", "content": "Run the supplied example.", "heuristic_hint": False},
        {"type": "code", "content": "print('provided result')", "heuristic_hint": False},
    ])
    assert "majority of the 10 marks" in prompt
    assert "at most 1 to 2 marks total" in prompt
    assert "runs without error" in prompt


def test_parse_rubric_response_malformed_json():
    parsed = parse_rubric_response("This is not JSON at all.")
    assert parsed["valid"] is False
    assert parsed["error"] is not None
    assert parsed["criteria"] == []


def test_parse_rubric_response_empty_string():
    parsed = parse_rubric_response("")
    assert parsed["valid"] is False
    assert "Empty response" in parsed["error"]


def test_parse_rubric_response_invalid_structure():
    # List instead of dict or missing keys
    parsed = parse_rubric_response(json.dumps({"criteria": [{"invalid": "no keys"}]}))
    assert parsed["valid"] is False
    assert "missing" in parsed["error"].lower()


# ===========================================================================
# 3. Gemini invocation wrapper
# ===========================================================================

def test_call_gemini_missing_api_key(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "gemini_api_key", "")
    with pytest.raises(RubricGenerationError) as exc_info:
        call_gemini_for_rubric("prompt")
    assert "not configured" in str(exc_info.value)


@patch("google.generativeai.GenerativeModel")
@patch("google.generativeai.configure")
def test_call_gemini_success(mock_configure, mock_model_cls, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    mock_instance = MagicMock()
    mock_instance.generate_content.return_value = MagicMock(text='{"criteria": []}')
    mock_model_cls.return_value = mock_instance

    result = call_gemini_for_rubric("test prompt")
    assert result == '{"criteria": []}'
    mock_configure.assert_called_once_with(api_key="test-key")


@patch("google.generativeai.GenerativeModel")
@patch("google.generativeai.configure")
def test_call_gemini_failure_raises_rubric_error(mock_configure, mock_model_cls, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    mock_instance = MagicMock()
    mock_instance.generate_content.side_effect = RuntimeError("Quota exceeded")
    mock_model_cls.return_value = mock_instance

    with pytest.raises(RubricGenerationError) as exc_info:
        call_gemini_for_rubric("test prompt")
    assert "Quota exceeded" in str(exc_info.value)


# ===========================================================================
# 4. generate_rubric_for_unsolved_file Orchestrator
# ===========================================================================

@patch("app.services.rubric.call_gemini_for_rubric")
def test_generate_rubric_valid(mock_gemini, seeded_db):
    sample_rubric = {
        "criteria": [
            {"criterion": "Data Loading & Preprocessing", "points_possible": 2.0},
            {"criterion": "Model Training", "points_possible": 5.0},
            {"criterion": "Evaluation & Visualisation", "points_possible": 3.0},
        ]
    }
    mock_gemini.return_value = json.dumps(sample_rubric)

    result = generate_rubric_for_unsolved_file(seeded_db, unsolved_file_id=100)

    assert result["success"] is True
    assert "rubric" in result
    assert len(result["rubric"]["criteria"]) == 3
    assert mock_gemini.call_count == 1

    # Check that database row was updated
    file_row = seeded_db.get(UnsolvedFile, 100)
    assert file_row.rubric_generated is True
    assert file_row.rubric_json is not None
    saved_data = json.loads(file_row.rubric_json)
    assert saved_data["criteria"][0]["criterion"] == "Data Loading & Preprocessing"


@patch("app.services.rubric.call_gemini_for_rubric")
def test_generate_rubric_cached_reuse_no_gemini_call(mock_gemini, seeded_db):
    # Pre-populate rubric
    cached_rubric = {
        "criteria": [
            {"criterion": "Pre-existing Criterion", "points_possible": 10.0}
        ]
    }
    file_row = seeded_db.get(UnsolvedFile, 100)
    file_row.rubric_json = json.dumps(cached_rubric)
    seeded_db.commit()

    # Call orchestrator
    result = generate_rubric_for_unsolved_file(seeded_db, unsolved_file_id=100)

    assert result["success"] is True
    assert result["rubric"] == cached_rubric
    # CRITICAL: Ensure Gemini was NOT called
    mock_gemini.assert_not_called()


@patch("app.services.rubric.call_gemini_for_rubric")
def test_force_regeneration_overwrites_cached_rubric(mock_gemini, seeded_db):
    row = seeded_db.get(UnsolvedFile, 100)
    row.rubric_json = json.dumps({"criteria": [{"criterion": "Old", "points_possible": 10.0}]})
    seeded_db.commit()
    fresh = {"criteria": [{"criterion": "New work", "points_possible": 10.0}]}
    mock_gemini.return_value = json.dumps(fresh)
    result = generate_rubric_for_unsolved_file(seeded_db, 100, force=True)
    assert result["success"] is True
    assert result["rubric"] == fresh
    assert mock_gemini.call_count == 1


@patch("app.services.rubric.call_gemini_for_rubric")
def test_generate_rubric_missing_requirements(mock_gemini, seeded_db, monkeypatch):
    file_row = seeded_db.get(UnsolvedFile, 100)
    file_row.parsed_requirements_text = None
    seeded_db.commit()
    monkeypatch.setattr(
        "app.services.rubric.extract_notebook_structure",
        lambda _path: {"valid": False, "cells": [], "error": "File corrupted"},
    )

    result = generate_rubric_for_unsolved_file(seeded_db, unsolved_file_id=100)

    assert result["success"] is False
    assert "File corrupted" in result["error"]
    mock_gemini.assert_not_called()


@patch("app.services.rubric.call_gemini_for_rubric")
def test_generate_rubric_retry_on_malformed_gemini_response(mock_gemini, seeded_db):
    valid_rubric = {
        "criteria": [
            {"criterion": "Task 1", "points_possible": 4.0},
            {"criterion": "Task 2", "points_possible": 6.0},
        ]
    }
    # First call returns malformed text, second call returns valid JSON
    mock_gemini.side_effect = [
        "Sure! Here is your rubric: {not json at all",
        json.dumps(valid_rubric),
    ]

    result = generate_rubric_for_unsolved_file(seeded_db, unsolved_file_id=100)

    assert result["success"] is True
    assert len(result["rubric"]["criteria"]) == 2
    # Verify it retried exactly once
    assert mock_gemini.call_count == 2
    # Verify retry prompt included strict instruction
    second_call_prompt = mock_gemini.call_args_list[1][0][0]
    assert "Respond with valid JSON only, absolutely no markdown." in second_call_prompt


@patch("app.services.rubric.call_gemini_for_rubric")
def test_generate_rubric_retry_also_fails(mock_gemini, seeded_db):
    # Both attempts return garbage
    mock_gemini.side_effect = [
        "First bad output",
        "Second bad output",
    ]

    result = generate_rubric_for_unsolved_file(seeded_db, unsolved_file_id=100)

    assert result["success"] is False
    assert "error" in result
    assert mock_gemini.call_count == 2
    # File in db should NOT be marked rubric_generated
    file_row = seeded_db.get(UnsolvedFile, 100)
    assert file_row.rubric_generated is False


def test_generate_rubric_nonexistent_file(seeded_db):
    result = generate_rubric_for_unsolved_file(seeded_db, unsolved_file_id=99999)
    assert result["success"] is False
    assert "not found" in result["error"]


# ===========================================================================
# 5. Temporary Endpoint via TestClient
# ===========================================================================

def test_temporary_rubric_endpoint(client_with_seeded_data):
    """
    Test POST /api/v1/sessions/{session_id}/assignments/{file_id}/generate-rubric
    """
    client, instructor_token, student_token = client_with_seeded_data

    sample_rubric = {
        "criteria": [
            {"criterion": "Implementation", "points_possible": 6.0},
            {"criterion": "Testing", "points_possible": 4.0},
        ]
    }

    with patch("app.services.rubric.call_gemini_for_rubric") as mock_gemini:
        mock_gemini.return_value = json.dumps(sample_rubric)

        # 1. Student cannot call this (instructor only)
        res_student = client.post(
            "/api/v1/sessions/10/assignments/100/generate-rubric",
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert res_student.status_code == 403

        # 2. Instructor calls successfully
        res_instructor = client.post(
            "/api/v1/sessions/10/assignments/100/generate-rubric",
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert res_instructor.status_code == 200
        data = res_instructor.json()
        assert data["success"] is True
        assert len(data["rubric"]["criteria"]) == 2
        assert mock_gemini.call_count == 1

        # 3. Calling a second time returns cached rubric without calling Gemini again
        res_second = client.post(
            "/api/v1/sessions/10/assignments/100/generate-rubric",
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert res_second.status_code == 200
        assert mock_gemini.call_count == 1  # No additional call


@pytest.fixture()
def client_with_seeded_data(db):
    """
    Sets up a FastAPI TestClient wired to the in-memory test DB.
    """
    from app.main import app
    from app.database import get_db

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
    unsolved = UnsolvedFile(
        id=100,
        session_id=10,
        original_filename="hw1.ipynb",
        file_path="10/assignments/hw1.ipynb",
        parsed_requirements_text="Implement model and test.",
    )
    db.add_all([instructor, student, lms_session, unsolved])
    db.commit()

    instructor_token = create_access_token(1, UserRole.instructor)
    student_token = create_access_token(2, UserRole.student)

    with TestClient(app) as test_client:
        yield test_client, instructor_token, student_token

    app.dependency_overrides.clear()
