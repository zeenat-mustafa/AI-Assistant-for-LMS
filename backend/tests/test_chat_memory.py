"""
Fast/mocked unit tests for app.services.chat_memory — Phase 7, Sub-feature 7.4.

The real end-to-end summarization test against real Gemini lives in
tests/adversarial/test_chat_memory_real.py (excluded from default collection
by pytest.ini, like every other real-Gemini test).

Run with:
    cd backend
    python -m pytest tests/test_chat_memory.py -v
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.conversation import ConversationMessage, ConversationThread, MessageRole
from app.models.session import LMSSession
from app.models.user import User, UserRole
from app.services import chat_memory
from app.services.chat_memory import (
    RECENT_N,
    SUMMARIZE_BUFFER,
    SUMMARIZATION_PROMPT,
    add_message,
    get_context_for_prompt,
    get_or_create_thread,
    summarize_older_turns,
)
from app.services.llm_provider import LLMProviderError


@pytest.fixture()
def db():
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def student(db):
    user = User(name="Stu", email="stu@example.com", hashed_password="x", role=UserRole.student)
    db.add(user)
    db.commit()
    return user


@pytest.fixture()
def lms_sessions(db):
    a = LMSSession(title="Week 3 Day 1")
    b = LMSSession(title="Week 5 Day 2")
    db.add_all([a, b])
    db.commit()
    return a, b


@pytest.fixture()
def thread(db, student, lms_sessions):
    return get_or_create_thread(db, student.id, lms_sessions[0].id)


def _add_exchanges(db, thread, count):
    for i in range(count):
        add_message(db, thread, "user", f"question {i}")
        add_message(db, thread, "assistant", f"answer {i}")


class _FakeLLM:
    def __init__(self, reply="The student asked questions and the assistant explained concepts."):
        self.reply = reply
        self.prompts = []

    def __call__(self, prompt, purpose="fast"):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


# ===========================================================================
# get_or_create_thread
# ===========================================================================

def test_get_or_create_thread_is_idempotent(db, student, lms_sessions):
    first = get_or_create_thread(db, student.id, lms_sessions[0].id)
    second = get_or_create_thread(db, student.id, lms_sessions[0].id)
    assert first.id == second.id
    assert db.query(ConversationThread).count() == 1
    assert first.rolling_summary is None
    assert first.summarized_through_message_id is None


def test_different_lms_session_gets_separate_thread(db, student, lms_sessions):
    week3 = get_or_create_thread(db, student.id, lms_sessions[0].id)
    week5 = get_or_create_thread(db, student.id, lms_sessions[1].id)
    assert week3.id != week5.id
    add_message(db, week3, "user", "week 3 question")
    assert get_context_for_prompt(db, week5)["recent_messages"] == []


def test_unique_constraint_enforced_at_db_level(db, student, lms_sessions):
    get_or_create_thread(db, student.id, lms_sessions[0].id)
    db.add(ConversationThread(student_id=student.id, lms_session_id=lms_sessions[0].id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ===========================================================================
# add_message
# ===========================================================================

def test_add_message_appends_and_never_overwrites(db, thread):
    first = add_message(db, thread, MessageRole.user, "hello")
    second = add_message(db, thread, "assistant", "hi there")
    rows = db.query(ConversationMessage).order_by(ConversationMessage.id).all()
    assert [(r.id, r.role, r.content) for r in rows] == [
        (first.id, MessageRole.user, "hello"),
        (second.id, MessageRole.assistant, "hi there"),
    ]


def test_add_message_rejects_unknown_role(db, thread):
    with pytest.raises(ValueError):
        add_message(db, thread, "system", "nope")


# ===========================================================================
# summarize_older_turns
# ===========================================================================

def test_summarize_folds_into_existing_summary_and_sets_bookmark(db, thread, monkeypatch):
    _add_exchanges(db, thread, 2)
    thread.rolling_summary = "EARLIER SUMMARY TEXT"
    db.commit()
    messages = db.query(ConversationMessage).order_by(ConversationMessage.id).all()

    fake = _FakeLLM("Updated summary.")
    monkeypatch.setattr(chat_memory, "call_llm", fake)

    result = summarize_older_turns(db, thread, messages)

    assert result == "Updated summary."
    assert thread.rolling_summary == "Updated summary."
    assert thread.summarized_through_message_id == messages[-1].id
    sent = fake.prompts[0]
    assert "EARLIER SUMMARY TEXT" in sent
    assert "Student: question 0" in sent
    assert "Assistant: answer 1" in sent


def test_summarize_uses_none_yet_placeholder_when_no_summary(db, thread, monkeypatch):
    _add_exchanges(db, thread, 1)
    messages = db.query(ConversationMessage).all()
    fake = _FakeLLM()
    monkeypatch.setattr(chat_memory, "call_llm", fake)
    summarize_older_turns(db, thread, messages)
    assert "(none yet)" in fake.prompts[0]


def test_summarization_prompt_is_the_approved_text(db, thread, monkeypatch):
    _add_exchanges(db, thread, 1)
    messages = db.query(ConversationMessage).all()
    fake = _FakeLLM()
    monkeypatch.setattr(chat_memory, "call_llm", fake)
    summarize_older_turns(db, thread, messages)
    expected = SUMMARIZATION_PROMPT.format(
        existing_summary="(none yet)",
        new_messages="Student: question 0\nAssistant: answer 0",
    )
    assert fake.prompts[0] == expected


@pytest.mark.parametrize("error", [LLMProviderError("both failed"), ValueError("empty response")])
def test_summarize_failure_keeps_previous_summary_and_does_not_raise(db, thread, monkeypatch, error):
    _add_exchanges(db, thread, 1)
    thread.rolling_summary = "OLD"
    db.commit()
    messages = db.query(ConversationMessage).all()
    monkeypatch.setattr(chat_memory, "call_llm", _FakeLLM(error))

    assert summarize_older_turns(db, thread, messages) == "OLD"
    assert thread.rolling_summary == "OLD"
    assert thread.summarized_through_message_id is None


@pytest.mark.parametrize(
    "bad_output",
    ["", "   ", "Here:\n```python\ntools = [a, b]\n```", "word " * 700],
)
def test_summarize_rejects_empty_codeblock_or_oversized_output(db, thread, monkeypatch, bad_output):
    _add_exchanges(db, thread, 1)
    messages = db.query(ConversationMessage).all()
    monkeypatch.setattr(chat_memory, "call_llm", _FakeLLM(bad_output))

    assert summarize_older_turns(db, thread, messages) is None
    assert thread.rolling_summary is None
    assert thread.summarized_through_message_id is None


def test_summarize_never_touches_raw_messages(db, thread, monkeypatch):
    _add_exchanges(db, thread, 3)
    before = [(m.id, m.role, m.content) for m in db.query(ConversationMessage).order_by(ConversationMessage.id)]
    monkeypatch.setattr(chat_memory, "call_llm", _FakeLLM())
    summarize_older_turns(db, thread, db.query(ConversationMessage).all())
    after = [(m.id, m.role, m.content) for m in db.query(ConversationMessage).order_by(ConversationMessage.id)]
    assert before == after


# ===========================================================================
# get_context_for_prompt
# ===========================================================================

def test_context_shape_for_empty_thread(db, thread):
    assert get_context_for_prompt(db, thread) == {"rolling_summary": None, "recent_messages": []}


def test_no_summarization_at_or_below_threshold(db, thread, monkeypatch):
    fake = _FakeLLM()
    monkeypatch.setattr(chat_memory, "call_llm", fake)
    threshold_messages = RECENT_N + SUMMARIZE_BUFFER  # 12
    _add_exchanges(db, thread, threshold_messages // 2)

    context = get_context_for_prompt(db, thread)

    assert fake.prompts == []
    assert context["rolling_summary"] is None
    assert len(context["recent_messages"]) == threshold_messages
    assert context["recent_messages"][0] == {"role": "user", "content": "question 0"}


def test_summarization_fires_above_threshold_and_keeps_recent_n_verbatim(db, thread, monkeypatch):
    fake = _FakeLLM("Summary of the first three exchanges.")
    monkeypatch.setattr(chat_memory, "call_llm", fake)
    _add_exchanges(db, thread, 7)  # 14 messages > 12

    context = get_context_for_prompt(db, thread)

    assert len(fake.prompts) == 1
    # The 6 oldest messages (14 - RECENT_N) were sent for folding.
    assert "Student: question 2" in fake.prompts[0]
    assert "Student: question 3" not in fake.prompts[0]
    assert context["rolling_summary"] == "Summary of the first three exchanges."
    assert len(context["recent_messages"]) == RECENT_N
    assert context["recent_messages"][0] == {"role": "user", "content": "question 3"}
    assert context["recent_messages"][-1] == {"role": "assistant", "content": "answer 6"}
    # Raw rows are all still there.
    assert db.query(ConversationMessage).filter_by(thread_id=thread.id).count() == 14


def test_second_summarization_only_folds_new_messages(db, thread, monkeypatch):
    fake = _FakeLLM("S1")
    monkeypatch.setattr(chat_memory, "call_llm", fake)
    _add_exchanges(db, thread, 7)
    get_context_for_prompt(db, thread)

    # Next call without new messages must NOT re-summarize.
    get_context_for_prompt(db, thread)
    assert len(fake.prompts) == 1

    fake.reply = "S2"
    for i in range(7, 10):
        add_message(db, thread, "user", f"question {i}")
        add_message(db, thread, "assistant", f"answer {i}")
    context = get_context_for_prompt(db, thread)

    assert len(fake.prompts) == 2
    second_prompt = fake.prompts[1]
    assert "S1" in second_prompt  # existing summary compounded, not reset
    assert "Student: question 2" not in second_prompt  # already folded last time
    assert "Student: question 3" in second_prompt
    assert context["rolling_summary"] == "S2"
    assert len(context["recent_messages"]) == RECENT_N


def test_failed_summarization_keeps_prompt_bounded(db, thread, monkeypatch):
    monkeypatch.setattr(chat_memory, "call_llm", _FakeLLM(LLMProviderError("down")))
    _add_exchanges(db, thread, 10)  # 20 messages

    context = get_context_for_prompt(db, thread)

    assert context["rolling_summary"] is None
    assert len(context["recent_messages"]) == RECENT_N + SUMMARIZE_BUFFER
    assert context["recent_messages"][-1] == {"role": "assistant", "content": "answer 9"}
    assert thread.summarized_through_message_id is None


def test_custom_recent_n(db, thread, monkeypatch):
    monkeypatch.setattr(chat_memory, "call_llm", _FakeLLM("S"))
    _add_exchanges(db, thread, 4)  # 8 messages > 2 + 4
    context = get_context_for_prompt(db, thread, recent_n=2)
    assert len(context["recent_messages"]) == 2
    assert context["rolling_summary"] == "S"


# ===========================================================================
# Cascade (locked decision C)
# ===========================================================================

def test_deleting_lms_session_cascades_threads_and_messages(db, thread, lms_sessions):
    _add_exchanges(db, thread, 1)
    db.delete(lms_sessions[0])
    db.commit()
    assert db.query(ConversationThread).count() == 0
    assert db.query(ConversationMessage).count() == 0
