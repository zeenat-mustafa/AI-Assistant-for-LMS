"""
Unit tests for app.services.chat_session_resolver — Phase 7, Sub-feature 7.5.

retrieve() is replaced with a fake returning prepared, deterministic results
per session scope, so every branch of the signed-off flow is exercised
exactly. Real retrieval behavior is verified separately (Step 6).

Run with:
    cd backend
    python -m pytest tests/test_chat_session_resolver.py -v
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.session import LMSSession
from app.services import chat_session_resolver as resolver
from app.services.chat_session_resolver import (
    BROAD_MIN_SIMILARITY,
    MAX_CLARIFICATION_CANDIDATES,
    build_clarification_message,
    resolve_session,
)

STUDENT_ID = 42


@pytest.fixture()
def db():
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        LMSSession(id=1, title="Week 1 Day 1"),
        LMSSession(id=2, title="Week 1 Day 2"),
        LMSSession(id=3, title="Week 2 Day 1"),
        LMSSession(id=4, title="Week 2 Day 2"),
    ])
    session.commit()
    yield session
    session.close()


def _chunk(session_id, similarity):
    return {"session_id": session_id, "similarity": similarity, "source_type": "notebook",
            "source_file_id": session_id * 10, "chunk_text": "x"}


def _fake_retrieve(monkeypatch, broad, scoped=None):
    """broad: results for session_id=None; scoped: {session_id: results}."""
    calls = []

    def fake(query, session_id=None, top_k=5, min_similarity=0.35):
        calls.append(session_id)
        if session_id is None:
            return list(broad)
        return list((scoped or {}).get(session_id, []))

    monkeypatch.setattr(resolver, "retrieve", fake)
    return calls


# ===========================================================================
# Branch 1 — current session, question relevant to it
# ===========================================================================

class TestCurrentSessionRelevant:

    def test_stays_in_current_session_when_no_other_session_clearly_beats_it(self, db, monkeypatch):
        _fake_retrieve(
            monkeypatch,
            broad=[_chunk(1, 0.60), _chunk(2, 0.62)],
            scoped={1: [_chunk(1, 0.60)]},
        )
        result = resolve_session(db, STUDENT_ID, "q", current_session_id=1)
        assert (result.status, result.session_id, result.resolution) == ("resolved", 1, "current_session")
        assert result.session_title == "Week 1 Day 1"

    def test_follow_up_with_zero_retrieval_stays_in_current_session(self, db, monkeypatch):
        """"Can you go deeper on that?" retrieves nothing — must not ask again."""
        _fake_retrieve(monkeypatch, broad=[], scoped={1: []})
        result = resolve_session(db, STUDENT_ID, "Can you go deeper on that?", current_session_id=1)
        assert (result.status, result.session_id, result.resolution) == ("resolved", 1, "current_session")

    def test_other_session_above_min_but_within_redirect_margin_stays(self, db, monkeypatch):
        _fake_retrieve(monkeypatch, broad=[_chunk(2, 0.70)], scoped={1: [_chunk(1, 0.60)]})
        result = resolve_session(db, STUDENT_ID, "q", current_session_id=1)
        assert (result.session_id, result.resolution) == (1, "current_session")

    def test_other_session_with_big_margin_but_below_min_similarity_stays(self, db, monkeypatch):
        _fake_retrieve(monkeypatch, broad=[_chunk(2, BROAD_MIN_SIMILARITY - 0.05)], scoped={1: []})
        result = resolve_session(db, STUDENT_ID, "q", current_session_id=1)
        assert (result.session_id, result.resolution) == (1, "current_session")


# ===========================================================================
# Branch 2 — current session, question clearly about a different session
# ===========================================================================

class TestCurrentSessionIrrelevantRedirect:

    def test_redirects_when_another_session_is_clearly_better(self, db, monkeypatch):
        _fake_retrieve(
            monkeypatch,
            broad=[_chunk(3, 0.82), _chunk(3, 0.70), _chunk(1, 0.40)],
            scoped={1: [_chunk(1, 0.40)]},
        )
        result = resolve_session(db, STUDENT_ID, "q", current_session_id=1)
        assert (result.status, result.session_id, result.resolution) == ("resolved", 3, "redirected")
        assert result.session_title == "Week 2 Day 1"

    def test_redirects_when_current_session_has_no_relevant_material_at_all(self, db, monkeypatch):
        _fake_retrieve(monkeypatch, broad=[_chunk(4, 0.66)], scoped={1: []})
        result = resolve_session(db, STUDENT_ID, "q", current_session_id=1)
        assert (result.session_id, result.resolution) == (4, "redirected")

    def test_picks_the_best_other_session(self, db, monkeypatch):
        _fake_retrieve(monkeypatch, broad=[_chunk(2, 0.60), _chunk(3, 0.80)], scoped={1: []})
        result = resolve_session(db, STUDENT_ID, "q", current_session_id=1)
        assert (result.session_id, result.resolution) == (3, "redirected")


# ===========================================================================
# Branch 3 — no context, broad search resolves confidently
# ===========================================================================

class TestNoContextBroadSearchResolves:

    def test_resolves_single_session_above_min(self, db, monkeypatch):
        calls = _fake_retrieve(monkeypatch, broad=[_chunk(2, 0.85), _chunk(2, 0.60)])
        result = resolve_session(db, STUDENT_ID, "q")
        assert (result.status, result.session_id, result.resolution) == ("resolved", 2, "broad_search")
        assert calls == [None]  # no scoped search without context

    def test_resolves_clear_winner_by_similarity_margin(self, db, monkeypatch):
        _fake_retrieve(monkeypatch, broad=[_chunk(3, 0.70), _chunk(4, 0.50), _chunk(4, 0.48)])
        result = resolve_session(db, STUDENT_ID, "q")
        assert (result.session_id, result.resolution) == (3, "broad_search")

    def test_resolves_clear_winner_by_hit_share_despite_small_margin(self, db, monkeypatch):
        """Real probe shape: pivot-table question — 9/10 hits in one session."""
        _fake_retrieve(monkeypatch, broad=[_chunk(4, 0.66)] * 9 + [_chunk(3, 0.62)])
        result = resolve_session(db, STUDENT_ID, "q")
        assert (result.session_id, result.resolution) == (4, "broad_search")

    def test_min_similarity_is_inclusive(self, db, monkeypatch):
        _fake_retrieve(monkeypatch, broad=[_chunk(1, BROAD_MIN_SIMILARITY)])
        assert resolve_session(db, STUDENT_ID, "q").status == "resolved"


# ===========================================================================
# Branch 4 — ambiguous → clarification, never a fabricated match
# ===========================================================================

class TestAmbiguousAsksForClarification:

    def test_single_session_below_min_asks_with_that_one_candidate(self, db, monkeypatch):
        """Real probe shape: 'the TODO in this assignment' — one session at 0.538."""
        _fake_retrieve(monkeypatch, broad=[_chunk(2, 0.538)] * 4)
        result = resolve_session(db, STUDENT_ID, "What should I do for the TODO in this assignment?")
        assert result.status == "clarification_needed"
        assert result.session_id is None and result.resolution is None
        assert [c["session_id"] for c in result.candidates] == [2]

    def test_close_split_resolves_as_broad_topic_search(self, db, monkeypatch):
        """Fix 2: a multi-session topic with no clear winner but scores >= 0.35
        resolves as broad_search with session_id=None instead of asking."""
        _fake_retrieve(
            monkeypatch,
            broad=[_chunk(1, 0.73)] * 5 + [_chunk(2, 0.68)] * 3 + [_chunk(3, 0.60)] * 2,
        )
        result = resolve_session(db, STUDENT_ID, "q")
        assert result.status == "resolved"
        assert result.session_id is None
        assert result.resolution == "broad_search"

    def test_close_split_below_topic_threshold_asks_with_ranked_candidates(self, db, monkeypatch):
        """When no session clears even BROAD_TOPIC_MIN_SIMILARITY (0.35), still ask."""
        _fake_retrieve(
            monkeypatch,
            broad=[_chunk(1, 0.30)] * 5 + [_chunk(2, 0.28)] * 3,
        )
        result = resolve_session(db, STUDENT_ID, "q")
        assert result.status == "clarification_needed"
        assert [c["session_id"] for c in result.candidates] == [1, 2]

    def test_candidates_capped_below_topic_threshold(self, db, monkeypatch):
        """Candidates are only offered when all scores are below BROAD_TOPIC_MIN_SIMILARITY."""
        _fake_retrieve(monkeypatch, broad=[_chunk(s, 0.25) for s in (1, 2, 3, 4)])
        result = resolve_session(db, STUDENT_ID, "q")
        assert result.status == "clarification_needed"
        assert len(result.candidates) == MAX_CLARIFICATION_CANDIDATES

    def test_zero_results_without_context_resolves_as_conversational(self, db, monkeypatch):
        """When retrieval returns nothing (no material in DB or uniformly low
        similarity), treat as conversational rather than asking which session."""
        _fake_retrieve(monkeypatch, broad=[])
        result = resolve_session(db, STUDENT_ID, "What is the capital of France?")
        assert result.status == "resolved"
        assert result.resolution == "conversational"
        assert result.session_id is None


# ===========================================================================
# Robustness
# ===========================================================================

class TestRobustness:

    def test_chunks_from_deleted_sessions_are_ignored(self, db, monkeypatch):
        _fake_retrieve(monkeypatch, broad=[_chunk(99, 0.95), _chunk(2, 0.70)])
        result = resolve_session(db, STUDENT_ID, "q")
        assert (result.status, result.session_id) == ("resolved", 2)

    def test_never_raises_without_context_asks(self, db, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("chroma down")
        monkeypatch.setattr(resolver, "retrieve", boom)
        result = resolve_session(db, STUDENT_ID, "q")
        assert result.status == "clarification_needed"

    def test_never_raises_with_context_stays_in_current(self, db, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("chroma down")
        monkeypatch.setattr(resolver, "retrieve", boom)
        result = resolve_session(db, STUDENT_ID, "q", current_session_id=3)
        assert (result.status, result.session_id, result.resolution) == ("resolved", 3, "current_session")
        assert result.session_title == "Week 2 Day 1"


# ===========================================================================
# Vague-reference guard (7.5 Step 6 finding, option (b))
# ===========================================================================

# Both real phrasings from Step 6, with their REAL live similarity scores —
# one landed below BROAD_MIN_SIMILARITY, one above. Both must ask.
BORDERLINE_TODO_PHRASINGS = [
    ("What should I do for the TODO in this assignment?", 0.5376),
    ("what do I do for the TODO in this assignment", 0.5664),
]


class TestVagueSessionReferenceGuard:

    @pytest.mark.parametrize("question,similarity", BORDERLINE_TODO_PHRASINGS)
    def test_both_borderline_todo_phrasings_ask_without_context(self, db, monkeypatch, question, similarity):
        _fake_retrieve(monkeypatch, broad=[_chunk(2, similarity)] * 4)
        result = resolve_session(db, STUDENT_ID, question)
        assert result.status == "clarification_needed"
        assert [c["session_id"] for c in result.candidates] == [2]

    def test_vague_reference_asks_even_with_a_very_confident_match(self, db, monkeypatch):
        _fake_retrieve(monkeypatch, broad=[_chunk(3, 0.95)] * 10)
        result = resolve_session(db, STUDENT_ID, "How do I start this lab?")
        assert result.status == "clarification_needed"

    @pytest.mark.parametrize("question,similarity", BORDERLINE_TODO_PHRASINGS)
    def test_with_page_context_vague_reference_means_the_current_session(
        self, db, monkeypatch, question, similarity,
    ):
        _fake_retrieve(monkeypatch, broad=[_chunk(2, similarity)] * 4, scoped={2: [_chunk(2, similarity)]})
        result = resolve_session(db, STUDENT_ID, question, current_session_id=2)
        assert (result.status, result.session_id, result.resolution) == ("resolved", 2, "current_session")

    def test_vague_reference_with_zero_results_asks_with_no_candidates(self, db, monkeypatch):
        _fake_retrieve(monkeypatch, broad=[])
        result = resolve_session(db, STUDENT_ID, "What's due for this week?")
        assert (result.status, result.candidates) == ("clarification_needed", [])

    @pytest.mark.parametrize("question", [
        "What should I do for the TODO in this assignment?",
        "what do I do for the TODO in this assignment",
        "How do I start THIS LAB?",
        "Can you explain this notebook",
        "what is this homework about",
        "help with this exercise",
        "what is this task asking",
        "what did we cover this week",
        "summarize this session",
    ])
    def test_pattern_matches_vague_references(self, question):
        assert resolver.has_vague_session_reference(question)

    @pytest.mark.parametrize("question", [
        "What does this code do?\n\ncalculator.invoke(\"25*18\")",  # pasted code is specific
        "How does YOLOv8 object detection work?",
        "What is a pivot table in pandas?",
        "What does the assignment in Week 2 Day 1 cover?",
        "Explain thistle plants",
    ])
    def test_pattern_does_not_match_specific_questions(self, question):
        assert not resolver.has_vague_session_reference(question)

    def test_specific_question_still_resolves_normally(self, db, monkeypatch):
        _fake_retrieve(monkeypatch, broad=[_chunk(4, 0.66)] * 9 + [_chunk(3, 0.62)])
        result = resolve_session(db, STUDENT_ID, "What is a pivot table in pandas?")
        assert (result.status, result.session_id) == ("resolved", 4)


class TestClarificationMessage:

    def test_multiple_candidates_matches_phase3_tone(self):
        message = build_clarification_message(
            [{"session_title": "Week 2 Day 1"}, {"session_title": "Week 2 Day 2"}]
        )
        assert message == (
            "I found a few sessions that could match — did you mean one of these? Week 2 Day 1, Week 2 Day 2"
        )

    def test_single_candidate(self):
        assert build_clarification_message([{"session_title": "Week 4 Day 1"}]) == (
            "I'm not sure which session that question is about — did you mean Week 4 Day 1?"
        )

    def test_no_candidates(self):
        assert build_clarification_message([]) == (
            "I couldn't find course material matching that question. Which session is it about?"
        )


# ===========================================================================
# is_conversational helper (Fix 1)
# ===========================================================================

class TestIsConversational:

    @pytest.mark.parametrize("text", [
        "hi", "hello", "hey", "Hi!", "Hello.", "HELLO",
        "thanks", "thank you", "thx", "ty",
        "ok", "okay", "ok thanks", "yes", "no", "yeah", "nah",
        "bye", "goodbye",
        "got it", "gotcha",
        "cool", "great", "awesome",
    ])
    def test_greetings_and_small_talk_are_conversational(self, text):
        from app.services.chat_session_resolver import is_conversational
        assert is_conversational(text), f"Expected {text!r} to be conversational"

    @pytest.mark.parametrize("text", [
        "what is a pandas dataframe",
        "how does bind_tools work",
        "explain the ReAct loop",
        "what should I do for the TODO",
        "evaluation of AI models",
        "hi how does the agent work",   # 5 words, has "agent" → not caught
        "how are you",  # Bug fix: looks like greeting but "how" makes it a question
        "what are you",  # Bug fix: same - question word prevents conversational match
        "why",  # Bug fix: single question word
        "",
    ])
    def test_course_questions_are_not_conversational(self, text):
        from app.services.chat_session_resolver import is_conversational
        assert not is_conversational(text), f"Expected {text!r} NOT to be conversational"


# ===========================================================================
# Conversational bypass path in resolve_session (Fix 1)
# ===========================================================================

class TestConversationalBypassInResolver:

    def test_greeting_returns_conversational_status_without_chroma_call(self, db, monkeypatch):
        """retrieve() must never be called for a greeting — bypass fires first."""
        retrieve_called = []

        def fake_retrieve(*args, **kwargs):
            retrieve_called.append(args)
            return []

        monkeypatch.setattr(resolver, "retrieve", fake_retrieve)
        result = resolve_session(db, STUDENT_ID, "hi")
        assert result.status == "conversational"
        assert result.session_id is None
        assert retrieve_called == []  # No Chroma call at all.

    def test_greeting_with_current_session_id_still_bypasses(self, db, monkeypatch):
        retrieve_called = []
        monkeypatch.setattr(resolver, "retrieve", lambda *a, **kw: retrieve_called.append(a) or [])
        result = resolve_session(db, STUDENT_ID, "thanks", current_session_id=7)
        assert result.status == "conversational"
        assert retrieve_called == []


# ===========================================================================
# Broad multi-session topic path (Fix 2)
# ===========================================================================

class TestBroadTopicResolution:

    def test_multi_session_topic_resolves_with_no_session_id(self, db, monkeypatch):
        """Scores 0.73/0.68/0.60 — no clear winner but all above 0.35."""
        _fake_retrieve(
            monkeypatch,
            broad=[_chunk(1, 0.73)] * 5 + [_chunk(2, 0.68)] * 3 + [_chunk(3, 0.60)] * 2,
        )
        result = resolve_session(db, STUDENT_ID, "what is evaluation of AI")
        assert result.status == "resolved"
        assert result.session_id is None
        assert result.resolution == "broad_search"

    def test_topic_just_above_threshold_resolves_broadly(self, db, monkeypatch):
        """Top score 0.36 — just above BROAD_TOPIC_MIN_SIMILARITY (0.35)."""
        _fake_retrieve(
            monkeypatch,
            broad=[_chunk(1, 0.36)] * 2 + [_chunk(2, 0.35)] * 2,
        )
        result = resolve_session(db, STUDENT_ID, "some general topic")
        assert result.status == "resolved"
        assert result.session_id is None

    def test_topic_below_threshold_still_asks(self, db, monkeypatch):
        """Top score 0.34 — below BROAD_TOPIC_MIN_SIMILARITY (0.35) → clarification."""
        _fake_retrieve(
            monkeypatch,
            broad=[_chunk(1, 0.34)] * 3 + [_chunk(2, 0.30)] * 2,
        )
        result = resolve_session(db, STUDENT_ID, "some obscure question")
        assert result.status == "clarification_needed"

    def test_high_confidence_clear_winner_still_uses_session_specific_path(self, db, monkeypatch):
        """Score 0.73 with a clear 0.10+ lead — single-session broad_search, not None."""
        _fake_retrieve(
            monkeypatch,
            broad=[_chunk(1, 0.73)] * 5 + [_chunk(2, 0.60)] * 2,
        )
        result = resolve_session(db, STUDENT_ID, "what does bind_tools do")
        # top=0.73, runner_up=0.60, gap=0.13 >= BROAD_CLEAR_MARGIN(0.10) → clear winner
        assert result.status == "resolved"
        assert result.session_id == 1   # specific session, not None
        assert result.resolution == "broad_search"
