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
