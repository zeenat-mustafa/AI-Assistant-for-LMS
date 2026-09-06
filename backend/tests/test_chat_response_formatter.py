"""
Tests for backend/app/services/chat_response_formatter.py -- Phase 3,
Sub-feature 3.5.

This file pins the actual wording of each conversational message. The
endpoint tests in test_chat.py assert only that /chat and /chat/stream
attach the formatter's output for the right context, so wording changes
land here and don't ripple through the wiring tests.

Cases covered
-------------
1. no_session_match      -> non-empty, invites a retry
2. ambiguous_session     -> names the candidate session titles
3. student_not_found     -> quotes the attempted name
4. ambiguous_student     -> names the candidate students
5. unsupported_filter    -> includes the reason, suggests an alternative
6. graded, scope "all"   -> "Graded X of Y submissions in <title>."
7. graded, scope "student" -> also names the student
Plus: failure note, singular/plural, nothing-to-grade, empty candidate
lists, and an unknown status degrading gracefully.
"""

from app.services.chat_response_formatter import build_response_message


# ---------------------------------------------------------------------------
# 1-5. Early-exit statuses
# ---------------------------------------------------------------------------

def test_no_session_match_message():
    msg = build_response_message("no_session_match")
    assert msg
    assert "couldn't find a session" in msg


def test_ambiguous_session_message_lists_candidate_titles():
    candidates = [
        {"session_id": 1, "session_title": "Week 1 Day 1", "confidence": 0.9},
        {"session_id": 2, "session_title": "Week 1 Day 2", "confidence": 0.85},
    ]
    msg = build_response_message("ambiguous_session", candidates=candidates)
    assert "Week 1 Day 1" in msg
    assert "Week 1 Day 2" in msg


def test_student_not_found_message_quotes_attempted_name():
    msg = build_response_message("student_not_found", attempted_name="Xavier")
    assert "Xavier" in msg


def test_ambiguous_student_message_lists_candidate_names():
    candidates = [
        {"student_id": 2, "student_name": "Ali Khan"},
        {"student_id": 3, "student_name": "Ali Raza"},
    ]
    msg = build_response_message("ambiguous_student", candidates=candidates)
    assert "Ali Khan" in msg
    assert "Ali Raza" in msg


def test_unsupported_filter_message_includes_reason():
    msg = build_response_message(
        "unsupported_filter", reason="exclusionary filters not yet supported"
    )
    assert "exclusionary filters not yet supported" in msg


# ---------------------------------------------------------------------------
# 6-7. graded, both scopes
# ---------------------------------------------------------------------------

def test_graded_all_scope_message():
    msg = build_response_message(
        "graded",
        session_title="Week 8 Day 3",
        scope="all",
        student_name=None,
        summary={"total": 11, "graded": 11, "failed": 0, "failures": []},
    )
    assert msg == "Graded 11 of 11 submissions in Week 8 Day 3."


def test_graded_student_scope_message_names_the_student():
    msg = build_response_message(
        "graded",
        session_title="Week 8 Day 3",
        scope="student",
        student_name="Alice",
        summary={"total": 2, "graded": 2, "failed": 0, "failures": []},
    )
    assert msg == "Graded 2 of 2 submissions for Alice in Week 8 Day 3."


# ---------------------------------------------------------------------------
# graded — edge cases
# ---------------------------------------------------------------------------

def test_graded_message_notes_failures_without_enumerating_them():
    msg = build_response_message(
        "graded",
        session_title="Week 8 Day 3",
        scope="all",
        student_name=None,
        summary={
            "total": 11,
            "graded": 10,
            "failed": 1,
            "failures": [{"student_id": 3, "filename": "bad.ipynb", "error": "boom"}],
        },
    )
    assert msg == "Graded 10 of 11 submissions in Week 8 Day 3. 1 failed — see the details below."
    # The message stays short: the failing filename/error live in the
    # structured `failures` data, not in the prose.
    assert "bad.ipynb" not in msg
    assert "boom" not in msg


def test_graded_message_uses_singular_for_one_submission():
    msg = build_response_message(
        "graded",
        session_title="Week 8 Day 3",
        scope="all",
        student_name=None,
        summary={"total": 1, "graded": 1, "failed": 0, "failures": []},
    )
    assert msg == "Graded 1 of 1 submission in Week 8 Day 3."


def test_graded_message_when_nothing_left_to_grade():
    """A very common real case: every file in the session is already graded,
    so the batch yields a zero-count summary. "Graded 0 of 0 submissions"
    reads like a failure, so this gets its own sentence."""
    msg = build_response_message(
        "graded",
        session_title="Week 8 Day 3",
        scope="all",
        student_name=None,
        summary={"total": 0, "graded": 0, "failed": 0, "failures": []},
    )
    assert "nothing new to grade" in msg
    assert "Week 8 Day 3" in msg


# ---------------------------------------------------------------------------
# Defensive cases
# ---------------------------------------------------------------------------

def test_empty_candidate_lists_still_produce_a_message():
    assert build_response_message("ambiguous_session", candidates=[])
    assert build_response_message("ambiguous_student", candidates=[])


def test_unknown_status_degrades_instead_of_raising():
    msg = build_response_message("something_new_we_added_later")
    assert isinstance(msg, str)
    assert msg
