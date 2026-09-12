"""
Real multi-turn chat memory verification — Phase 7, Sub-feature 7.4 (Step 5).

Two genuinely different real threads for the demo student, each run exactly
as 7.5's chatbot will run a turn: memory context built BEFORE persisting the
question → real retrieval → build_scope_safe_prompt → call_llm → persist the
real question and answer.

  Thread A: LMSSession 8 "demo session" (lecture file week 10_day1.pptx), 5 turns
  Thread B: LMSSession 7 "Week 4 Day 1" (Week 10_Lab3.ipynb et al.), 10 turns —
            grows past the summarization trigger (>12 unsummarized messages)

Storage is a scratch backup-API copy of the real dev DB upgraded to head —
never the real lms.db, so re-running never adds threads to real users.
Retrieval uses the real Chroma collection. REAL production Gemini only;
every call must be served by the primary tier. Excluded from default
collection by pytest.ini's norecursedirs. Run explicitly:

    cd backend
    $env:ADVERSARIAL_CALL_DELAY_S="8"   # keeps ~16 calls under the per-minute limit
    python -m pytest tests/adversarial/test_chat_memory_multiturn_real.py -v -s

Mechanical assertions: primary tier; summarization fires exactly when the
threshold is crossed; verbatim window never exceeds RECENT_N + SUMMARIZE_BUFFER;
the prompt shrinks right after summarization; every raw message is still
stored verbatim. Whether follow-up answers ("the first one you mentioned",
"what was the second snippet") genuinely used earlier turns, and whether the
stored summary is accurate, is judged by a human from
transcripts_report_7.4_multiturn.md.
"""

import logging
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models.conversation import ConversationMessage, ConversationThread, MessageRole
from app.models.user import User
from app.services.chat_memory import (
    RECENT_N,
    SUMMARIZE_BUFFER,
    add_message,
    get_context_for_prompt,
    get_or_create_thread,
)
from app.services.chat_safety import build_scope_safe_prompt
from app.services.embeddings import retrieve
from app.services.llm_provider import call_llm

DEMO_STUDENT_EMAIL = "student@demo.com"
BACKEND_DIR = Path(__file__).resolve().parents[2]
REAL_DB_PATH = BACKEND_DIR / "lms.db"
TRANSCRIPT_PATH = Path(__file__).parent / "transcripts_report_7.4_multiturn.md"

THREAD_A_SESSION_ID = 8
THREAD_A_QUESTIONS = [
    "What are the common failure modes in agent systems covered in this lecture?",
    "Can you go deeper on the first one you mentioned?",
    "How would that failure show up in practice — what would I actually observe?",
    "Is that related to the other failure mode you listed second? How are they different?",
    "Summarize for me in two sentences what we've covered so far.",
]

THREAD_B_SESSION_ID = 7
THREAD_B_QUESTIONS = [
    "What does this code do?\n\nresponse = llm.invoke(\"Say Hello!\")\nprint(response.content)",
    "Can you go deeper on that — what kind of object is response?",
    "Next, can you explain what's happening here and why it prints 450?\n\ncalculator.invoke(\"25*18\")",
    "How is calling the tool directly like that different from what you described for llm.invoke earlier?",
    "In general, what does the @tool decorator do?",
    "What is bind_tools used for, in general?",
    "What is a ReAct agent loop, conceptually?",
    "Going back to the very first thing I asked you about — remind me what that code did?",
    "And how does that first example connect to the ReAct loop you just explained?",
    "What was the second code snippet I asked about, and what number did it print?",
]

