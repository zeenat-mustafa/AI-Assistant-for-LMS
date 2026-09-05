"""
Tests for backend/app/services/grading_pipeline.py — Phase 2, Sub-feature 7.

All Gemini API calls and file-system reads are mocked so no real quota is
consumed and no disk files are required.

Cases covered
─────────────
1. grade_single_submission_file
   a. Returns success dict with score when pipeline succeeds
   b. Returns failure dict (not an exception) when file has no matched assignment
   c. Returns failure dict when generate_feedback_and_persist raises unexpectedly
   d. Returns failure dict for non-existent submission_file_id
   e. success=False from generate_feedback_and_persist is forwarded as failure
   f. student_id and filename are always present in the returned dict

2. grade_session_batch (generator)
   a. Empty batch yields exactly one summary event with all-zero counts
   b. All-success batch: events are in correct order (checking → graded × N → summary)
   c. All-success batch: summary counts match actual number of files
   d. Mixed batch: failed files yield "failed" events; rest continue to "graded"
   e. Failures are collected in summary["failures"] with student_id/filename/error
   f. Generator never raises regardless of what fails inside
   g. Already-graded files (graded=True) are NOT included in the batch
   h. Event ordering: every "checking" is immediately followed by its "graded"/"failed"

3. Endpoint  POST /api/v1/sessions/{session_id}/grade
   a. Unauthenticated → 401
   b. Student role → 403
   c. Non-existent session → 404
   d. Empty batch returns {"events": [summary], "summary": {...}} with zero counts
   e. Successful batch returns events list and correct summary
   f. summary key equals the last element of the events list
   g. Mixed batch: failed files appear in summary["failures"]
"""

import json
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
from app.services.grading_pipeline import grade_session_batch, grade_single_submission_file


# ===========================================================================
# Shared fixtures
# ===========================================================================

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


def _rubric_json() -> str:
    return json.dumps({
        "criteria": [
            {"criterion": "Correctness", "points_possible": 10.0},
        ]
    })


def _good_gemini_response() -> str:
    return json.dumps({
        "criteria": [
            {
                "criterion": "Correctness",
                "points_possible": 10.0,
                "points_awarded": 8.0,
                "explanation": "Mostly correct.",
            }
        ]
    })


@pytest.fixture()
def seeded_db(db):
    """
    Session 10, instructor (id=1), two students (id=2, id=3).

    SubmissionFiles:
      id=300  student=2  matched unsolved → gradeable
      id=301  student=2  matched unsolved → gradeable  (second file for same student)
      id=302  student=3  matched_unsolved_file_id=None → unmatched (will fail)
      id=303  student=3  already graded=True            → skipped by batch
    """
    db.add_all([
        User(id=1, name="Prof", email="prof@x.com", hashed_password="h", role=UserRole.instructor),
        User(id=2, name="Alice", email="alice@x.com", hashed_password="h", role=UserRole.student),
        User(id=3, name="Bob", email="bob@x.com", hashed_password="h", role=UserRole.student),
        LMSSession(id=10, title="DS101"),
        UnsolvedFile(
            id=100, session_id=10, original_filename="hw1.ipynb",
            file_path="10/assignments/hw1.ipynb",
            parsed_requirements_text="Do the thing.",
            rubric_json=_rubric_json(),
            rubric_generated=True,
        ),
        # Alice's submission
        Submission(
            id=200, session_id=10, student_id=2,
            original_filename="alice_hw1.zip",
            uploaded_file_path="10/submissions/2/alice_hw1.zip",
        ),
        SubmissionFile(
            id=300, submission_id=200,
            matched_unsolved_file_id=100,
            original_filename="hw1.ipynb",
            extracted_ipynb_path="10/submissions/2/hw1.ipynb",
            graded=False,
        ),
        SubmissionFile(
            id=301, submission_id=200,
            matched_unsolved_file_id=100,
            original_filename="bonus.ipynb",
            extracted_ipynb_path="10/submissions/2/bonus.ipynb",
            graded=False,
        ),
        # Bob's submission
        Submission(
            id=201, session_id=10, student_id=3,
            original_filename="bob_hw1.ipynb",
            uploaded_file_path="10/submissions/3/bob_hw1.ipynb",
        ),
        SubmissionFile(
            id=302, submission_id=201,
            matched_unsolved_file_id=None,   # unmatched — will fail
            original_filename="bob_hw1.ipynb",
            extracted_ipynb_path="10/submissions/3/bob_hw1.ipynb",
            graded=False,
        ),
        SubmissionFile(
            id=303, submission_id=201,
            matched_unsolved_file_id=100,
            original_filename="already_done.ipynb",
            extracted_ipynb_path="10/submissions/3/already_done.ipynb",
            graded=True,                     # already graded — must be skipped
        ),
    ])
    db.commit()
    return db


