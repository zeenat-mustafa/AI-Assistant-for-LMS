"""
Real multi-turn conversations through the REAL student chat endpoint — 7.5.

Driven exactly as the floating widget will drive it: the first question is
sent with NO page context (broad-search resolution), and every later turn
echoes back the session the previous turn resolved to as current_session_id.

  Thread A: lecture material (expected to resolve to session 8), 5 turns
  Thread B: notebook material (expected to resolve to session 7), 10 turns —
            grows past 7.4's summarization trigger (turn 8)

Mechanical assertions: primary tier on every call; the first turn resolves by
broad search and every later turn stays in that session (continuity); in
thread B summarization fires exactly once, at turn 8, and the prompt shrinks
right after it; stored raw messages equal what was sent/received. Whether
follow-ups genuinely used earlier turns is judged by a human from
transcripts_report_7.5_endpoint_multiturn.md. Run explicitly:

    cd backend
    $env:ADVERSARIAL_CALL_DELAY_S="8"
    python -m pytest tests/adversarial/test_student_chat_endpoint_multiturn.py -v -s
"""

from pathlib import Path

import pytest

REPORT = Path(__file__).parent / "transcripts_report_7.5_endpoint_multiturn.md"

THREAD_A = (8, [
    "What are the common failure modes in agent systems covered in this lecture?",
    "Can you go deeper on the first one you mentioned?",
    "How would that failure show up in practice — what would I actually observe?",
    "Is that related to the other failure mode you listed second? How are they different?",
    "Summarize for me in two sentences what we've covered so far.",
])

THREAD_B = (7, [
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
])

_entries: list[dict] = []


@pytest.fixture(scope="module", autouse=True)
def _report(write_turn_report):
    yield
    if _entries:
        write_turn_report(REPORT, "Real Multi-Turn Conversations via the Real Student Chat Endpoint (7.5)", _entries)


def _run_conversation(name, expected_session_id, questions, ask, reset_threads, thread_state):
    reset_threads()
    turns = []
    current = None
    for i, question in enumerate(questions, 1):
        turn = ask(question, current_session_id=current)
        turns.append(turn)
        _entries.append({"label": f"{name} — turn {i}", "turn": turn})

        assert turn["tiers"] and all(t == "primary" for t, _ in turn["tiers"]), f"turn {i}: {turn['tiers']}"
        assert turn["kinds"][0] == "resolved", f"turn {i}: expected resolution, got {turn['kinds']}"
        assert turn["kinds"][-1] == "done" and turn["answer"].strip(), f"turn {i}: {turn['kinds']}"
        current = turn["resolved"]["session_id"]  # echoed back, exactly like the widget

    assert turns[0]["resolved"]["resolution"] == "broad_search"
    assert turns[0]["resolved"]["session_id"] == expected_session_id
    assert all(t["resolved"]["session_id"] == expected_session_id for t in turns), \
        [t["resolved"] for t in turns]

    state = thread_state(expected_session_id)
    expected = [pair for t in turns for pair in (("user", t["question"]), ("assistant", t["answer"]))]
    assert state["messages"] == expected
    _entries.append({
        "label": f"{name} — final stored thread state",
        "note": f"summarized_through_message_id={state['summarized_through_message_id']}\n\n"
                f"```\n{state['rolling_summary']}\n```",
    })
    return turns, state


def test_thread_a_lecture_followups(ask, reset_threads, thread_state):
    turns, state = _run_conversation("Thread A", *THREAD_A, ask, reset_threads, thread_state)
    assert not any(("primary", "chat_memory_summarization") in t["tiers"] for t in turns)
    assert state["rolling_summary"] is None


def test_thread_b_notebook_past_summarization_trigger(ask, reset_threads, thread_state):
    turns, state = _run_conversation("Thread B", *THREAD_B, ask, reset_threads, thread_state)
    summarized_turns = [i for i, t in enumerate(turns, 1) if ("primary", "chat_memory_summarization") in t["tiers"]]
    assert summarized_turns == [8]
    assert state["rolling_summary"] and "```" not in state["rolling_summary"]
    assert all("Summary of earlier conversation:\n(none" in t["memory_block"] for t in turns[:7])
    assert all("Summary of earlier conversation:\n(none" not in t["memory_block"] for t in turns[7:])
    assert len(turns[7]["prompt"]) < len(turns[6]["prompt"])
    assert len(state["messages"]) == 20
