"""
Tests for backend/app/routers/chat.py -- Phase 3, Sub-feature 3.3.

match_instruction_to_session (3.1) and parse_grading_filter (3.2) are mocked
at the boundary for every test here — their own internal logic is already
covered in test_session_matcher.py / test_instruction_filter.py. This file
only confirms /chat routes each of the 7 possible outcomes to the correct
response shape, plus the 403 (non-instructor) and 422 (malformed body)
access-control cases.

For the two "graded" outcomes, the real grade_session_batch pipeline runs
against a seeded in-memory DB (same fixture shape as test_grading_pipeline.py's
POST /sessions/{id}/grade endpoint tests) with only the LLM call boundary
mocked — this proves /chat really drives the Phase 2 pipeline, not a mock of
the wiring itself.

Cases covered
-------------
1. session_matcher "no_match" -> {"status": "no_session_match", ...}
2. session_matcher "ambiguous" -> {"status": "ambiguous_session", "candidates": [...]}
3. instruction_filter "not_found" -> {"status": "student_not_found", ...}
4. instruction_filter "ambiguous" -> {"status": "ambiguous_student", ...}
5. instruction_filter "unsupported" -> {"status": "unsupported_filter", ...}
6. scope "all" -> {"status": "graded", "scope": "all", ...} (real pipeline)
7. scope "student" -> {"status": "graded", "scope": "student", "student_name": ..., ...} (real pipeline)
8. Non-instructor (student) -> 403
9. Missing "instruction" field -> 422
"""

import json
from unittest.mock import patch

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


def _rubric_json() -> str:
    return json.dumps({
        "criteria": [{"criterion": "Correctness", "points_possible": 10.0}]
    })


def _good_gemini_response() -> str:
    return json.dumps({
        "criteria": [{
            "criterion": "Correctness",
            "points_possible": 10.0,
            "points_awarded": 8.0,
            "explanation": "Mostly correct.",
        }]
    })


_MOCK_NB = {"valid": True, "code_cells": [{"source": "x = 1", "outputs": []}]}
_MOCK_NB_STRUCTURE = {
    "valid": True,
    "cells": [{"cell_type": "code", "source": "x = 1", "outputs": []}],
}


def _patch_pipeline(monkeypatch):
    monkeypatch.setattr("app.services.evaluator.parse_notebook_file", lambda _p: _MOCK_NB)
    monkeypatch.setattr(
        "app.services.evaluator.extract_notebook_structure", lambda _p: _MOCK_NB_STRUCTURE
    )
    monkeypatch.setattr(
        "app.services.evaluator.call_gemini_for_evaluation",
        lambda _prompt: _good_gemini_response(),
    )


@pytest.fixture()
def db():
    """Fresh in-memory SQLite DB using the real ORM models."""
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
def api_client(db):
    """
    FastAPI TestClient with in-memory DB override. Seeds an instructor,
    session 10 ("DS101"), and one gradeable ungraded SubmissionFile for
    student Alice (id=2). Yields (client, instructor_token, student_token, db).
    """
    from app.database import get_db
    from app.main import app

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    db.add_all([
        User(id=1, name="Prof", email="prof@x.com", hashed_password="h", role=UserRole.instructor),
        User(id=2, name="Alice", email="alice@x.com", hashed_password="h", role=UserRole.student),
        LMSSession(id=10, title="DS101", instructor_id=1),
        UnsolvedFile(
            id=100, session_id=10, original_filename="hw1.ipynb",
            file_path="10/assignments/hw1.ipynb",
            parsed_requirements_text="Do the thing.",
            rubric_json=_rubric_json(),
            rubric_generated=True,
        ),
        Submission(
            id=200, session_id=10, student_id=2,
            original_filename="hw1.ipynb",
            uploaded_file_path="10/submissions/2/hw1.ipynb",
        ),
        SubmissionFile(
            id=300, submission_id=200,
            matched_unsolved_file_id=100,
            original_filename="hw1.ipynb",
            extracted_ipynb_path="10/submissions/2/hw1.ipynb",
            graded=False,
        ),
    ])
    db.commit()

    instructor_token = create_access_token(1, UserRole.instructor)
    student_token = create_access_token(2, UserRole.student)

    with TestClient(app) as c:
        yield c, instructor_token, student_token, db

    app.dependency_overrides.clear()