# Mock notebook parse: returns a minimal valid structure so the pipeline
# doesn't try to touch the filesystem.
_MOCK_NB = {"valid": True, "code_cells": [{"source": "x = 1", "outputs": []}]}
_MOCK_NB_STRUCTURE = {
    "valid": True,
    "cells": [{"cell_type": "code", "source": "x = 1", "outputs": []}],
}


def _patch_pipeline(monkeypatch):
    """Apply all mocks needed for the full grading pipeline to succeed."""
    monkeypatch.setattr(
        "app.services.evaluator.parse_notebook_file",
        lambda _path: _MOCK_NB,
    )
    monkeypatch.setattr(
        "app.services.evaluator.extract_notebook_structure",
        lambda _path: _MOCK_NB_STRUCTURE,
    )
    monkeypatch.setattr(
        "app.services.evaluator.call_gemini_for_evaluation",
        lambda _prompt: _good_gemini_response(),
    )


# ===========================================================================
# 1. grade_single_submission_file
# ===========================================================================

class TestGradeSingleSubmissionFile:

    def test_success_returns_correct_shape(self, seeded_db, monkeypatch):
        """Full pipeline mock → should return success=True with a score."""
        _patch_pipeline(monkeypatch)
        result = grade_single_submission_file(seeded_db, submission_file_id=300)

        assert result["success"] is True
        assert result["submission_file_id"] == 300
        assert result["student_id"] == 2
        assert result["filename"] == "hw1.ipynb"
        assert isinstance(result["score"], float)
        assert 0.0 <= result["score"] <= 10.0

    def test_unmatched_file_returns_failure_not_exception(self, seeded_db):
        """File 302 has no matched unsolved file — pipeline returns failure dict."""
        result = grade_single_submission_file(seeded_db, submission_file_id=302)

        assert result["success"] is False
        assert result["submission_file_id"] == 302
        assert result["student_id"] == 3
        assert result["filename"] == "bob_hw1.ipynb"
        assert "error" in result
        assert isinstance(result["error"], str)
        assert len(result["error"]) > 0

    def test_unexpected_exception_is_caught(self, seeded_db, monkeypatch):
        """If generate_feedback_and_persist raises, still returns a failure dict."""
        # The function does `from app.services.feedback import generate_feedback_and_persist`
        # inside its body, so we patch at the source module.
        with patch(
            "app.services.feedback.generate_feedback_and_persist",
            side_effect=RuntimeError("disk exploded"),
        ):
            result = grade_single_submission_file(seeded_db, submission_file_id=300)

        assert result["success"] is False
        assert "disk exploded" in result["error"]

    def test_nonexistent_submission_file(self, seeded_db):
        """Non-existent id → failure dict, never raises."""
        result = grade_single_submission_file(seeded_db, submission_file_id=99999)

        assert result["success"] is False
        assert result["submission_file_id"] == 99999
        assert "99999" in result["error"]

    def test_pipeline_failure_forwarded_as_failure(self, seeded_db, monkeypatch):
        """generate_feedback_and_persist returns success=False → forwarded cleanly."""
        monkeypatch.setattr(
            "app.services.evaluator.parse_notebook_file",
            lambda _path: {"valid": False, "error": "corrupted notebook"},
        )
        monkeypatch.setattr(
            "app.services.evaluator.extract_notebook_structure",
            lambda _path: {"valid": False, "error": "corrupted notebook"},
        )
        result = grade_single_submission_file(seeded_db, submission_file_id=300)

        assert result["success"] is False
        assert "error" in result

    def test_student_id_and_filename_always_present(self, seeded_db):
        """Even on failure the identity fields must be present."""
        result = grade_single_submission_file(seeded_db, submission_file_id=302)

        assert "student_id" in result
        assert "filename" in result
        assert result["student_id"] == 3
        assert result["filename"] == "bob_hw1.ipynb"


