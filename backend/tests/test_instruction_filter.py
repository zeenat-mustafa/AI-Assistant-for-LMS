"""
Tests for backend/app/services/instruction_filter.py -- Phase 3, Sub-feature 3.2.

All DB-dependent tests use an in-memory SQLite database built from the real
ORM models so there are zero external dependencies. Run with:

    cd backend
    python -m pytest tests/test_instruction_filter.py -v

Cases covered
-------------
1. No name mentioned at all -> {"scope": "all"}.
2. A unique student's full name mentioned -> correct "student" match.
3. A unique student's first name only mentioned -> correct "student" match.
4. Two students sharing the same first name in the same session, instruction
   mentions only the first name -> "ambiguous", both returned.
5. A name mentioned that isn't any student in this session (typo) -> "not_found".
6. A student who exists in the DB globally but has no submission in THIS
   session's candidate pool, mentioned by name -> "not_found" (must not
   incorrectly match students outside this session).
7. Course-topic vocabulary from the session's own title (e.g. "Linear
   Regression") is never mistaken for a name reference -> "all".
8. Exclusionary phrasing ("except", "without", "other than") immediately
   before a matched name -> {"scope": "unsupported", ...}, NEVER falls
   through to matching that student as an inclusion target.

No LLM fallback was built for this sub-feature (see the report accompanying
this PR) — "student"/"all"/"ambiguous"/"not_found" is a closed set with no
shape to route "the second student" or "everyone except X" into, and the
narrowed Step 0 scope ("all vs one specific named student, that's it") gives
no LLM disambiguation a destination — so there is nothing to mock/test here.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.instruction_filter import parse_grading_filter


# ---------------------------------------------------------------------------
# In-memory DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    """
    Fresh in-memory SQLite database using the project's real SQLAlchemy models.
    Yields a connected Session; tears it down after each test.
    """
    from app.database import Base
    import app.models.user     # noqa: F401
    import app.models.session  # noqa: F401
    import app.models.submission  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------

def _make_instructor(db, *, user_id: int = 1):
    from app.models.user import User, UserRole
    user = User(
        id=user_id,
        name=f"Instructor {user_id}",
        email=f"instructor{user_id}@test.com",
        hashed_password="x",
        role=UserRole.instructor,
    )
    db.add(user)
    db.flush()
    return user


def _make_session(db, *, session_id: int, title: str, instructor_id: int):
    from app.models.session import LMSSession
    lms_session = LMSSession(id=session_id, title=title, instructor_id=instructor_id)
    db.add(lms_session)
    db.flush()
    return lms_session


def _make_student(db, *, user_id: int, name: str):
    from app.models.user import User, UserRole
    student = User(
        id=user_id,
        name=name,
        email=f"student{user_id}@test.com",
        hashed_password="x",
        role=UserRole.student,
    )
    db.add(student)
    db.flush()
    return student


def _make_submission(db, *, submission_id: int, session_id: int, student_id: int):
    from app.models.submission import Submission
    submission = Submission(
        id=submission_id,
        session_id=session_id,
        student_id=student_id,
        original_filename=f"hw_{student_id}.ipynb",
        uploaded_file_path=f"{session_id}/submissions/{student_id}/hw_{student_id}.ipynb",
    )
    db.add(submission)
    db.flush()
    return submission


# ---------------------------------------------------------------------------
# 1. No name mentioned -> all
# ---------------------------------------------------------------------------

def test_no_name_mentioned_returns_all(db):
    instructor = _make_instructor(db)
    _make_session(db, session_id=1, title="Week 8 Day 3", instructor_id=instructor.id)
    student = _make_student(db, user_id=10, name="Ali Khan")
    _make_submission(db, submission_id=100, session_id=1, student_id=student.id)

    result = parse_grading_filter("grade week 8 day 3", 1, db)

    assert result == {"scope": "all"}


# ---------------------------------------------------------------------------
# 2. Unique full name -> student match
# ---------------------------------------------------------------------------

def test_unique_full_name_matches_student(db):
    instructor = _make_instructor(db)
    _make_session(db, session_id=1, title="Week 8 Day 3", instructor_id=instructor.id)
    ali = _make_student(db, user_id=10, name="Ali Khan")
    sara = _make_student(db, user_id=11, name="Sara Ahmed")
    _make_submission(db, submission_id=100, session_id=1, student_id=ali.id)
    _make_submission(db, submission_id=101, session_id=1, student_id=sara.id)

    result = parse_grading_filter("grade only Ali Khan's submission", 1, db)

    assert result == {"scope": "student", "student_id": ali.id, "student_name": "Ali Khan"}


# ---------------------------------------------------------------------------
# 3. Unique first name only -> student match
# ---------------------------------------------------------------------------

def test_unique_first_name_matches_student(db):
    instructor = _make_instructor(db)
    _make_session(db, session_id=1, title="Week 8 Day 3", instructor_id=instructor.id)
    ali = _make_student(db, user_id=10, name="Ali Khan")
    sara = _make_student(db, user_id=11, name="Sara Ahmed")
    _make_submission(db, submission_id=100, session_id=1, student_id=ali.id)
    _make_submission(db, submission_id=101, session_id=1, student_id=sara.id)

    result = parse_grading_filter("grade only Ali's submission", 1, db)

    assert result == {"scope": "student", "student_id": ali.id, "student_name": "Ali Khan"}


# ---------------------------------------------------------------------------
# 4. Two students sharing the same first name -> ambiguous
# ---------------------------------------------------------------------------

def test_ambiguous_shared_first_name(db):
    instructor = _make_instructor(db)
    _make_session(db, session_id=1, title="Week 8 Day 3", instructor_id=instructor.id)
    ali_khan = _make_student(db, user_id=10, name="Ali Khan")
    ali_raza = _make_student(db, user_id=11, name="Ali Raza")
    _make_submission(db, submission_id=100, session_id=1, student_id=ali_khan.id)
    _make_submission(db, submission_id=101, session_id=1, student_id=ali_raza.id)

    result = parse_grading_filter("grade only Ali's submission", 1, db)

    assert result["scope"] == "ambiguous"
    candidate_ids = {c["student_id"] for c in result["candidates"]}
    assert candidate_ids == {ali_khan.id, ali_raza.id}


# ---------------------------------------------------------------------------
# 5. Name mentioned that isn't any student anywhere (typo) -> not_found
# ---------------------------------------------------------------------------

def test_typo_name_returns_not_found(db):
    instructor = _make_instructor(db)
    _make_session(db, session_id=1, title="Week 8 Day 3", instructor_id=instructor.id)
    ali = _make_student(db, user_id=10, name="Ali Khan")
    _make_submission(db, submission_id=100, session_id=1, student_id=ali.id)

    result = parse_grading_filter("grade Xavier's submission", 1, db)

    assert result == {"scope": "not_found", "attempted_name": "Xavier"}


# ---------------------------------------------------------------------------
# 6. Student exists globally but not in this session -> not_found
# ---------------------------------------------------------------------------

def test_name_from_different_session_returns_not_found(db):
    instructor = _make_instructor(db)
    _make_session(db, session_id=1, title="Week 8 Day 3", instructor_id=instructor.id)
    _make_session(db, session_id=2, title="Week 1 Day 1", instructor_id=instructor.id)
    ali = _make_student(db, user_id=10, name="Ali Khan")
    bob = _make_student(db, user_id=11, name="Bob Smith")
    _make_submission(db, submission_id=100, session_id=1, student_id=ali.id)
    # Bob only submitted to session 2, not session 1.
    _make_submission(db, submission_id=101, session_id=2, student_id=bob.id)

    result = parse_grading_filter("grade Bob's submission", 1, db)

    assert result == {"scope": "not_found", "attempted_name": "Bob"}


# ---------------------------------------------------------------------------
# 7. Session-title vocabulary is never mistaken for a name reference
# ---------------------------------------------------------------------------

def test_session_title_words_not_mistaken_for_names(db):
    instructor = _make_instructor(db)
    _make_session(
        db, session_id=1, title="Week 2 Day 1 - Linear Regression", instructor_id=instructor.id
    )
    student = _make_student(db, user_id=10, name="Ali Khan")
    _make_submission(db, submission_id=100, session_id=1, student_id=student.id)

    result = parse_grading_filter("grade the linear regression assignments", 1, db)

    assert result == {"scope": "all"}


# ---------------------------------------------------------------------------
# 8. Exclusionary phrasing -> unsupported, never a silent "student" match
# ---------------------------------------------------------------------------

def test_exclusionary_except_returns_unsupported(db):
    instructor = _make_instructor(db)
    _make_session(db, session_id=1, title="Week 8 Day 3", instructor_id=instructor.id)
    ali = _make_student(db, user_id=10, name="Ali Khan")
    sara = _make_student(db, user_id=11, name="Sara Ahmed")
    _make_submission(db, submission_id=100, session_id=1, student_id=ali.id)
    _make_submission(db, submission_id=101, session_id=1, student_id=sara.id)

    result = parse_grading_filter("grade everyone except Ali", 1, db)

    assert result == {
        "scope": "unsupported",
        "reason": "exclusionary filters not yet supported",
    }


def test_exclusionary_without_returns_unsupported(db):
    instructor = _make_instructor(db)
    _make_session(db, session_id=1, title="Week 8 Day 3", instructor_id=instructor.id)
    ali = _make_student(db, user_id=10, name="Ali Khan")
    _make_submission(db, submission_id=100, session_id=1, student_id=ali.id)

    result = parse_grading_filter("grade all without Ali", 1, db)

    assert result["scope"] == "unsupported"


def test_exclusionary_other_than_returns_unsupported(db):
    instructor = _make_instructor(db)
    _make_session(db, session_id=1, title="Week 8 Day 3", instructor_id=instructor.id)
    ali = _make_student(db, user_id=10, name="Ali Khan")
    _make_submission(db, submission_id=100, session_id=1, student_id=ali.id)

    result = parse_grading_filter("grade everyone other than Ali", 1, db)

    assert result["scope"] == "unsupported"