_ENDPOINT = "/api/v1/chat"
_MATCHED_SESSION = {
    "status": "matched", "session_id": 10, "session_title": "DS101", "confidence": 1.0,
}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1-2. Session-resolution branches
# ---------------------------------------------------------------------------

def test_no_session_match(api_client):
    c, instructor_token, *_ = api_client
    with patch(
        "app.routers.chat.match_instruction_to_session",
        return_value={"status": "no_match"},
    ):
        res = c.post(
            _ENDPOINT, json={"instruction": "grade something"}, headers=_auth(instructor_token)
        )
    assert res.status_code == 200
    assert res.json()["status"] == "no_session_match"


def test_ambiguous_session(api_client):
    c, instructor_token, *_ = api_client
    candidates = [
        {"session_id": 10, "session_title": "DS101", "confidence": 0.9},
        {"session_id": 11, "session_title": "DS101 Extra", "confidence": 0.85},
    ]
    with patch(
        "app.routers.chat.match_instruction_to_session",
        return_value={"status": "ambiguous", "candidates": candidates},
    ):
        res = c.post(
            _ENDPOINT, json={"instruction": "grade ds101"}, headers=_auth(instructor_token)
        )
    assert res.status_code == 200
    body = res.json()
    assert body == {"status": "ambiguous_session", "candidates": candidates}


# ---------------------------------------------------------------------------
# 3-5. Filter-resolution branches
# ---------------------------------------------------------------------------

def test_student_not_found(api_client):
    c, instructor_token, *_ = api_client
    with patch(
        "app.routers.chat.match_instruction_to_session", return_value=_MATCHED_SESSION
    ), patch(
        "app.routers.chat.parse_grading_filter",
        return_value={"scope": "not_found", "attempted_name": "Xavier"},
    ):
        res = c.post(
            _ENDPOINT, json={"instruction": "grade Xavier's file"}, headers=_auth(instructor_token)
        )
    assert res.status_code == 200
    assert res.json() == {"status": "student_not_found", "attempted_name": "Xavier"}


def test_ambiguous_student(api_client):
    c, instructor_token, *_ = api_client
    candidates = [
        {"student_id": 2, "student_name": "Ali Khan"},
        {"student_id": 3, "student_name": "Ali Raza"},
    ]
    with patch(
        "app.routers.chat.match_instruction_to_session", return_value=_MATCHED_SESSION
    ), patch(
        "app.routers.chat.parse_grading_filter",
        return_value={"scope": "ambiguous", "candidates": candidates},
    ):
        res = c.post(
            _ENDPOINT, json={"instruction": "grade Ali's file"}, headers=_auth(instructor_token)
        )
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "status": "ambiguous_student",
        "session_id": 10,
        "session_title": "DS101",
        "candidates": candidates,
    }


def test_unsupported_filter(api_client):
    c, instructor_token, *_ = api_client
    with patch(
        "app.routers.chat.match_instruction_to_session", return_value=_MATCHED_SESSION
    ), patch(
        "app.routers.chat.parse_grading_filter",
        return_value={"scope": "unsupported", "reason": "exclusionary filters not yet supported"},
    ):
        res = c.post(
            _ENDPOINT,
            json={"instruction": "grade everyone except Ali"},
            headers=_auth(instructor_token),
        )
    assert res.status_code == 200
    assert res.json() == {
        "status": "unsupported_filter",
        "reason": "exclusionary filters not yet supported",
    }


# ---------------------------------------------------------------------------
# 6-7. "graded" branches — real grade_session_batch pipeline runs
# ---------------------------------------------------------------------------

def test_graded_all_scope_runs_real_pipeline(api_client, monkeypatch):
    c, instructor_token, *_ = api_client
    _patch_pipeline(monkeypatch)

    with patch(
        "app.routers.chat.match_instruction_to_session", return_value=_MATCHED_SESSION
    ), patch(
        "app.routers.chat.parse_grading_filter", return_value={"scope": "all"}
    ):
        res = c.post(
            _ENDPOINT, json={"instruction": "grade ds101"}, headers=_auth(instructor_token)
        )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "graded"
    assert body["session_id"] == 10
    assert body["session_title"] == "DS101"
    assert body["scope"] == "all"
    assert "student_name" not in body
    assert body["summary"]["total"] == 1
    assert body["summary"]["graded"] == 1
    assert body["events"][-1] == body["summary"]