# ===========================================================================
# 2. grade_session_batch (generator)
# ===========================================================================

class TestGradeSessionBatch:

    def test_empty_batch_yields_zero_summary(self, seeded_db):
        """Session with zero ungraded files yields exactly one summary event."""
        # Mark all ungraded files as graded to simulate empty batch
        for sfid in [300, 301, 302]:
            sf = seeded_db.get(SubmissionFile, sfid)
            sf.graded = True
        seeded_db.commit()

        events = list(grade_session_batch(seeded_db, session_id=10))

        assert len(events) == 1
        summary = events[0]
        assert summary["event"] == "summary"
        assert summary["total"] == 0
        assert summary["graded"] == 0
        assert summary["failed"] == 0
        assert summary["failures"] == []

    def test_all_success_event_order(self, seeded_db, monkeypatch):
        """
        For each file: checking → graded/failed in that order.
        Final summary is the very last event.
        """
        _patch_pipeline(monkeypatch)

        events = list(grade_session_batch(seeded_db, session_id=10))

        # Last event must always be summary
        assert events[-1]["event"] == "summary"

        # Build pairs: every non-summary event should alternate checking/result
        non_summary = [e for e in events if e["event"] != "summary"]
        for i in range(0, len(non_summary), 2):
            assert non_summary[i]["event"] == "checking", (
                f"Expected 'checking' at position {i}, got {non_summary[i]['event']!r}"
            )
            assert non_summary[i + 1]["event"] in ("graded", "failed"), (
                f"Expected 'graded'/'failed' at position {i+1}, got {non_summary[i+1]['event']!r}"
            )

    def test_all_success_summary_counts(self, seeded_db, monkeypatch):
        """
        With 3 ungraded files (300, 301 gradeable; 302 unmatched):
        - 300 and 301 should succeed
        - 302 should fail (not matched)
        - summary totals must match
        """
        _patch_pipeline(monkeypatch)

        events = list(grade_session_batch(seeded_db, session_id=10))
        summary = events[-1]

        assert summary["event"] == "summary"
        assert summary["total"] == 3          # 300, 301, 302 (303 is already graded)
        assert summary["graded"] == 2         # 300, 301
        assert summary["failed"] == 1         # 302 unmatched
        assert summary["graded"] + summary["failed"] == summary["total"]

    def test_mixed_batch_failed_events_present(self, seeded_db, monkeypatch):
        """Unmatched file 302 must produce a 'failed' event."""
        _patch_pipeline(monkeypatch)

        events = list(grade_session_batch(seeded_db, session_id=10))
        failed_events = [e for e in events if e["event"] == "failed"]

        assert len(failed_events) == 1
        assert failed_events[0]["student_id"] == 3
        assert failed_events[0]["filename"] == "bob_hw1.ipynb"
        assert isinstance(failed_events[0]["error"], str)

    def test_failures_collected_in_summary(self, seeded_db, monkeypatch):
        """Failure entries in summary['failures'] must have all required keys."""
        _patch_pipeline(monkeypatch)

        events = list(grade_session_batch(seeded_db, session_id=10))
        summary = events[-1]

        assert len(summary["failures"]) == 1
        failure = summary["failures"][0]
        assert "student_id" in failure
        assert "filename" in failure
        assert "error" in failure
        assert failure["student_id"] == 3
        assert failure["filename"] == "bob_hw1.ipynb"

    def test_generator_never_raises(self, seeded_db, monkeypatch):
        """Even if grade_single_submission_file raises, the generator must not propagate."""
        # Patch generate_feedback_and_persist at the source so every call explodes.
        # grade_session_batch has a last-resort try/except that catches this.
        with patch(
            "app.services.feedback.generate_feedback_and_persist",
            side_effect=Exception("nuclear failure"),
        ):
            try:
                events = list(grade_session_batch(seeded_db, session_id=10))
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"grade_session_batch raised an unexpected exception: {exc}")

        # Should still have a summary event at the end
        assert events[-1]["event"] == "summary"

    def test_already_graded_files_are_skipped(self, seeded_db, monkeypatch):
        """SubmissionFile 303 has graded=True — it must not appear in any event."""
        _patch_pipeline(monkeypatch)

        events = list(grade_session_batch(seeded_db, session_id=10))
        all_filenames = [
            e.get("filename") for e in events if e["event"] != "summary"
        ]

        assert "already_done.ipynb" not in all_filenames

    def test_checking_immediately_followed_by_result(self, seeded_db, monkeypatch):
        """
        The sequence must be: checking(A) → graded/failed(A) → checking(B) → ...
        Each 'checking' event's filename must match the immediately following result.
        """
        _patch_pipeline(monkeypatch)

        events = list(grade_session_batch(seeded_db, session_id=10))
        non_summary = [e for e in events if e["event"] != "summary"]

        for i in range(0, len(non_summary) - 1, 2):
            checking = non_summary[i]
            result = non_summary[i + 1]
            assert checking["event"] == "checking"
            assert result["event"] in ("graded", "failed")
            # The filename must be consistent across the pair
            assert checking["filename"] == result["filename"]
            assert checking["student_id"] == result["student_id"]

    def test_nonexistent_session_yields_empty_summary(self, seeded_db):
        """A session with no submissions yields a zero-count summary, no exception."""
        events = list(grade_session_batch(seeded_db, session_id=9999))

        assert len(events) == 1
        assert events[0]["event"] == "summary"
        assert events[0]["total"] == 0

    def test_graded_files_marked_in_db_after_success(self, seeded_db, monkeypatch):
        """After a successful batch, graded SubmissionFiles should have graded=True."""
        _patch_pipeline(monkeypatch)

        list(grade_session_batch(seeded_db, session_id=10))

        sf300 = seeded_db.get(SubmissionFile, 300)
        sf301 = seeded_db.get(SubmissionFile, 301)
        seeded_db.refresh(sf300)
        seeded_db.refresh(sf301)

        assert sf300.graded is True
        assert sf301.graded is True

    def test_corrupted_notebook_fails_gracefully(self, seeded_db, monkeypatch):
        """
        If one submission's notebook is corrupted (parse returns valid=False),
        that file yields a 'failed' event and the batch continues without raising.
        """
        call_count = {"n": 0}

        def selective_parse(path):
            call_count["n"] += 1
            # Make the first submission notebook look corrupted
            if "hw1.ipynb" in str(path) and "submissions" in str(path):
                return {"valid": False, "error": "notebook could not be parsed"}
            return _MOCK_NB

        def selective_structure(path):
            if "hw1.ipynb" in str(path) and "submissions" in str(path):
                return {"valid": False, "error": "notebook could not be parsed"}
            return _MOCK_NB_STRUCTURE

        monkeypatch.setattr("app.services.evaluator.parse_notebook_file", selective_parse)
        monkeypatch.setattr("app.services.evaluator.extract_notebook_structure", selective_structure)
        monkeypatch.setattr(
            "app.services.evaluator.call_gemini_for_evaluation",
            lambda _prompt: _good_gemini_response(),
        )

        try:
            events = list(grade_session_batch(seeded_db, session_id=10))
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"grade_session_batch raised unexpectedly: {exc}")

        summary = events[-1]
        assert summary["event"] == "summary"
        # At least one failure for the corrupted notebook; batch didn't crash
        assert summary["failed"] >= 1