_report: list[str] = ["# Real Multi-Turn Chat Memory Verification (7.4) — Transcript Report\n"]


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(scope="module")
def scratch_engine(tmp_path_factory):
    if not REAL_DB_PATH.exists():
        pytest.skip("backend/lms.db not present in this environment")
    scratch = tmp_path_factory.mktemp("multiturn_real") / "scratch_lms.db"
    src = sqlite3.connect(str(REAL_DB_PATH))
    dst = sqlite3.connect(str(scratch))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()

    env = dict(os.environ, DATABASE_URL=f"sqlite:///{scratch.as_posix()}")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    engine = create_engine(f"sqlite:///{scratch.as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    yield engine
    engine.dispose()


@pytest.fixture()
def db(scratch_engine):
    session = sessionmaker(bind=scratch_engine)()
    yield session
    session.close()


def _fresh_thread(db, lms_session_id: int) -> ConversationThread:
    """Clear any thread the scratch copy inherited for this pair (the real DB
    may already hold one), then create a brand-new one."""
    student = db.query(User).filter_by(email=DEMO_STUDENT_EMAIL).one()
    for existing in db.query(ConversationThread).filter_by(
        student_id=student.id, lms_session_id=lms_session_id
    ):
        db.delete(existing)
    db.commit()
    return get_or_create_thread(db, student.id, lms_session_id)


@pytest.fixture(scope="module", autouse=True)
def _write_transcript_report():
    yield
    if len(_report) > 1:
        TRANSCRIPT_PATH.write_text("\n".join(_report), encoding="utf-8")
        print(f"\nTranscript report written to {TRANSCRIPT_PATH}")


# ===========================================================================
# Helpers
# ===========================================================================

def _run_thread(db, caplog, name: str, lms_session_id: int, questions: list[str]) -> list[dict]:
    caplog.set_level(logging.INFO, logger="app.services.llm_provider")
    delay = float(os.environ.get("ADVERSARIAL_CALL_DELAY_S", "0"))
    thread = _fresh_thread(db, lms_session_id)
    turns = []
    _report.append(f"## {name} (LMSSession {lms_session_id})\n")

    for i, question in enumerate(questions, 1):
        time.sleep(delay)
        start = len(caplog.records)
        context = get_context_for_prompt(db, thread)
        retrieved = retrieve(question, session_id=lms_session_id, top_k=5, min_similarity=0.35)
        prompt = build_scope_safe_prompt(retrieved, question, conversation_history=context)
        answer = call_llm(prompt, purpose="chat_memory_multiturn_test")

        add_message(db, thread, MessageRole.user, question)
        add_message(db, thread, MessageRole.assistant, answer)
        db.refresh(thread)

        tiers = [
            ("primary" if "gemini primary" in r.message else "fallback", r.message.split("purpose=")[-1])
            for r in caplog.records[start:]
            if "LLM call served by" in r.message
        ]
        turn = {
            "turn": i,
            "question": question,
            "answer": answer,
            "tiers": tiers,
            "summary_in_prompt": context["rolling_summary"],
            "verbatim": len(context["recent_messages"]),
            "prompt_chars": len(prompt),
            "summarized": any("chat_memory_summarization" in p for _, p in tiers),
        }
        turns.append(turn)
        print(f"[{name} turn {i}] tiers={tiers} verbatim={turn['verbatim']} "
              f"summary={'yes' if turn['summary_in_prompt'] else 'no'} prompt_chars={turn['prompt_chars']}",
              flush=True)

        _report.append(f"### Turn {i}\n")
        _report.append(f"**Tiers:** {tiers}  \n**Verbatim messages sent:** {turn['verbatim']}  \n"
                       f"**Summary in prompt:** {'yes' if turn['summary_in_prompt'] else 'no'}  \n"
                       f"**Prompt size:** {turn['prompt_chars']} chars (~{turn['prompt_chars'] // 4} tokens)\n")
        _report.append(f"**Question:**\n\n```\n{question}\n```\n")
        _report.append(f"**Answer:**\n\n```\n{answer}\n```\n")

    stored = [
        (m.role.value, m.content)
        for m in db.query(ConversationMessage).filter_by(thread_id=thread.id).order_by(ConversationMessage.id)
    ]
    expected = [pair for t in turns for pair in (("user", t["question"]), ("assistant", t["answer"]))]
    _report.append(f"**Final rolling_summary** (summarized_through_message_id="
                   f"{thread.summarized_through_message_id}):\n\n```\n{thread.rolling_summary}\n```\n")

    bad_tiers = [t["turn"] for t in turns if not t["tiers"] or any(tier != "primary" for tier, _ in t["tiers"])]
    assert not bad_tiers, f"{name}: turns {bad_tiers} not fully served by primary Gemini"
    assert stored == expected, f"{name}: stored raw messages differ from what was sent/received"
    assert all(t["verbatim"] <= RECENT_N + SUMMARIZE_BUFFER for t in turns)
    return turns


# ===========================================================================
# Tests
# ===========================================================================

def test_thread_a_lecture_followups_stay_below_threshold(db, caplog):
    turns = _run_thread(db, caplog, "Thread A — lecture follow-ups", THREAD_A_SESSION_ID, THREAD_A_QUESTIONS)

    # 5 turns = at most 8 prior messages: always verbatim, never summarized.
    assert [t["verbatim"] for t in turns] == [0, 2, 4, 6, 8]
    assert not any(t["summarized"] for t in turns)
    assert all(t["summary_in_prompt"] is None for t in turns)


def test_thread_b_notebook_grows_past_summarization_trigger(db, caplog):
    turns = _run_thread(db, caplog, "Thread B — notebook, past the trigger", THREAD_B_SESSION_ID, THREAD_B_QUESTIONS)

    # Turns 1-7 carry 0..12 verbatim messages. Turn 8 is the first with 14
    # prior messages (> RECENT_N + SUMMARIZE_BUFFER), so it summarizes and
    # drops back to RECENT_N verbatim; turns 9-10 grow again from there.
    assert [t["verbatim"] for t in turns] == [0, 2, 4, 6, 8, 10, 12, 8, 10, 12]
    assert [t["turn"] for t in turns if t["summarized"]] == [8]
    assert all(t["summary_in_prompt"] is None for t in turns[:7])
    assert all(t["summary_in_prompt"] for t in turns[7:])
    assert "```" not in turns[7]["summary_in_prompt"]

    # Bounded, not linear: the prompt shrinks right after summarization.
    assert turns[7]["prompt_chars"] < turns[6]["prompt_chars"]