def test_graded_student_scope_runs_real_pipeline(api_client, monkeypatch):
    c, instructor_token, *_ = api_client
    _patch_pipeline(monkeypatch)

    with patch(
        "app.routers.chat.match_instruction_to_session", return_value=_MATCHED_SESSION
    ), patch(
        "app.routers.chat.parse_grading_filter",
        return_value={"scope": "student", "student_id": 2, "student_name": "Alice"},
    ):
        res = c.post(
            _ENDPOINT, json={"instruction": "grade Alice's file"}, headers=_auth(instructor_token)
        )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "graded"
    assert body["scope"] == "student"
    assert body["student_name"] == "Alice"
    assert body["summary"]["total"] == 1
    assert body["summary"]["graded"] == 1


# ---------------------------------------------------------------------------
# 8-9. Access control / validation
# ---------------------------------------------------------------------------

def test_non_instructor_returns_403(api_client):
    c, _, student_token, _ = api_client
    res = c.post(
        _ENDPOINT, json={"instruction": "grade ds101"}, headers=_auth(student_token)
    )
    assert res.status_code == 403


def test_missing_instruction_field_returns_422(api_client):
    c, instructor_token, *_ = api_client
    res = c.post(_ENDPOINT, json={}, headers=_auth(instructor_token))
    assert res.status_code == 422


# ===========================================================================
# POST /chat/stream -- Phase 3, Sub-feature 3.4
#
# _resolve_chat_instruction and grade_session_batch are mocked at the
# boundary — their own internal logic (and /chat's use of the same
# resolution helper) is already covered above and in
# test_session_matcher.py / test_instruction_filter.py. These tests only
# confirm /chat/stream frames each outcome as SSE events correctly.
# ===========================================================================

_STREAM_ENDPOINT = "/api/v1/chat/stream"


def _parse_sse_events(raw_text: str) -> list[dict]:
    """Split raw SSE text on the blank-line frame boundary and parse each
    "data: {...}" block back into a dict."""
    events = []
    for block in raw_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        assert block.startswith("data: "), f"unexpected SSE line: {block!r}"
        events.append(json.loads(block[len("data: "):]))
    return events


def _fake_grade_session_batch(db, session_id, student_id=None):
    yield {"event": "checking", "student_id": 2, "filename": "hw1.ipynb"}
    yield {"event": "graded", "student_id": 2, "filename": "hw1.ipynb", "score": 8.5}
    yield {"event": "summary", "total": 1, "graded": 1, "failed": 0, "failures": []}


def test_stream_early_exit_produces_one_event(api_client):
    c, instructor_token, *_ = api_client
    early_exit = {"status": "ambiguous_session", "candidates": [
        {"session_id": 10, "session_title": "DS101", "confidence": 0.9},
        {"session_id": 11, "session_title": "DS101 Extra", "confidence": 0.85},
    ]}
    with patch("app.routers.chat._resolve_chat_instruction", return_value=early_exit):
        res = c.post(
            _STREAM_ENDPOINT, json={"instruction": "grade ds101"}, headers=_auth(instructor_token)
        )

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse_events(res.text)
    assert events == [early_exit]


def test_stream_resolved_streams_events_in_order(api_client):
    c, instructor_token, *_ = api_client
    resolution = {
        "resolved": True, "session_id": 10, "session_title": "DS101",
        "student_id": None, "student_name": None,
    }
    with patch(
        "app.routers.chat._resolve_chat_instruction", return_value=resolution
    ), patch(
        "app.services.grading_pipeline.grade_session_batch", _fake_grade_session_batch
    ):
        res = c.post(
            _STREAM_ENDPOINT, json={"instruction": "grade ds101"}, headers=_auth(instructor_token)
        )

    assert res.status_code == 200
    events = _parse_sse_events(res.text)
    assert [e["event"] for e in events] == ["checking", "graded", "summary"]
    assert events[-1] == {"event": "summary", "total": 1, "graded": 1, "failed": 0, "failures": []}


def test_stream_non_instructor_returns_403(api_client):
    c, _, student_token, _ = api_client
    res = c.post(
        _STREAM_ENDPOINT, json={"instruction": "grade ds101"}, headers=_auth(student_token)
    )
    assert res.status_code == 403


def test_stream_missing_instruction_field_returns_422(api_client):
    c, instructor_token, *_ = api_client
    res = c.post(_STREAM_ENDPOINT, json={}, headers=_auth(instructor_token))
    assert res.status_code == 422
