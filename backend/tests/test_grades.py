"""
Tests for GET /api/v1/sessions/{session_id}/grades and its /mine and
/{student_id} variants — Sub-feature 2.9's grade-averaging fairness fix.

Bug fixed: combined_score used to be
    sum(per_file scores) / len(per_file)
which rewards a student who skips an assignment entirely (smaller
denominator) the same as one who submits everything and does poorly on it.
The fix divides by the session's TOTAL assignment file count instead, so a
missing or unmatched submission is effectively scored 0 for that file
rather than excluded from the average.

Run with:
    cd backend
    python -m pytest tests/test_grades.py -v

Cases covered
─────────────
1. Student graded on all 4 of 4 assignment files → combined = sum/4.
2. Student graded on only 3 of 4 (4th never submitted) → combined still
   divides by 4, not 3.
3. Student with one submitted file that matched no assignment (ungraded,
   matched_unsolved_file_id=None) → that file counts as 0, denominator
   still 4.
4. Same fairness math applies through all three read endpoints:
   session_grade_report (instructor, all students), /mine (student), and
   /{student_id} (instructor, one student).
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.grade import Grade
from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.submission_file import SubmissionFile
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole
from app.services.auth import create_access_token


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture()
def db():
    """In-memory SQLite database wired to the real ORM models."""
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
def client(db):
    """
    TestClient backed by in-memory DB, seeded with a session that has 4
    UnsolvedFile rows (ids 1-4) and three students (A, B, C) whose
    submissions/grades are built per-test to exercise the fairness fix.

    Seeded:
      - instructor (id=1)
      - student A (id=2), student B (id=3), student C (id=4)
      - LMS session (id=10, title='Week 1 Day 1')
      - 4 UnsolvedFile rows (ids 1-4) under session 10

    Yields (TestClient, instructor_token, {student_id: token}).
    """

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    instructor = User(
        id=1, name="Prof Test", email="prof@test.com",
        hashed_password="hash", role=UserRole.instructor,
    )
    student_a = User(id=2, name="Student A", email="a@test.com", hashed_password="hash", role=UserRole.student)
    student_b = User(id=3, name="Student B", email="b@test.com", hashed_password="hash", role=UserRole.student)
    student_c = User(id=4, name="Student C", email="c@test.com", hashed_password="hash", role=UserRole.student)
    lms_session = LMSSession(id=10, title="Week 1 Day 1")
    db.add_all([instructor, student_a, student_b, student_c, lms_session])
    db.commit()

    unsolved_files = [
        UnsolvedFile(
            id=i, session_id=10,
            original_filename=f"hw{i}.ipynb",
            file_path=f"10/assignments/hw{i}.ipynb",
            parsed_requirements_text=f"Assignment {i} instructions.",
        )
        for i in range(1, 5)
    ]
    db.add_all(unsolved_files)
    db.commit()

    tokens = {
        1: create_access_token(1, UserRole.instructor),
        2: create_access_token(2, UserRole.student),
        3: create_access_token(3, UserRole.student),
        4: create_access_token(4, UserRole.student),
    }

    with TestClient(app) as c:
        yield c, tokens[1], tokens

    app.dependency_overrides.clear()


def _add_graded_submission_file(
    db, *, submission_id: int, matched_unsolved_file_id: int | None,
    filename: str, score: float | None,
) -> None:
    """
    Create one SubmissionFile under `submission_id`. If `score` is given,
    also attach a Grade row (graded=True); otherwise leaves it ungraded
    (simulates an unmatched/ungraded file — matched_unsolved_file_id may
    still be None for the "no confident match" case).
    """
    sf = SubmissionFile(
        submission_id=submission_id,
        matched_unsolved_file_id=matched_unsolved_file_id,
        original_filename=filename,
        extracted_ipynb_path=f"10/submissions/x/{filename}",
        graded=score is not None,
    )
    db.add(sf)
    db.flush()
    if score is not None:
        db.add(Grade(
            submission_file_id=sf.id,
            score=score,
            feedback_text=f"Score: {score}/10.",
            rationale_json=None,
            graded_at=datetime.now(timezone.utc),
        ))
    db.commit()


# ===========================================================================
# Tests
# ===========================================================================

class TestGradeAveragingFairness:

    # ── 1. Graded on all 4 of 4 ────────────────────────────────────────────

    def test_student_graded_on_all_assignments_divides_by_total(self, client, db):
        c, instr_token, _tokens = client
        submission = Submission(
            id=100, session_id=10, student_id=2,
            original_filename="a.zip", uploaded_file_path="10/submissions/2/a.zip",
        )
        db.add(submission)
        db.commit()
        scores = [8.0, 6.0, 10.0, 4.0]  # sum = 28
        for uf_id, score in zip(range(1, 5), scores):
            _add_graded_submission_file(
                db, submission_id=100, matched_unsolved_file_id=uf_id,
                filename=f"hw{uf_id}.ipynb", score=score,
            )

        res = c.get(
            "/api/v1/sessions/10/grades/2",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["per_file"]) == 4
        assert data["combined_score"] == pytest.approx(28.0 / 4)  # == 7.0

    # ── 2. Graded on 3 of 4 — 4th assignment never submitted ───────────────

    def test_student_missing_one_submission_still_divides_by_total(self, client, db):
        c, instr_token, _tokens = client
        submission = Submission(
            id=101, session_id=10, student_id=3,
            original_filename="b.zip", uploaded_file_path="10/submissions/3/b.zip",
        )
        db.add(submission)
        db.commit()
        # Student B only submitted/matched assignments 1, 2, 3 — never touched #4.
        scores = [8.0, 8.0, 8.0]  # sum = 24
        for uf_id, score in zip(range(1, 4), scores):
            _add_graded_submission_file(
                db, submission_id=101, matched_unsolved_file_id=uf_id,
                filename=f"hw{uf_id}.ipynb", score=score,
            )

        res = c.get(
            "/api/v1/sessions/10/grades/3",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["per_file"]) == 3
        # Bug would have given 24/3 = 8.0 — correct fairness-adjusted value is 24/4 = 6.0
        assert data["combined_score"] == pytest.approx(24.0 / 4)
        assert data["combined_score"] != pytest.approx(24.0 / 3)

    # ── 3. One submitted file matched no assignment (ungraded) ─────────────

    def test_unmatched_submission_file_counts_as_zero(self, client, db):
        c, instr_token, _tokens = client
        submission = Submission(
            id=102, session_id=10, student_id=4,
            original_filename="c.zip", uploaded_file_path="10/submissions/4/c.zip",
        )
        db.add(submission)
        db.commit()
        # Assignments 1-3 matched and graded; the 4th submitted file matched
        # nothing (matched_unsolved_file_id=None) so it was never graded.
        scores = [5.0, 5.0, 5.0]  # sum = 15
        for uf_id, score in zip(range(1, 4), scores):
            _add_graded_submission_file(
                db, submission_id=102, matched_unsolved_file_id=uf_id,
                filename=f"hw{uf_id}.ipynb", score=score,
            )
        _add_graded_submission_file(
            db, submission_id=102, matched_unsolved_file_id=None,
            filename="mystery.ipynb", score=None,
        )

        res = c.get(
            "/api/v1/sessions/10/grades/4",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["per_file"]) == 3  # ungraded file has no Grade row, excluded from per_file
        # Bug would have given 15/3 = 5.0 — fairness-adjusted value is 15/4 = 3.75,
        # which then rounds to the nearest 0.5 increment -> 4.0.
        assert data["combined_score"] == pytest.approx(4.0)
        assert data["combined_score"] != pytest.approx(15.0 / 3)

    # ── 4. Same math holds across all three endpoints ───────────────────────

    def test_fairness_math_consistent_across_session_report_and_mine(self, client, db):
        c, instr_token, tokens = client
        submission = Submission(
            id=103, session_id=10, student_id=3,
            original_filename="b.zip", uploaded_file_path="10/submissions/3/b.zip",
        )
        db.add(submission)
        db.commit()
        scores = [8.0, 8.0, 8.0]  # sum = 24, missing 1 of 4 assignments
        for uf_id, score in zip(range(1, 4), scores):
            _add_graded_submission_file(
                db, submission_id=103, matched_unsolved_file_id=uf_id,
                filename=f"hw{uf_id}.ipynb", score=score,
            )

        # session_grade_report (instructor, all students)
        report_res = c.get(
            "/api/v1/sessions/10/grades",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert report_res.status_code == 200
        report_data = report_res.json()
        student_b_summary = next(s for s in report_data["students"] if s["student_id"] == 3)
        assert student_b_summary["combined_score"] == pytest.approx(24.0 / 4)

        # /mine (student B viewing their own grades)
        mine_res = c.get(
            "/api/v1/sessions/10/grades/mine",
            headers={"Authorization": f"Bearer {tokens[3]}"},
        )
        assert mine_res.status_code == 200
        assert mine_res.json()["combined_score"] == pytest.approx(24.0 / 4)

    # ── 5. combined_score rounds to the nearest 0.5, not nearest 0.01 ───────

    def test_combined_score_rounds_down_to_nearest_half(self, client, db):
        """
        4 scores of 8.5, 8, 10, 10 sum to 36.5; divided by 4 assignments
        that's 9.125 — must round to the nearest 0.5 increment (9.0), not
        plain 2-decimal rounding (9.12/9.13). Real bug found via manual
        testing: combined_score previously used round(combined, 2).
        """
        c, instr_token, _tokens = client
        submission = Submission(
            id=104, session_id=10, student_id=2,
            original_filename="d.zip", uploaded_file_path="10/submissions/2/d.zip",
        )
        db.add(submission)
        db.commit()
        scores = [8.5, 8.0, 10.0, 10.0]  # sum = 36.5, /4 = 9.125
        for uf_id, score in zip(range(1, 5), scores):
            _add_graded_submission_file(
                db, submission_id=104, matched_unsolved_file_id=uf_id,
                filename=f"hw{uf_id}.ipynb", score=score,
            )

        res = c.get(
            "/api/v1/sessions/10/grades/2",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["combined_score"] == 9.0

    def test_combined_score_rounds_up_to_nearest_half(self, client, db):
        """9.5+9.5+9.5+9.0 sum to 37.5; divided by 4 that's 9.375 — nearest
        0.5 increment is 9.5, confirming this is genuine nearest-0.5
        rounding (not truncation, which would give 9.0 instead)."""
        c, instr_token, _tokens = client
        submission = Submission(
            id=105, session_id=10, student_id=3,
            original_filename="e.zip", uploaded_file_path="10/submissions/3/e.zip",
        )
        db.add(submission)
        db.commit()
        scores = [9.5, 9.5, 9.5, 9.0]  # sum = 37.5, /4 = 9.375
        for uf_id, score in zip(range(1, 5), scores):
            _add_graded_submission_file(
                db, submission_id=105, matched_unsolved_file_id=uf_id,
                filename=f"hw{uf_id}.ipynb", score=score,
            )

        res = c.get(
            "/api/v1/sessions/10/grades/3",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["combined_score"] == 9.5

    def test_no_submission_at_all_scores_zero_not_none(self, client, db):
        """
        A student with zero submissions has an empty per_file list but a
        session with assignment files still has a total_assignment_files > 0,
        so combined_score is 0.0 (0 of 4 assignments done) — consistent with
        the fairness fix's own logic, not None.
        """
        c, instr_token, tokens = client
        res = c.get(
            "/api/v1/sessions/10/grades/2",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["per_file"] == []
        assert data["combined_score"] == pytest.approx(0.0)

    def test_no_assignment_files_returns_none(self, client, db):
        """
        combined_score is None only when the session has zero UnsolvedFile
        rows at all (nothing to divide by) — an edge case distinct from a
        student simply not having submitted anything yet.
        """
        c, instr_token, tokens = client
        empty_session = LMSSession(id=20, title="Empty Session")
        db.add(empty_session)
        db.commit()

        res = c.get(
            "/api/v1/sessions/20/grades/2",
            headers={"Authorization": f"Bearer {instr_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["per_file"] == []
        assert data["combined_score"] is None