# ===========================================================================
# 3. Endpoint  POST /api/v1/sessions/{session_id}/grade
# ===========================================================================

@pytest.fixture()
def api_client(db):
    """
    FastAPI TestClient with in-memory DB override.
    Yields (client, instructor_token, student_token, db).
    """
    from app.database import get_db
    from app.main import app

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    db.add_all([
        User(id=1, name="Prof", email="prof@x.com", hashed_password="h", role=UserRole.instructor),
        User(id=2, name="Alice", email="alice@x.com", hashed_password="h", role=UserRole.student),
        LMSSession(id=10, title="DS101"),
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


_ENDPOINT = "/api/v1/sessions/{}/grade"


class TestGradeSessionEndpoint:

    def test_unauthenticated_returns_401(self, api_client):
        c, *_ = api_client
        res = c.post(_ENDPOINT.format(10))
        assert res.status_code == 401

    def test_student_returns_403(self, api_client):
        c, _, student_token, *_ = api_client
        res = c.post(
            _ENDPOINT.format(10),
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert res.status_code == 403

    def test_nonexistent_session_returns_404(self, api_client):
        c, instructor_token, *_ = api_client
        res = c.post(
            _ENDPOINT.format(9999),
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert res.status_code == 404

    def test_empty_batch_returns_zero_summary(self, api_client):
        """Session with all files already graded → zero-count summary, 200 OK."""
        c, instructor_token, _, db = api_client
        sf = db.get(SubmissionFile, 300)
        sf.graded = True
        db.commit()

        res = c.post(
            _ENDPOINT.format(10),
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert res.status_code == 200
        body = res.json()

        assert "events" in body
        assert "summary" in body
        assert body["summary"]["total"] == 0
        assert body["summary"]["graded"] == 0
        assert body["summary"]["failed"] == 0

    def test_successful_batch_response_shape(self, api_client, monkeypatch):
        """Full pipeline mock — response has events list + correct summary."""
        c, instructor_token, *_ = api_client
        monkeypatch.setattr("app.services.evaluator.parse_notebook_file", lambda _: _MOCK_NB)
        monkeypatch.setattr("app.services.evaluator.extract_notebook_structure", lambda _: _MOCK_NB_STRUCTURE)
        monkeypatch.setattr(
            "app.services.evaluator.call_gemini_for_evaluation",
            lambda _: _good_gemini_response(),
        )

        res = c.post(
            _ENDPOINT.format(10),
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert res.status_code == 200
        body = res.json()

        assert isinstance(body["events"], list)
        assert len(body["events"]) >= 2  # at least checking + graded/failed + summary
        assert body["summary"]["event"] == "summary"
        assert body["summary"]["total"] >= 1

    def test_summary_equals_last_event(self, api_client, monkeypatch):
        """summary key must equal the last element of events."""
        c, instructor_token, *_ = api_client
        monkeypatch.setattr("app.services.evaluator.parse_notebook_file", lambda _: _MOCK_NB)
        monkeypatch.setattr("app.services.evaluator.extract_notebook_structure", lambda _: _MOCK_NB_STRUCTURE)
        monkeypatch.setattr(
            "app.services.evaluator.call_gemini_for_evaluation",
            lambda _: _good_gemini_response(),
        )

        res = c.post(
            _ENDPOINT.format(10),
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        body = res.json()
        assert body["summary"] == body["events"][-1]

    def test_mixed_batch_failures_in_summary(self, api_client, monkeypatch):
        """Add an unmatched file to the session; it should appear in failures."""
        c, instructor_token, _, db = api_client

        # Add a second student + submission with an unmatched file
        db.add(User(id=3, name="Bob", email="bob@x.com", hashed_password="h", role=UserRole.student))
        db.add(Submission(
            id=201, session_id=10, student_id=3,
            original_filename="bad.ipynb",
            uploaded_file_path="10/submissions/3/bad.ipynb",
        ))
        db.add(SubmissionFile(
            id=301, submission_id=201,
            matched_unsolved_file_id=None,
            original_filename="bad.ipynb",
            extracted_ipynb_path="10/submissions/3/bad.ipynb",
            graded=False,
        ))
        db.commit()

        monkeypatch.setattr("app.services.evaluator.parse_notebook_file", lambda _: _MOCK_NB)
        monkeypatch.setattr("app.services.evaluator.extract_notebook_structure", lambda _: _MOCK_NB_STRUCTURE)
        monkeypatch.setattr(
            "app.services.evaluator.call_gemini_for_evaluation",
            lambda _: _good_gemini_response(),
        )

        res = c.post(
            _ENDPOINT.format(10),
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert res.status_code == 200
        body = res.json()
        summary = body["summary"]

        assert summary["total"] == 2      # file 300 (good) + 301 (bad)
        assert summary["graded"] == 1
        assert summary["failed"] == 1
        assert len(summary["failures"]) == 1
        assert summary["failures"][0]["filename"] == "bad.ipynb"
