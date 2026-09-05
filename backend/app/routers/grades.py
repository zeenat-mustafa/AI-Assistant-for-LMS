"""
Grade read endpoints.

GET /sessions/{session_id}/grades              → full report for all students (instructor)
GET /sessions/{session_id}/grades/mine         → current student's own grades
GET /sessions/{session_id}/grades/{student_id} → one student's grades (instructor)
"""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.grade import Grade
from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.submission_file import SubmissionFile
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User
from app.schemas.grade import GradeRead, GradeSummary, SessionGradeReport
from app.services.auth import get_current_user, require_instructor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions/{session_id}/grades", tags=["grades"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_session_or_404(session_id: int, db: Session) -> LMSSession:
    s = db.get(LMSSession, session_id)
    if s is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    return s


def _build_grade_summary(
    student: User,
    submission: Submission | None,
) -> GradeSummary:
    """
    Build a GradeSummary for one student.
    combined_score = average of per-file scores (each out of 10), normalised to 0–10.
    If no graded files exist, combined_score is None.
    """
    per_file: list[GradeRead] = []

    if submission:
        for sf in submission.submission_files:
            if sf.grade:
                per_file.append(GradeRead.from_orm_model(sf.grade, sf))

    combined: float | None = None
    if per_file:
        combined = sum(g.score for g in per_file) / len(per_file)
        combined = round(combined, 2)

    return GradeSummary(
        student_id=student.id,
        student_name=student.name,
        per_file=per_file,
        combined_score=combined,
    )


def _load_submission_with_grades(
    session_id: int, student_id: int, db: Session
) -> Submission | None:
    return (
        db.query(Submission)
        .options(
            joinedload(Submission.submission_files).joinedload(SubmissionFile.grade)
        )
        .filter(
            Submission.session_id == session_id,
            Submission.student_id == student_id,
        )
        .first()
    )


# ── Full session report (instructor) ─────────────────────────────────────────

@router.get(
    "",
    response_model=SessionGradeReport,
    summary="Full grading report for all students in a session (instructor only)",
)
def session_grade_report(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> SessionGradeReport:
    lms_session = _get_session_or_404(session_id, db)

    # Find every student who has a submission for this session.
    submissions = (
        db.query(Submission)
        .options(
            joinedload(Submission.student),
            joinedload(Submission.submission_files).joinedload(SubmissionFile.grade),
        )
        .filter(Submission.session_id == session_id)
        .all()
    )

    summaries = [
        _build_grade_summary(sub.student, sub) for sub in submissions
    ]

    return SessionGradeReport(
        session_id=session_id,
        session_title=lms_session.title,
        students=summaries,
    )


# ── My grades (student) ───────────────────────────────────────────────────────

@router.get(
    "/mine",
    response_model=GradeSummary,
    summary="Get the current student's own grades for a session",
)
def my_grades(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> GradeSummary:
    _get_session_or_404(session_id, db)
    submission = _load_submission_with_grades(session_id, current_user.id, db)
    return _build_grade_summary(current_user, submission)


# ── One student's grades (instructor) ────────────────────────────────────────

@router.get(
    "/{student_id}",
    response_model=GradeSummary,
    summary="Get one student's grades for a session (instructor only)",
)
def student_grade_summary(
    session_id: int,
    student_id: int,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> GradeSummary:
    _get_session_or_404(session_id, db)
    student = db.get(User, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student {student_id} not found.",
        )
    submission = _load_submission_with_grades(session_id, student_id, db)
    return _build_grade_summary(student, submission)
