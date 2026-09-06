"""
Tests for backend/app/services/session_matcher.py -- Phase 3, Sub-feature 3.1.

All DB-dependent tests use an in-memory SQLite database built from the real
ORM models so there are zero external dependencies. Run with:

    cd backend
    python -m pytest tests/test_session_matcher.py -v

Cases covered
-------------
1. Exact title match -> "matched", high confidence.
2. Close typo/partial phrasing -> "matched" via string similarity alone
   (LLM fallback NOT invoked).
3. Two similarly-named sessions scoring close together -> "ambiguous",
   both returned as candidates.
4. Instruction referencing nothing that exists -> "no_match".
5. Vague instruction in the inconclusive middle band -> LLM fallback IS
   invoked, its JSON response parsed into the matched shape.
6. LLM fallback call raises an exception -> gracefully "no_match".
7. Sessions belonging to a different instructor are never matched, even
   with an identical title.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.session_matcher import match_instruction_to_session


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

def _make_instructor(db, *, user_id: int, email: str = None):
    from app.models.user import User, UserRole
    user = User(
        id=user_id,
        name=f"Instructor {user_id}",
        email=email or f"instructor{user_id}@test.com",
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


# ---------------------------------------------------------------------------
# 1. Exact title match
# ---------------------------------------------------------------------------

def test_exact_title_match(db):
    instructor = _make_instructor(db, user_id=1)
    _make_session(db, session_id=1, title="Week 8 Day 4", instructor_id=instructor.id)
    _make_session(db, session_id=2, title="Week 1 Day 1", instructor_id=instructor.id)

    result = match_instruction_to_session("Week 8 Day 4", instructor.id, db)

    assert result["status"] == "matched"
    assert result["session_id"] == 1
    assert result["session_title"] == "Week 8 Day 4"
    assert result["confidence"] == 1.0


# ---------------------------------------------------------------------------
# 2. Typo/partial phrasing -> matched via string similarity alone
# ---------------------------------------------------------------------------

def test_typo_partial_phrasing_matches_without_llm(db):
    instructor = _make_instructor(db, user_id=1)
    _make_session(db, session_id=1, title="Week 8 - Day 3", instructor_id=instructor.id)
    _make_session(db, session_id=2, title="Week 1 Day 1", instructor_id=instructor.id)

    with patch("app.services.session_matcher.llm_provider.call_llm") as mock_call_llm:
        result = match_instruction_to_session("week 8 day 3", instructor.id, db)

    assert result["status"] == "matched"
    assert result["session_id"] == 1
    mock_call_llm.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Two similarly-named sessions scoring close together -> ambiguous
# ---------------------------------------------------------------------------

def test_ambiguous_two_similar_sessions(db):
    instructor = _make_instructor(db, user_id=1)
    _make_session(db, session_id=1, title="Week 8 Day 3", instructor_id=instructor.id)
    _make_session(db, session_id=2, title="Week 8 Day 3 Extra", instructor_id=instructor.id)
    # An unrelated "Week N Day M"-shaped session must NOT be swept into the
    # ambiguous set just because it shares the words "week"/"day" — only the
    # day/week NUMBER should count as a real signal.
    _make_session(db, session_id=3, title="Week 1 Day 1", instructor_id=instructor.id)

    with patch("app.services.session_matcher.llm_provider.call_llm") as mock_call_llm:
        result = match_instruction_to_session(
            "week 8 day 3 extra credit", instructor.id, db
        )

    assert result["status"] == "ambiguous"
    mock_call_llm.assert_not_called()
    candidate_ids = {c["session_id"] for c in result["candidates"]}
    assert candidate_ids == {1, 2}
    # sorted by confidence descending
    confidences = [c["confidence"] for c in result["candidates"]]
    assert confidences == sorted(confidences, reverse=True)


def test_ambiguous_candidates_never_a_single_item_list(db):
    """
    Regression test: found via real dev-DB verification during 3.3.
    "grade week 10" against real titles ("Week 10 Day 3", "Week 1 Day 1",
    "Week 3 Day 1", ...) scored the top session at 0.5625 with every other
    candidate below SESSION_MATCH_THRESHOLD (0.55) but within
    CLEAR_WINNER_MARGIN (0.15) of the top score — not a clear winner, but
    filtering the "ambiguous" candidate list to only entries >= 0.55 left
    exactly one entry, a nonsensical "ambiguous, pick one of: just this one"
    response. The candidate list must include every entry within margin of
    the best score, regardless of whether each one individually clears
    SESSION_MATCH_THRESHOLD, so "ambiguous" always means 2+ real candidates.
    """
    instructor = _make_instructor(db, user_id=1)
    _make_session(db, session_id=1, title="Week 10 Day 3", instructor_id=instructor.id)
    _make_session(db, session_id=2, title="Week 1 Day 1", instructor_id=instructor.id)
    _make_session(db, session_id=3, title="Week 3 Day 1", instructor_id=instructor.id)

    result = match_instruction_to_session("grade week 10", instructor.id, db)

    assert result["status"] == "ambiguous"
    assert len(result["candidates"]) >= 2


# ---------------------------------------------------------------------------
# 4. No match
# ---------------------------------------------------------------------------

def test_no_match(db):
    instructor = _make_instructor(db, user_id=1)
    _make_session(db, session_id=1, title="Week 1 Day 1", instructor_id=instructor.id)
    _make_session(db, session_id=2, title="Week 2 Day 1", instructor_id=instructor.id)

    result = match_instruction_to_session(
        "please grade the intro to blockchain workshop", instructor.id, db
    )

    assert result["status"] == "no_match"


# ---------------------------------------------------------------------------
# 5. Vague middle-band instruction -> LLM fallback invoked
# ---------------------------------------------------------------------------

def test_llm_fallback_invoked_for_vague_instruction(db):
    instructor = _make_instructor(db, user_id=1)
    _make_session(
        db, session_id=1, title="Week 9 Day 2 - RAG Pipeline", instructor_id=instructor.id
    )
    _make_session(
        db, session_id=2, title="Week 9 Day 3 - RAG Evaluation", instructor_id=instructor.id
    )
    _make_session(db, session_id=3, title="Week 1 Day 1", instructor_id=instructor.id)

    with patch("app.services.session_matcher.llm_provider.call_llm") as mock_call_llm:
        mock_call_llm.return_value = '{"status": "matched", "session_id": 1}'
        result = match_instruction_to_session(
            "grade the rag session please", instructor.id, db
        )

    mock_call_llm.assert_called_once()
    assert result["status"] == "matched"
    assert result["session_id"] == 1
    assert result["session_title"] == "Week 9 Day 2 - RAG Pipeline"


def test_llm_fallback_ambiguous_response_parsed(db):
    instructor = _make_instructor(db, user_id=1)
    _make_session(
        db, session_id=1, title="Week 9 Day 2 - RAG Pipeline", instructor_id=instructor.id
    )
    _make_session(
        db, session_id=2, title="Week 9 Day 3 - RAG Evaluation", instructor_id=instructor.id
    )
    _make_session(db, session_id=3, title="Week 1 Day 1", instructor_id=instructor.id)

    with patch("app.services.session_matcher.llm_provider.call_llm") as mock_call_llm:
        mock_call_llm.return_value = '{"status": "ambiguous", "session_ids": [1, 2]}'
        result = match_instruction_to_session(
            "grade the rag session please", instructor.id, db
        )

    assert result["status"] == "ambiguous"
    assert {c["session_id"] for c in result["candidates"]} == {1, 2}


# ---------------------------------------------------------------------------
# 6. LLM fallback exception -> no_match, never raises
# ---------------------------------------------------------------------------

def test_llm_fallback_exception_returns_no_match(db):
    instructor = _make_instructor(db, user_id=1)
    _make_session(
        db, session_id=1, title="Week 9 Day 2 - RAG Pipeline", instructor_id=instructor.id
    )
    _make_session(db, session_id=2, title="Week 1 Day 1", instructor_id=instructor.id)

    with patch("app.services.session_matcher.llm_provider.call_llm") as mock_call_llm:
        mock_call_llm.side_effect = RuntimeError("boom")
        result = match_instruction_to_session(
            "grade the rag session please", instructor.id, db
        )

    assert result["status"] == "no_match"


# ---------------------------------------------------------------------------
# 7. Cross-instructor isolation
# ---------------------------------------------------------------------------

def test_sessions_scoped_to_instructor(db):
    instructor_a = _make_instructor(db, user_id=1, email="a@test.com")
    instructor_b = _make_instructor(db, user_id=2, email="b@test.com")
    _make_session(db, session_id=1, title="Week 8 Day 4", instructor_id=instructor_a.id)
    _make_session(db, session_id=2, title="Week 8 Day 4", instructor_id=instructor_b.id)

    result_a = match_instruction_to_session("Week 8 Day 4", instructor_a.id, db)
    result_b = match_instruction_to_session("Week 8 Day 4", instructor_b.id, db)

    assert result_a["status"] == "matched"
    assert result_a["session_id"] == 1
    assert result_b["status"] == "matched"
    assert result_b["session_id"] == 2
