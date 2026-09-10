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
7. Matching is shared across instructors, not scoped to a caller: another
   instructor's session matches normally by title. (Two different
   instructors' sessions sharing one exact title used to be reachable and
   correctly "ambiguous" -- that case is no longer possible to construct
   at all since bugfix-session-naming-attribution made session titles
   globally unique, so the DB itself now prevents the scenario this test
   used to cover.)
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

    result = match_instruction_to_session("Week 8 Day 4", db)

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
        result = match_instruction_to_session("week 8 day 3", db)

    assert result["status"] == "matched"
    assert result["session_id"] == 1
    mock_call_llm.assert_not_called()


def test_swapped_week_day_numbers_not_confused(db):
    """
    Regression test: found via real Swagger testing against the live dev DB.
    "Week 2 Day 1" and "Week 1 Day 2" share the exact same token set
    ({week, 1, day, 2}) under plain bag-of-words scoring, so an instruction
    naming either one scored both titles at confidence 1.0 and returned
    "ambiguous" — wrong, since the instruction unambiguously names one
    specific session. The matcher must track which number follows "week"
    and which follows "day", not just whether the number appears somewhere
    in the title.
    """
    instructor = _make_instructor(db, user_id=1)
    _make_session(db, session_id=1, title="Week 2 Day 1", instructor_id=instructor.id)
    _make_session(db, session_id=2, title="Week 1 Day 2", instructor_id=instructor.id)

    with patch("app.services.session_matcher.llm_provider.call_llm") as mock_call_llm:
        result = match_instruction_to_session("grade week 2 day 1", db)

    assert result["status"] == "matched"
    assert result["session_id"] == 1
    assert result["session_title"] == "Week 2 Day 1"
    mock_call_llm.assert_not_called()


def test_digit_only_near_miss_resolves_confidently(db):
    """
    Regression test: found via real Swagger testing against the live dev DB
    (5 real sessions), one level past test_swapped_week_day_numbers_not_
    confused. That fix (merging "week"+number into one token) stopped exact
    ties, but "Week 1 Day 1" and "Week 3 Day 1" still shared "Day 1" with
    the target "Week 2 Day 1", and their mismatched week number ("1"/"3" vs
    "2") still scored ~0.8 similarity under plain character-level
    SequenceMatcher (4 of 5 characters of "week1"/"week3" match "week2") —
    close enough to stay inside the ambiguity margin against the correct
    match, so "grade week 2 day 1" against these real titles still returned
    "ambiguous" instead of resolving to the one correct session.

    Fix: numeric tokens must match EXACTLY or score 0 — a single-digit
    difference is a different week, not a "close" one. This must resolve
    to session 3 alone, confidently, with NO margin/threshold change.
    """
    instructor = _make_instructor(db, user_id=1)
    _make_session(db, session_id=1, title="Week 1 Day 1", instructor_id=instructor.id)
    _make_session(db, session_id=2, title="Week 1 Day 2", instructor_id=instructor.id)
    _make_session(db, session_id=3, title="Week 2 Day 1", instructor_id=instructor.id)
    _make_session(db, session_id=4, title="Week 2 Day 2", instructor_id=instructor.id)
    _make_session(db, session_id=5, title="Week 3 Day 1", instructor_id=instructor.id)

    with patch("app.services.session_matcher.llm_provider.call_llm") as mock_call_llm:
        result = match_instruction_to_session("grade week 2 day 1", db)

    assert result["status"] == "matched"
    assert result["session_id"] == 3
    assert result["session_title"] == "Week 2 Day 1"
    assert result["confidence"] == 1.0
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
        result = match_instruction_to_session("week 8 day 3 extra credit", db)

    assert result["status"] == "ambiguous"
    mock_call_llm.assert_not_called()
    candidate_ids = {c["session_id"] for c in result["candidates"]}
    assert candidate_ids == {1, 2}
    # sorted by confidence descending
    confidences = [c["confidence"] for c in result["candidates"]]
    assert confidences == sorted(confidences, reverse=True)


def test_ambiguous_candidates_never_a_single_item_list(db):
    """
    Regression test: found via real dev-DB verification during 3.3 (the
    candidate-filtering bug itself, not the scoring bug fixed later in 3.1).
    The "ambiguous" candidate list must include every entry within
    CLEAR_WINNER_MARGIN of the best score, regardless of whether each one
    individually clears SESSION_MATCH_THRESHOLD, so "ambiguous" always means
    2+ real candidates — never a nonsensical "ambiguous, pick one of: just
    this one" response.

    Scenario: the instruction only names the week, not the day, so two
    sessions sharing that week with different days are genuinely and
    correctly tied (an unrelated week is still excluded by the digit-exact
    numeric matching added later — see test_digit_only_near_miss_resolves_
    confidently for that fix).
    """
    instructor = _make_instructor(db, user_id=1)
    _make_session(db, session_id=1, title="Week 10 Day 1", instructor_id=instructor.id)
    _make_session(db, session_id=2, title="Week 10 Day 3", instructor_id=instructor.id)
    _make_session(db, session_id=3, title="Week 1 Day 1", instructor_id=instructor.id)

    result = match_instruction_to_session("grade week 10", db)

    assert result["status"] == "ambiguous"
    assert len(result["candidates"]) >= 2
    candidate_ids = {c["session_id"] for c in result["candidates"]}
    assert candidate_ids == {1, 2}


