"""
Tests for backend/app/routers/sessions.py -- bugfix-session-naming-attribution.

Covers Part 1 (global, cross-instructor title uniqueness on create) and
Part 2 (the new PATCH /sessions/{id} rename endpoint, enforcing the same
global uniqueness). Session matching/grading itself is covered elsewhere
(test_session_matcher.py, test_chat.py, test_feedback.py).

Run with:
    cd backend
    python -m pytest tests/test_sessions.py -v
"""

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.session import LMSSession
from app.models.user import User, UserRole
from app.services.auth import create_access_token


@pytest.fixture()
def db() -> Iterator:
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
    TestClient backed by an in-memory DB seeded with two DIFFERENT
    instructors (id=1, id=2) and one pre-existing session (id=10,
    "Week 3 Day 1") owned by instructor 1 -- exactly the shape needed to
    prove uniqueness is enforced ACROSS instructors, not just within one.

    Yields (TestClient, instructor1_token, instructor2_token).
    """

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    instructor1 = User(
        id=1, name="Demo Instructor", email="instructor@demo.com",
        hashed_password="hash", role=UserRole.instructor,
    )
    instructor2 = User(
        id=2, name="Demo Instructor 2", email="instructor2@demo.com",
        hashed_password="hash", role=UserRole.instructor,
    )
    db.add_all([instructor1, instructor2])
    db.commit()

    existing = LMSSession(id=10, title="Week 3 Day 1", instructor_id=1)
    db.add(existing)
    db.commit()

    tokens = {
        1: create_access_token(1, UserRole.instructor),
        2: create_access_token(2, UserRole.instructor),
    }

    with TestClient(app) as c:
        yield c, tokens[1], tokens[2]

    app.dependency_overrides.clear()


class TestGlobalTitleUniquenessOnCreate:

    def test_different_instructor_cannot_create_a_duplicate_title(self, client, db):
        """
        The exact scenario this fix exists for: instructor 2 tries to
        create a session titled identically to instructor 1's existing
        one. Must be a real 409, not a silent success (which the old
        per-instructor constraint would have allowed).
        """
        c, _instr1_token, instr2_token = client

        res = c.post(
            "/api/v1/sessions",
            json={"title": "Week 3 Day 1"},
            headers={"Authorization": f"Bearer {instr2_token}"},
        )

        assert res.status_code == 409
        detail = res.json()["detail"]
        assert "Week 3 Day 1" in detail
        assert "already exists" in detail
        # Names the real conflicting session and its actual owner.
        assert "id=10" in detail
        assert "Demo Instructor" in detail

        # And no second row was created.
        assert db.query(LMSSession).filter(LMSSession.title == "Week 3 Day 1").count() == 1

    def test_same_instructor_still_cannot_create_a_duplicate_of_their_own(self, client):
        """Sanity check: the pre-existing per-instructor behaviour still works
        now that it's a special case of the global rule."""
        c, instr1_token, _instr2_token = client

        res = c.post(
            "/api/v1/sessions",
            json={"title": "Week 3 Day 1"},
            headers={"Authorization": f"Bearer {instr1_token}"},
        )

        assert res.status_code == 409

    def test_a_genuinely_new_title_still_succeeds_for_any_instructor(self, client):
        c, _instr1_token, instr2_token = client

        res = c.post(
            "/api/v1/sessions",
            json={"title": "Week 4 Day 1"},
            headers={"Authorization": f"Bearer {instr2_token}"},
        )

        assert res.status_code == 201
        assert res.json()["title"] == "Week 4 Day 1"
        assert res.json()["instructor_id"] == 2


class TestRenameSession:

    def test_any_instructor_can_rename_any_session(self, client, db):
        """Shared workspace: renaming isn't restricted to the creator."""
        c, _instr1_token, instr2_token = client

        res = c.patch(
            "/api/v1/sessions/10",
            json={"title": "Week 3 Day 1 (renamed)"},
            headers={"Authorization": f"Bearer {instr2_token}"},
        )

        assert res.status_code == 200
        assert res.json()["title"] == "Week 3 Day 1 (renamed)"
        # Ownership is untouched by the rename -- still instructor 1's.
        assert res.json()["instructor_id"] == 1

        db.refresh(db.get(LMSSession, 10))
        assert db.get(LMSSession, 10).title == "Week 3 Day 1 (renamed)"

    def test_rename_enforces_the_same_global_uniqueness_as_create(self, client, db):
        c, instr1_token, _instr2_token = client
        other = LMSSession(id=11, title="Week 4 Day 1", instructor_id=2)
        db.add(other)
        db.commit()

        res = c.patch(
            "/api/v1/sessions/10",
            json={"title": "Week 4 Day 1"},
            headers={"Authorization": f"Bearer {instr1_token}"},
        )

        assert res.status_code == 409
        detail = res.json()["detail"]
        assert "id=11" in detail
        assert "Demo Instructor 2" in detail
        # The original title must be untouched after a rejected rename.
        db.refresh(db.get(LMSSession, 10))
        assert db.get(LMSSession, 10).title == "Week 3 Day 1"

    def test_renaming_a_session_to_its_own_current_title_is_not_a_conflict(self, client):
        """
        exclude_session_id must exempt a session from colliding with
        itself -- otherwise saving with no real change would 409.
        """
        c, instr1_token, _instr2_token = client

        res = c.patch(
            "/api/v1/sessions/10",
            json={"title": "Week 3 Day 1"},
            headers={"Authorization": f"Bearer {instr1_token}"},
        )

        assert res.status_code == 200
        assert res.json()["title"] == "Week 3 Day 1"

    def test_rename_rejects_an_empty_title(self, client):
        c, instr1_token, _instr2_token = client

        res = c.patch(
            "/api/v1/sessions/10",
            json={"title": "   "},
            headers={"Authorization": f"Bearer {instr1_token}"},
        )

        assert res.status_code == 422

    def test_rename_of_nonexistent_session_returns_404(self, client):
        c, instr1_token, _instr2_token = client

        res = c.patch(
            "/api/v1/sessions/99999",
            json={"title": "Whatever"},
            headers={"Authorization": f"Bearer {instr1_token}"},
        )

        assert res.status_code == 404


