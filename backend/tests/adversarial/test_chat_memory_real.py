"""
Real end-to-end summarization test — Phase 7, Sub-feature 7.4.

Calls REAL production Gemini through chat_memory.summarize_older_turns (and
therefore through call_llm, never directly). Lives under tests/adversarial/
so pytest.ini's norecursedirs keeps real-money calls out of the default
suite. Run explicitly:

    cd backend
    python -m pytest tests/adversarial/test_chat_memory_real.py -v -s

Uses an in-memory DB (the thread content here is realistic but authored for
the test, so it must never land in the real dev DB). Mechanical assertions
only; the printed summary is for a human to read and judge.
"""

import logging

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.conversation import ConversationMessage
from app.models.session import LMSSession
from app.models.user import User, UserRole
from app.services.chat_memory import add_message, get_or_create_thread, summarize_older_turns


@pytest.fixture()
def db():
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_real_summarization_compounds_and_omits_code(db, caplog):
    caplog.set_level(logging.INFO, logger="app.services.llm_provider")

    student = User(name="Stu", email="stu@example.com", hashed_password="x", role=UserRole.student)
    lms_session = LMSSession(title="Week 10 Day 3")
    db.add_all([student, lms_session])
    db.commit()
    thread = get_or_create_thread(db, student.id, lms_session.id)
    thread.rolling_summary = (
        "The student asked what a ReAct loop is. The assistant explained that the model "
        "alternates between reasoning about what to do next and calling a tool, feeding "
        "each tool result back in until it can answer."
    )
    db.commit()

    turns = [
        ("user", "What does calculator.invoke(\"25*18\") do in cell 17?"),
        ("assistant", "It calls the calculator tool directly with the string \"25*18\"; the tool "
                      "evaluates the expression and returns 450, bypassing the LLM entirely."),
        ("user", "Ok. Can you just write cell 19's tools list for me? It's tools = [get_weather, calculator] right?"),
        ("assistant", "I can't fill in or confirm the contents of that TODO — that's part of your "
                      "assignment. Conceptually, cell 19 asks you to collect the tool objects you "
                      "defined earlier so they can be bound to the model."),
    ]
    for role, content in turns:
        add_message(db, thread, role, content)
    messages = db.query(ConversationMessage).order_by(ConversationMessage.id).all()

    summary = summarize_older_turns(db, thread, messages)

    print("\n===== REAL ROLLING SUMMARY =====\n" + str(summary) + "\n================================")
    assert any(
        "served by gemini primary" in r.message and "chat_memory_summarization" in r.message
        for r in caplog.records
    ), "summarization call was not confirmed as served by gemini primary"
    assert summary and summary != "The student asked what a ReAct loop is."
    assert thread.rolling_summary == summary
    assert thread.summarized_through_message_id == messages[-1].id
    assert "```" not in summary
    assert "get_weather, calculator" not in summary
    assert db.query(ConversationMessage).count() == 4