# ---------------------------------------------------------------------------
# 4. No match
# ---------------------------------------------------------------------------

def test_no_match(db):
    instructor = _make_instructor(db, user_id=1)
    _make_session(db, session_id=1, title="Week 1 Day 1", instructor_id=instructor.id)
    _make_session(db, session_id=2, title="Week 2 Day 1", instructor_id=instructor.id)

    result = match_instruction_to_session("please grade the intro to blockchain workshop", db)

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
        result = match_instruction_to_session("grade the rag session please", db)

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
        result = match_instruction_to_session("grade the rag session please", db)

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
        result = match_instruction_to_session("grade the rag session please", db)

    assert result["status"] == "no_match"


# ---------------------------------------------------------------------------
# 7. Shared workspace: matching is not scoped to a calling instructor
# ---------------------------------------------------------------------------

def test_matches_another_instructors_session(db):
    """
    Instructor access is a shared faculty workspace (see README) — any
    instructor's instruction can resolve to any session, regardless of who
    created it. There is no per-caller identity in match_instruction_to_
    session at all any more; this just pins that a session owned by one
    instructor matches normally when looked up by title alone.
    """
    instructor_a = _make_instructor(db, user_id=1, email="a@test.com")
    instructor_b = _make_instructor(db, user_id=2, email="b@test.com")
    _make_session(db, session_id=1, title="Week 8 Day 4", instructor_id=instructor_a.id)
    _make_session(db, session_id=2, title="Week 1 Day 1", instructor_id=instructor_b.id)

    result = match_instruction_to_session("Week 8 Day 4", db)

    assert result["status"] == "matched"
    assert result["session_id"] == 1


# ---------------------------------------------------------------------------
# 8. Post-Phase-5 regression: a wrong week/day number must never fuzzy-match
#    off a spare filler word (bugfix-post-phase5, Fix 1)
# ---------------------------------------------------------------------------

def _seed_week_day_grid(db):
    """A realistic set of Week N Day M sessions for one instructor."""
    instructor = _make_instructor(db, user_id=1)
    titles = [
        (1, "Week 1 Day 1"), (2, "Week 1 Day 2"),
        (3, "Week 2 Day 1"), (4, "Week 2 Day 2"),
        (5, "Week 3 Day 1"), (6, "Week 3 Day 2"),
    ]
    for sid, title in titles:
        _make_session(db, session_id=sid, title=title, instructor_id=instructor.id)
    return instructor


def test_nonexistent_week_number_returns_no_match(db):
    # Week 7 exists nowhere; Day 2 does. Before the fix the title's unmatched
    # "week7" token earned ~0.2 fuzzy credit from the verb "grade", pushing
    # this to a false "ambiguous". A wrong week must rule the match out.
    instructor = _seed_week_day_grid(db)
    with patch("app.services.session_matcher.llm_provider.call_llm") as mock_llm:
        mock_llm.return_value = '{"status": "no_match"}'
        result = match_instruction_to_session("grade week 7 day 2", db)
    assert result["status"] == "no_match"


def test_nonexistent_day_number_returns_no_match(db):
    # Week 2 exists, Day 7 does not. This already behaved correctly; pinned so
    # a future change to the number rule can't silently regress it.
    instructor = _seed_week_day_grid(db)
    with patch("app.services.session_matcher.llm_provider.call_llm") as mock_llm:
        mock_llm.return_value = '{"status": "no_match"}'
        result = match_instruction_to_session("grade week 2 day 7", db)
    assert result["status"] == "no_match"


def test_filler_verb_does_not_change_outcome(db):
    # The core symptom: prefixing a filler verb must not change the result for
    # the same week/day numbers. Both a real match and a non-match are pinned
    # to be identical with and without "grade".
    instructor = _seed_week_day_grid(db)

    # Real session — matched either way.
    with_verb = match_instruction_to_session("grade week 3 day 1", db)
    without_verb = match_instruction_to_session("week 3 day 1", db)
    assert with_verb["status"] == without_verb["status"] == "matched"
    assert with_verb["session_id"] == without_verb["session_id"]

    # Nonexistent week — no_match either way (was: ambiguous only with the verb).
    with patch("app.services.session_matcher.llm_provider.call_llm") as mock_llm:
        mock_llm.return_value = '{"status": "no_match"}'
        with_verb_bad = match_instruction_to_session("grade week 7 day 2", db)
        without_verb_bad = match_instruction_to_session("week 7 day 2", db)
    assert with_verb_bad["status"] == without_verb_bad["status"] == "no_match"


def test_correct_instruction_still_matches_with_filler(db):
    # Guard against over-correction: a genuine, wordy instruction still matches.
    instructor = _seed_week_day_grid(db)
    result = match_instruction_to_session("please grade the week 3 day 1 assignments", db)
    assert result["status"] == "matched"
    assert result["session_title"] == "Week 3 Day 1"
