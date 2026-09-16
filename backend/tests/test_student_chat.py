"""
Endpoint tests for POST /api/v1/student-chat/stream — Phase 7, Sub-feature 7.5.

In-memory DB, real router + real SSE framing + real 7.4 memory service; the
resolver, retrieval and streaming model call are faked so every branch is
deterministic and no quota is used. The real-Gemini, real-retrieval runs of
this endpoint live in tests/adversarial/ (excluded from the default suite).

Run with:
    cd backend
    python -m pytest tests/test_student_chat.py -v
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.conversation import ConversationMessage, ConversationThread, MessageRole
from app.models.lecture_file import LectureFile
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole
from app.services import chat_session_resolver
from app.services import student_chat_service
from app.services.auth import create_access_token
from app.services.chat_safety import SCOPE_SAFETY_RULE
from app.services.chat_session_resolver import ResolutionResult
from app.services.llm_provider import LLMProviderError

URL = "/api/v1/student-chat/stream"
STUDENT_ID, INSTRUCTOR_ID = 1, 2

NOTEBOOK_CHUNK = {
    "source_type": "notebook", "source_file_id": 20, "session_id": 7, "cell_index": 17,
    "cell_type": "code", "chunk_text": 'calculator.invoke("25*18")', "similarity": 0.85,
    "chunk_id": "notebook:20:17",
}
LECTURE_CHUNK = {
    "source_type": "lecture", "source_file_id": 1, "session_id": 7, "slide_number": 23,
    "source": "slide_text", "chunk_text": "Common Failure Modes in Agent Systems", "similarity": 0.57,
    "chunk_id": "lecture:5",
}


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture()
def db():
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        User(id=STUDENT_ID, name="Stu", email="student@demo.com", hashed_password="h", role=UserRole.student),
        User(id=INSTRUCTOR_ID, name="Ins", email="instructor@demo.com", hashed_password="h",
             role=UserRole.instructor),
        LMSSession(id=7, title="Week 4 Day 1"),
        LMSSession(id=5, title="Week 3 Day 1"),
    ])
    session.commit()
    session.add_all([
        UnsolvedFile(id=20, session_id=7, original_filename="Week 10_Lab3.ipynb", file_path="7/a/lab3.ipynb"),
        LectureFile(id=1, session_id=7, original_filename="week 10_day1.pptx", file_path="7/lectures/w10.pptx"),
    ])
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def student_headers():
    return {"Authorization": f"Bearer {create_access_token(STUDENT_ID, UserRole.student)}"}


@pytest.fixture()
def fakes(monkeypatch):
    """Default fakes: resolves to session 7, retrieves two chunks, streams 3 tokens."""
    state = {
        "resolution": ResolutionResult(
            status="resolved", session_id=7, session_title="Week 4 Day 1", resolution="current_session",
        ),
        "retrieved": [NOTEBOOK_CHUNK, LECTURE_CHUNK],
        "tokens": ["It calls ", "the calculator ", "tool."],
        "resolve_calls": [], "retrieve_calls": [], "prompts": [],
    }

    def fake_resolve(db, student_id, question, current_session_id=None):
        state["resolve_calls"].append((student_id, question, current_session_id))
        return state["resolution"]

    def fake_retrieve(query, session_id=None, top_k=5, min_similarity=0.35):
        state["retrieve_calls"].append((query, session_id, top_k, min_similarity))
        return list(state["retrieved"])

    def fake_stream(prompt, purpose="fast"):
        state["prompts"].append((prompt, purpose))
        for item in state["tokens"]:
            if isinstance(item, Exception):
                raise item
            yield item

    monkeypatch.setattr(student_chat_service, "resolve_session", fake_resolve)
    monkeypatch.setattr(student_chat_service, "retrieve", fake_retrieve)
    monkeypatch.setattr(student_chat_service, "call_llm_stream", fake_stream)
    return state


def _events(response) -> list[dict]:
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [f for f in response.text.split("\n\n") if f.strip()]
    events = []
    for frame in frames:
        assert frame.startswith("data: "), frame
        events.append(json.loads(frame[len("data: "):]))
    return events


# ===========================================================================
# Auth + validation
# ===========================================================================

class TestAuthAndValidation:

    def test_requires_authentication(self, client, fakes):
        assert client.post(URL, json={"question": "hi"}).status_code == 401

    def test_instructors_are_forbidden(self, client, fakes):
        headers = {"Authorization": f"Bearer {create_access_token(INSTRUCTOR_ID, UserRole.instructor)}"}
        assert client.post(URL, json={"question": "hi"}, headers=headers).status_code == 403

    @pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "   "}])
    def test_missing_or_blank_question_is_422(self, client, student_headers, fakes, body):
        assert client.post(URL, json=body, headers=student_headers).status_code == 422

    def test_unknown_current_session_is_404_and_never_resolves(self, client, student_headers, fakes):
        response = client.post(URL, json={"question": "hi", "current_session_id": 999}, headers=student_headers)
        assert response.status_code == 404
        assert fakes["resolve_calls"] == []


# ===========================================================================
# Clarification path
# ===========================================================================

class TestClarificationPath:

    def test_single_clarification_event_and_no_thread_created(self, client, student_headers, fakes, db):
        fakes["resolution"] = ResolutionResult(
            status="clarification_needed",
            candidates=[
                {"session_id": 7, "session_title": "Week 4 Day 1", "best_similarity": 0.53},
                {"session_id": 5, "session_title": "Week 3 Day 1", "best_similarity": 0.50},
            ],
        )
        response = client.post(URL, json={"question": "What does bind_tools do?"}, headers=student_headers)

        assert response.status_code == 200
        events = _events(response)
        assert [e["event"] for e in events] == ["clarification_needed"]
        assert events[0]["message"] == (
            "I found a few sessions that could match — did you mean one of these? Week 4 Day 1, Week 3 Day 1"
        )
        assert [c["session_id"] for c in events[0]["candidates"]] == [7, 5]
        assert db.query(ConversationThread).count() == 0
        assert db.query(ConversationMessage).count() == 0
        assert fakes["prompts"] == [] and fakes["retrieve_calls"] == []

    @pytest.mark.parametrize("question,similarity", [
        # Both real Step 6 phrasings at their real similarity scores — the
        # second one used to resolve (0.5664 >= 0.55) before the vague-reference guard.
        ("What should I do for the TODO in this assignment?", 0.5376),
        ("what do I do for the TODO in this assignment", 0.5664),
    ])
    def test_real_resolver_ambiguity_also_creates_no_thread(
        self, client, student_headers, db, monkeypatch, question, similarity,
    ):
        """End-to-end through the REAL resolver: only its retrieval is faked."""
        monkeypatch.setattr(student_chat_service, "resolve_session", chat_session_resolver.resolve_session)
        monkeypatch.setattr(
            chat_session_resolver, "retrieve",
            lambda query, session_id=None, top_k=5, min_similarity=0.35: (
                [{"session_id": 7, "similarity": similarity}] * 4 if session_id is None else []
            ),
        )

        def must_not_generate(*args, **kwargs):
            raise AssertionError("no generation on the clarification path")

        monkeypatch.setattr(student_chat_service, "call_llm_stream", must_not_generate)

        response = client.post(URL, json={"question": question}, headers=student_headers)
        events = _events(response)
        assert [e["event"] for e in events] == ["clarification_needed"]
        assert events[0]["message"] == (
            "I'm not sure which session that question is about — did you mean Week 4 Day 1?"
        )
        assert db.query(ConversationThread).count() == 0


# ===========================================================================
# Resolved path
# ===========================================================================

class TestResolvedPath:

    def test_event_order_and_payloads(self, client, student_headers, fakes, db):
        response = client.post(
            URL, json={"question": "  What does cell 17 do?  ", "current_session_id": 7}, headers=student_headers,
        )
        assert response.status_code == 200
        events = _events(response)
        assert [e["event"] for e in events] == ["resolved", "citations", "token", "token", "token", "done"]
        assert events[0] == {
            "event": "resolved", "session_id": 7, "session_title": "Week 4 Day 1", "resolution": "current_session",
        }
        assert [e["text"] for e in events if e["event"] == "token"] == fakes["tokens"]
        assert fakes["resolve_calls"] == [(STUDENT_ID, "What does cell 17 do?", 7)]
        # Answer retrieval is scoped to the RESOLVED session.
        assert fakes["retrieve_calls"] == [("What does cell 17 do?", 7, 5, 0.35)]
        assert fakes["prompts"][0][1] == "student_chat"

    def test_citations_carry_filenames_and_locations(self, client, student_headers, fakes):
        events = _events(client.post(URL, json={"question": "q"}, headers=student_headers))
        citations = next(e for e in events if e["event"] == "citations")["citations"]
        assert citations[0] == {
            "source_type": "notebook", "source_file_id": 20, "session_id": 7, "similarity": 0.85,
            "snippet": 'calculator.invoke("25*18")', "filename": "Week 10_Lab3.ipynb",
            "cell_index": 17, "cell_type": "code",
        }
        assert citations[1]["filename"] == "week 10_day1.pptx"
        assert (citations[1]["slide_number"], citations[1]["source"]) == (23, "slide_text")

    def test_question_and_full_answer_persisted_after_stream(self, client, student_headers, fakes, db):
        events = _events(client.post(URL, json={"question": " What does cell 17 do? "}, headers=student_headers))
        done = events[-1]

        thread = db.query(ConversationThread).one()
        assert (thread.student_id, thread.lms_session_id) == (STUDENT_ID, 7)
        messages = db.query(ConversationMessage).order_by(ConversationMessage.id).all()
        assert [(m.role, m.content) for m in messages] == [
            (MessageRole.user, "What does cell 17 do?"),
            (MessageRole.assistant, "It calls the calculator tool."),
        ]
        assert done == {
            "event": "done", "thread_id": thread.id,
            "user_message_id": messages[0].id, "assistant_message_id": messages[1].id,
        }

    def test_prompt_has_rule_and_question_once_and_memory_on_second_turn(self, client, student_headers, fakes, db):
        client.post(URL, json={"question": "What does cell 17 do?"}, headers=student_headers)
        first_prompt = fakes["prompts"][0][0]
        assert SCOPE_SAFETY_RULE in first_prompt
        assert first_prompt.count("What does cell 17 do?") == 1  # not duplicated into history
        assert "(This is the start of the conversation.)" in first_prompt

        fakes["tokens"] = ["Deeper answer."]
        events = _events(client.post(URL, json={"question": "Can you go deeper on that?"}, headers=student_headers))
        second_prompt = fakes["prompts"][1][0]
        assert "Student: What does cell 17 do?" in second_prompt
        assert "Assistant: It calls the calculator tool." in second_prompt
        assert second_prompt.count("Can you go deeper on that?") == 1
        assert db.query(ConversationThread).count() == 1
        assert events[-1]["thread_id"] == db.query(ConversationThread).one().id
        assert db.query(ConversationMessage).count() == 4

    def test_redirected_resolution_uses_the_resolved_sessions_thread(self, client, student_headers, fakes, db):
        fakes["resolution"] = ResolutionResult(
            status="resolved", session_id=5, session_title="Week 3 Day 1", resolution="redirected",
        )
        events = _events(client.post(URL, json={"question": "q", "current_session_id": 7}, headers=student_headers))
        assert events[0]["resolution"] == "redirected"
        assert fakes["retrieve_calls"][0][1] == 5
        assert db.query(ConversationThread).one().lms_session_id == 5


# ===========================================================================
# Generation failures
# ===========================================================================

class TestGenerationFailure:

    def test_failure_before_any_text_sends_clean_error_and_saves_nothing(self, client, student_headers, fakes, db):
        raw = "Both Gemini models failed for purpose=student_chat: quota_metric=org_id=12345"
        fakes["tokens"] = [LLMProviderError(raw)]
        events = _events(client.post(URL, json={"question": "q"}, headers=student_headers))

        assert [e["event"] for e in events] == ["resolved", "citations", "error"]
        assert events[-1]["message"] == student_chat_service.STUDENT_CHAT_UNAVAILABLE_MESSAGE
        assert "quota_metric" not in json.dumps(events) and "Both Gemini" not in json.dumps(events)
        assert db.query(ConversationMessage).count() == 0

    def test_mid_stream_failure_sends_error_after_partial_tokens_and_saves_nothing(
        self, client, student_headers, fakes, db,
    ):
        fakes["tokens"] = ["partial ", RuntimeError("connection reset")]
        events = _events(client.post(URL, json={"question": "q"}, headers=student_headers))

        assert [e["event"] for e in events] == ["resolved", "citations", "token", "error"]
        assert db.query(ConversationMessage).count() == 0


# ===========================================================================
# Fix 1 — greeting / conversational bypass
# ===========================================================================

class TestConversationalBypass:
    """Greetings and small talk skip session resolution entirely (bypass fires
    inside resolve_session before any Chroma call, so we test via the real
    resolver rather than the monkeypatched fake in `fakes`)."""

    def test_greeting_returns_friendly_reply_with_no_retrieve_call(
        self, client, student_headers, monkeypatch, db
    ):
        """'hi' must produce token+done with no retrieve call and no thread."""
        retrieve_calls = []

        def fake_retrieve(query, session_id=None, top_k=5, min_similarity=0.35):
            retrieve_calls.append(query)
            return []

        monkeypatch.setattr(student_chat_service, "retrieve", fake_retrieve)
        # Let the REAL resolve_session run (don't replace it with fakes fixture).
        events = _events(
            client.post(URL, json={"question": "hi"}, headers=student_headers)
        )
        assert [e["event"] for e in events] == ["token", "done"]
        assert "ask" in events[0]["text"].lower() or "hi" in events[0]["text"].lower()
        assert retrieve_calls == []
        assert db.query(ConversationThread).count() == 0

    def test_greeting_produces_no_citations(self, client, student_headers, monkeypatch, db):
        monkeypatch.setattr(student_chat_service, "retrieve", lambda *a, **kw: [])
        events = _events(
            client.post(URL, json={"question": "thanks"}, headers=student_headers)
        )
        assert not any(e["event"] == "citations" for e in events)

    @pytest.mark.parametrize("greeting", ["hi", "hello", "hey", "thanks", "ok", "bye"])
    def test_common_greetings_all_bypass(self, client, student_headers, monkeypatch, greeting):
        retrieve_calls = []
        monkeypatch.setattr(student_chat_service, "retrieve", lambda *a, **kw: retrieve_calls.append(a) or [])
        events = _events(
            client.post(URL, json={"question": greeting}, headers=student_headers)
        )
        assert [e["event"] for e in events] == ["token", "done"]
        assert retrieve_calls == []


# ===========================================================================
# Fix 2 — broad multi-session topic path
# ===========================================================================

class TestBroadTopicPath:
    """General topic questions resolve with session_id=None and get a cross-session answer."""

    def test_broad_topic_resolution_triggers_cross_session_retrieval(
        self, client, student_headers, fakes, db
    ):
        # Simulate resolver returning broad_search with session_id=None (Fix 2).
        fakes["resolution"] = ResolutionResult(
            status="resolved", session_id=None, session_title=None, resolution="broad_search",
        )
        events = _events(
            client.post(URL, json={"question": "what is evaluation of AI?"}, headers=student_headers)
        )
        assert [e["event"] for e in events] == ["resolved", "citations", "token", "token", "token", "done"]
        assert events[0]["session_id"] is None
        # retrieve() was called with session_id=None — cross-session.
        assert fakes["retrieve_calls"][0][1] is None

    def test_broad_topic_with_no_material_suppresses_citations(
        self, client, student_headers, fakes, db
    ):
        fakes["resolution"] = ResolutionResult(
            status="resolved", session_id=None, session_title=None, resolution="broad_search",
        )
        fakes["retrieved"] = []  # Nothing above threshold.
        events = _events(
            client.post(URL, json={"question": "what is evaluation of AI?"}, headers=student_headers)
        )
        # No citations event when nothing was retrieved.
        assert not any(e["event"] == "citations" for e in events)
        assert any(e["event"] == "token" for e in events)

    def test_broad_topic_no_thread_created(self, client, student_headers, fakes, db):
        """session_id=None → no ConversationThread (can't key memory without a session)."""
        fakes["resolution"] = ResolutionResult(
            status="resolved", session_id=None, session_title=None, resolution="broad_search",
        )
        _events(client.post(URL, json={"question": "what is evaluation of AI?"}, headers=student_headers))
        assert db.query(ConversationThread).count() == 0


# ===========================================================================
# Fix 3 — citation suppression on no-material path
# ===========================================================================

class TestCitationSuppression:
    """Citations are only emitted when real material was retrieved."""

    def test_no_citations_when_retrieved_is_empty(self, client, student_headers, fakes, db):
        fakes["retrieved"] = []
        events = _events(client.post(URL, json={"question": "q"}, headers=student_headers))
        assert not any(e["event"] == "citations" for e in events)
        # Short-circuit canned message is sent instead (no prior history).
        token_texts = [e["text"] for e in events if e["event"] == "token"]
        assert len(token_texts) == 1
        assert "couldn't find" in token_texts[0]

    def test_follow_up_with_history_bypasses_short_circuit(self, client, student_headers, fakes, db):
        """When history exists, empty retrieval must NOT trigger the canned message —
        the LLM should answer using conversation context ("tell me more" pattern)."""
        # First turn: retrieved = real chunks → LLM answers, thread + messages persisted.
        first = _events(client.post(URL, json={"question": "tell me about ragas"}, headers=student_headers))
        assert any(e["event"] == "token" for e in first)
        assert "couldn't find" not in " ".join(e.get("text", "") for e in first)

        # Second turn: retrieval returns nothing for the vague follow-up.
        fakes["retrieved"] = []
        second = _events(client.post(URL, json={"question": "tell me more"}, headers=student_headers))

        # Must NOT hit the short-circuit — the LLM was called (fake_stream ran).
        assert len(fakes["prompts"]) == 2, "LLM must be called on the follow-up turn"
        # The canned "couldn't find" message must NOT appear.
        all_token_text = " ".join(e.get("text", "") for e in second if e["event"] == "token")
        assert "couldn't find" not in all_token_text
        # Conversation history from turn 1 is present in the second prompt.
        second_prompt = fakes["prompts"][1][0]
        assert "tell me about ragas" in second_prompt

    def test_citations_present_when_material_retrieved(self, client, student_headers, fakes, db):
        # Default fakes has two chunks — citations must still appear.
        events = _events(client.post(URL, json={"question": "q"}, headers=student_headers))
        assert any(e["event"] == "citations" for e in events)
