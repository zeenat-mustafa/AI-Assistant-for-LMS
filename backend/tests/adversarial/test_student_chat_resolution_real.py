"""
Session resolution against REAL retrieval — Phase 7, Sub-feature 7.5.

Runs the real resolve_session over the real Chroma collection with the local
sentence-transformers model. NO Gemini calls, no quota. The DB is opened
read-only (sqlite mode=ro), only to look up session titles.

Guards the resolution outcomes verified live in 7.5 Step 6 — most importantly
both phrasings of the borderline TODO question, which scored 0.5376 and
0.5664 against the real collection (either side of BROAD_MIN_SIMILARITY) and
must BOTH ask for clarification via the vague-reference guard. If content is
re-uploaded or re-embedded these expectations may legitimately need
revisiting — that is the point: a change must be seen, not slip through.

Lives in tests/adversarial/ (excluded from the default run) because it needs
the real embedded dev collection. Run explicitly:

    cd backend
    python -m pytest tests/adversarial/test_student_chat_resolution_real.py -v
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.chat_session_resolver import resolve_session

REAL_DB_PATH = Path(__file__).resolve().parents[2] / "lms.db"
STUDENT_ID = 3  # student2@demo.com — resolution does not depend on the student


@pytest.fixture(scope="module")
def readonly_db():
    if not REAL_DB_PATH.exists():
        pytest.skip("backend/lms.db not present in this environment")
    engine = create_engine(
        f"sqlite:///file:{REAL_DB_PATH.as_posix()}?mode=ro&uri=true",
        connect_args={"check_same_thread": False},
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.mark.parametrize("question", [
    "What should I do for the TODO in this assignment?",
    "what do I do for the TODO in this assignment",
])
def test_both_borderline_todo_phrasings_ask_for_clarification(readonly_db, question):
    result = resolve_session(readonly_db, STUDENT_ID, question)
    assert result.status == "clarification_needed", result
    assert result.session_id is None


@pytest.mark.parametrize("question", [
    "What should I do for the TODO in this assignment?",
    "what do I do for the TODO in this assignment",
])
def test_borderline_todo_with_page_context_stays_in_that_session(readonly_db, question):
    result = resolve_session(readonly_db, STUDENT_ID, question, current_session_id=7)
    assert (result.status, result.session_id, result.resolution) == ("resolved", 7, "current_session")


@pytest.mark.parametrize("question,expected_session,expected_resolution,current", [
    ("Can you explain what's happening here and why it prints 450?\n\ncalculator.invoke(\"25*18\")",
     7, "current_session", 7),
    ("How does YOLOv8 object detection work?", 1, "redirected", 7),
    ("What is GPU acceleration used for?", 5, "broad_search", None),
    ("What are the common failure modes in agent systems?", 8, "broad_search", None),
])
def test_step6_resolved_branches_unchanged(readonly_db, question, expected_session, expected_resolution, current):
    result = resolve_session(readonly_db, STUDENT_ID, question, current_session_id=current)
    assert (result.status, result.session_id, result.resolution) == (
        "resolved", expected_session, expected_resolution,
    ), result


@pytest.mark.parametrize("question", [
    "What does bind_tools do?",          # genuinely split across sessions 5/6/7
    "What is the capital of France?",    # no relevant material at all
])
def test_step6_clarification_branches_unchanged(readonly_db, question):
    assert resolve_session(readonly_db, STUDENT_ID, question).status == "clarification_needed"