# ===========================================================================
# DELETE /sessions/{id} -- quiz attempt cleanup (Phase 7.8 audit fix, Item 3)
# ===========================================================================
# No automated test existed for this cleanup before this fix, for any scope
# type -- these are new, not a rewrite. Covers the real gap the audit found
# ("assignment_file"-scoped attempts referencing the deleted session were
# never matched) alongside the two scopes the original fix did cover, plus
# isolation from an unrelated session/scope.

import json as _json

from app.models.quiz_attempt import QuizAttempt
from app.models.unsolved_file import UnsolvedFile


def _attempt(student_id, scope_type, scope_detail):
    return QuizAttempt(
        student_id=student_id,
        scope_type=scope_type,
        scope_detail=_json.dumps(scope_detail),
        questions_json=_json.dumps([{"question": "q", "options": ["a", "b", "c", "d"],
                                      "correct_option_index": 0, "source_citation": "x"}] * 5),
    )


class TestDeleteSessionQuizAttemptCleanup:

    @pytest.fixture()
    def seeded(self, db):
        """A fresh db (from the module's own `db` fixture) with an
        instructor, a student, session 10 (with one unsolved file, id 100),
        and an unrelated session 20 -- everything the cleanup cases need."""
        db.add_all([
            User(id=1, name="Prof", email="prof@x.com", hashed_password="h", role=UserRole.instructor),
            User(id=2, name="Alice", email="alice@x.com", hashed_password="h", role=UserRole.student),
            LMSSession(id=10, title="Week 3 Day 1", instructor_id=1),
            LMSSession(id=20, title="Week 4 Day 1", instructor_id=1),
        ])
        db.commit()
        db.add(UnsolvedFile(
            id=100, session_id=10, original_filename="lab.ipynb",
            file_path="10/assignments/lab.ipynb",
        ))
        db.commit()

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        yield db
        app.dependency_overrides.clear()

    def _delete(self, db, session_id=10):
        client = TestClient(app)
        token = create_access_token(1, UserRole.instructor)
        res = client.delete(f"/api/v1/sessions/{session_id}", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 204, res.text

    def test_session_scoped_attempt_is_deleted(self, seeded):
        db = seeded
        db.add(_attempt(2, "session", {"session_id": 10}))
        db.commit()
        self._delete(db)
        assert db.query(QuizAttempt).count() == 0

    def test_multiple_sessions_scoped_attempt_is_deleted(self, seeded):
        db = seeded
        db.add(_attempt(2, "multiple_sessions", {"session_ids": [10, 20]}))
        db.commit()
        self._delete(db)
        assert db.query(QuizAttempt).count() == 0

    def test_assignment_file_scoped_attempt_is_deleted(self, seeded):
        """The real gap the audit found: this scope has no session_id or
        session_ids key at all -- only unsolved_file_id, which belongs to
        the session being deleted."""
        db = seeded
        db.add(_attempt(2, "assignment_file", {"unsolved_file_id": 100}))
        db.commit()
        self._delete(db)
        assert db.query(QuizAttempt).count() == 0

    def test_topic_and_uploaded_file_scopes_are_never_touched(self, seeded):
        """These scopes structurally can't reference any session -- confirms
        they survive, rather than being accidentally swept up."""
        db = seeded
        db.add(_attempt(2, "topic", {"topic_text": "pandas"}))
        db.add(_attempt(2, "uploaded_file", {"original_filename": "x.pptx", "file_type": "pptx"}))
        db.commit()
        self._delete(db)
        assert db.query(QuizAttempt).count() == 2

    def test_attempt_for_a_different_session_survives(self, seeded):
        db = seeded
        db.add(_attempt(2, "session", {"session_id": 20}))
        db.commit()
        self._delete(db, session_id=10)
        assert db.query(QuizAttempt).count() == 1
        assert _json.loads(db.query(QuizAttempt).one().scope_detail) == {"session_id": 20}

    def test_multiple_sessions_attempt_not_referencing_the_deleted_session_survives(self, seeded):
        db = seeded
        db.add(_attempt(2, "multiple_sessions", {"session_ids": [20]}))
        db.commit()
        self._delete(db, session_id=10)
        assert db.query(QuizAttempt).count() == 1

    def test_mixed_scopes_only_the_referencing_ones_are_deleted(self, seeded):
        db = seeded
        db.add_all([
            _attempt(2, "session", {"session_id": 10}),
            _attempt(2, "assignment_file", {"unsolved_file_id": 100}),
            _attempt(2, "multiple_sessions", {"session_ids": [10, 20]}),
            _attempt(2, "session", {"session_id": 20}),
            _attempt(2, "topic", {"topic_text": "pandas"}),
        ])
        db.commit()
        self._delete(db, session_id=10)

        remaining = db.query(QuizAttempt).all()
        assert len(remaining) == 2
        assert {a.scope_type for a in remaining} == {"session", "topic"}
