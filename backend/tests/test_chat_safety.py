"""
Fast/mocked unit tests for app.services.chat_safety — Phase 7, Sub-feature 7.3.

These test prompt ASSEMBLY only. Whether the assembled prompt actually holds
the line against real adversarial attempts is proven separately, against
real Gemini, in tests/adversarial/test_scope_safety.py — that suite, not
this one, is 7.3's primary verification evidence.

Run with:
    cd backend
    python -m pytest tests/test_chat_safety.py -v
"""

from app.services.chat_safety import SCOPE_SAFETY_RULE, build_scope_safe_prompt


def test_scope_safety_rule_text_present_verbatim():
    prompt = build_scope_safe_prompt(retrieved_chunks=[], student_question="anything")
    assert SCOPE_SAFETY_RULE in prompt


def test_assembles_rule_context_and_question_in_order():
    chunks = [{
        "source_type": "notebook", "source_file_id": 20, "session_id": 5,
        "cell_index": 6, "cell_type": "code", "similarity": 0.71,
        "chunk_text": "from ___ import ___\nllm = ___(model=\"gemini-2.5-flash\")",
    }]
    prompt = build_scope_safe_prompt(chunks, "What does this cell do?")

    rule_pos = prompt.index(SCOPE_SAFETY_RULE)
    context_pos = prompt.index("Retrieved Course Material")
    chunk_pos = prompt.index("from ___ import ___")
    question_pos = prompt.index("Student's Question")
    question_text_pos = prompt.index("What does this cell do?")

    assert rule_pos < context_pos < chunk_pos < question_pos < question_text_pos


def test_notebook_chunk_metadata_rendered():
    chunks = [{
        "source_type": "notebook", "source_file_id": 20, "session_id": 5,
        "cell_index": 19, "cell_type": "code", "similarity": 0.5,
        "chunk_text": "tools = [___]",
    }]
    prompt = build_scope_safe_prompt(chunks, "q")
    assert "notebook" in prompt
    assert "source_file_id=20" in prompt
    assert "cell 19 (code)" in prompt


def test_lecture_chunk_metadata_rendered():
    chunks = [{
        "source_type": "lecture", "source_file_id": 1, "session_id": 5,
        "slide_number": 23, "source": "slide_text", "similarity": 0.6,
        "chunk_text": "Common Failure Modes in Agent Systems",
    }]
    prompt = build_scope_safe_prompt(chunks, "q")
    assert "lecture" in prompt
    assert "slide 23, slide_text" in prompt


def test_empty_retrieved_chunks_does_not_crash_and_says_so():
    """Legitimate case: a student asks something with no relevant retrieved
    content at all — must not crash, must assemble a usable prompt."""
    prompt = build_scope_safe_prompt(retrieved_chunks=[], student_question="What is Python?")
    assert "No relevant material was retrieved" in prompt
    assert "What is Python?" in prompt


def test_conversation_history_parameter_accepted_but_unused():
    """Reserved for 7.4 — accepted now so the signature won't need to change
    again later, but has no effect on the assembled prompt yet."""
    prompt_without = build_scope_safe_prompt([], "q")
    prompt_with = build_scope_safe_prompt([], "q", conversation_history=[{"role": "user", "text": "hi"}])
    assert prompt_without == prompt_with
