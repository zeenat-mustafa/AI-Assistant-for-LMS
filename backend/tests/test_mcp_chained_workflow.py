"""
Tests for the chained MCP workflow -- Phase 4, Sub-feature 4.6.

4.1-4.5 each verified one tool in isolation. This asserts they compose:
the session_id match_session returns is what actually drives
generate_rubric, evaluate_submission and grade_session, and each step
behaves the same in sequence as it does standalone.

The LLM boundary is mocked, so this spends zero Gemini quota.

Cases covered
-------------
1. The full chain runs, and step 1's session_id flows into steps 2-4.
2. evaluate_submission stays read-only mid-chain (does not mark the file
   graded), so grade_session still has work to do afterwards.
3. grade_session's student_id filter scopes the run to that student and
   skips files already graded.
"""

import json
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.mcp.tools.evaluation_tools import evaluate_submission
from app.mcp.tools.grading_tools import grade_session
from app.mcp.tools.rubric_tools import generate_rubric
from app.mcp.tools.session_tools import match_session
from app.models.grade import Grade
from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.submission_file import SubmissionFile
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User, UserRole

_RUBRIC_CRITERIA = [
    {"criterion": "Loads the dataset", "points_possible": 4.0},
    {"criterion": "Builds the model", "points_possible": 6.0},
]
_EVAL_CRITERIA = [
    {"criterion": "Loads the dataset", "points_possible": 4.0,
     "points_awarded": 3.5, "explanation": "Loaded."},
    {"criterion": "Builds the model", "points_possible": 6.0,
     "points_awarded": 5.0, "explanation": "Trains."},
]


@pytest.fixture()
def chain_db(monkeypatch):
    """
    Session 10 "Week 2 Day 1" owned by instructor 1, with a cached rubric,
    one UNGRADED file (400) and one ALREADY-GRADED file (401) for the same
    student — mirroring the real dev-DB shape used in the live 4.6 run.
    """
    import app.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    db.add_all([
        User(id=1, name="Prof", email="prof@x.com", hashed_password="h",
             role=UserRole.instructor),
        User(id=8, name="Nami", email="nami@x.com", hashed_password="h",
             role=UserRole.student),
        LMSSession(id=10, title="Week 2 Day 1", instructor_id=1),
        UnsolvedFile(
            id=100, session_id=10, original_filename="Numpy_and_Plotting.ipynb",
            file_path="10/assignments/np.ipynb",
            parsed_requirements_text="Do the thing.",
            rubric_json=json.dumps({"criteria": _RUBRIC_CRITERIA}),
        ),
        Submission(id=200, session_id=10, student_id=8,
                   original_filename="sub.zip",
                   uploaded_file_path="10/submissions/8/sub.zip"),
        SubmissionFile(id=400, submission_id=200, matched_unsolved_file_id=100,
                       original_filename="Numpy_and_Plotting-us.ipynb",
                       extracted_ipynb_path="10/submissions/8/np-us.ipynb",
                       graded=False),
        SubmissionFile(id=401, submission_id=200, matched_unsolved_file_id=100,
                       original_filename="Pandas_Hands_on-us.ipynb",
                       extracted_ipynb_path="10/submissions/8/pd-us.ipynb",
                       graded=True),
    ])
    db.commit()

    mock_nb = {"valid": True, "code_cells": [{"source": "x = 1", "outputs": []}]}
    mock_structure = {
        "valid": True,
        "cells": [{"type": "code", "content": "x = 1", "heuristic_hint": False}],
    }
    monkeypatch.setattr("app.services.evaluator.parse_notebook_file", lambda _p: mock_nb)
    monkeypatch.setattr(
        "app.services.evaluator.extract_notebook_structure", lambda _p: mock_structure
    )
    monkeypatch.setattr(
        "app.services.evaluator.call_gemini_for_evaluation",
        lambda _prompt: json.dumps({"criteria": _EVAL_CRITERIA}),
    )

    yield db

    db.close()
    Base.metadata.drop_all(engine)


def test_chained_workflow_session_id_flows_through_every_step(chain_db):
    db = chain_db
    patches = [
        patch(f"app.mcp.tools.{mod}.SessionLocal", return_value=db)
        for mod in ("session_tools", "rubric_tools", "evaluation_tools", "grading_tools")
    ]
    for p in patches:
        p.start()
    try:
        # --- 1. resolve the instruction ---------------------------------
        matched = match_session("grade week 2 day 1", 1)
        assert matched["status"] == "matched"
        session_id = matched["session_id"]
        assert session_id == 10
        assert matched["session_title"] == "Week 2 Day 1"

        # --- 2. rubric for an assignment IN that session -----------------
        unsolved = (
            db.query(UnsolvedFile)
            .filter(UnsolvedFile.session_id == session_id)
            .order_by(UnsolvedFile.id)
            .first()
        )
        assert unsolved.session_id == session_id, "step 2 must use step 1's session"
        rubric_result = generate_rubric(unsolved.id)
        assert rubric_result["success"] is True
        assert rubric_result["rubric"]["criteria"] == _RUBRIC_CRITERIA
        # Cached rubric, so no regeneration and no staleness warning.
        assert "warning" not in rubric_result

        # --- 3. evaluate an ungraded submission in that session ----------
        sub_file = (
            db.query(SubmissionFile)
            .join(Submission, SubmissionFile.submission_id == Submission.id)
            .filter(Submission.session_id == session_id,
                    SubmissionFile.graded == False)  # noqa: E712
            .order_by(SubmissionFile.id)
            .first()
        )
        assert sub_file.id == 400
        evaluation = evaluate_submission(sub_file.id)
        assert evaluation["success"] is True
        assert evaluation["total_score"] == 8.5
        assert evaluation["criteria"] == _EVAL_CRITERIA

        # Read-only mid-chain: still ungraded, no Grade row yet.
        # Re-query rather than refresh: each tool closes the session it was
        # given (in production it opens its own), which detaches instances
        # held across calls.
        assert db.get(SubmissionFile, 400).graded is False
        assert db.query(Grade).count() == 0

        # --- 4. grade that session for that student ---------------------
        batch = grade_session(session_id, student_id=8)
        assert [e["event"] for e in batch["events"]] == ["checking", "graded", "summary"]
        assert batch["summary"]["total"] == 1, (
            "only the ungraded file should be processed — 401 was already graded"
        )
        assert batch["summary"]["graded"] == 1
        assert batch["summary"]["failed"] == 0
        assert batch["events"][1]["filename"] == "Numpy_and_Plotting-us.ipynb"

        # The evaluated file is now graded, and its score matches what
        # step 3 reported for the same submission.
        assert db.get(SubmissionFile, 400).graded is True
        grade = db.query(Grade).filter(Grade.submission_file_id == 400).one()
        assert grade.score == evaluation["total_score"]
    finally:
        for p in patches:
            p.stop()
