"""
Phase 7, Sub-feature 7.6: student practice quizzes.

POST /quiz/generate            → JSON body; scope assignment_file | session | multiple_sessions | topic
POST /quiz/generate/upload     → multipart; a one-off .pptx/.ipynb, never persisted or embedded
POST /quiz/{attempt_id}/submit → score once (409 on a second submit; never re-scored)
GET  /quiz/history             → the student's own SUBMITTED attempts, most recent first
GET  /quiz/{attempt_id}        → one of the student's own attempts (answers only once submitted)

All student-only (require_student), and every attempt is owner-only (403).
Nothing here ever reads or writes the Grade table: the score is a practice
score and is labeled as not affecting real grades wherever it is returned.

The endpoints are sync `def` on purpose: generation makes a blocking LLM call,
which FastAPI then runs in its threadpool instead of on the event loop.

Uploads: Starlette itself spools an UploadFile larger than 1 MB to a temporary
file it owns and deletes. That is outside this project's control and is not a
violation of "never written to disk" — the guarantee is about our own code,
which reads the bytes into memory and never saves, embeds, or stores them.
"""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.quiz_attempt import QuizAttempt
from app.models.user import User
from app.schemas.quiz import (
    QuizAttemptOut,
    QuizGenerateRequest,
    QuizHistoryItem,
    QuizHistoryOut,
    QuizQuestionOut,
    QuizQuestionResultOut,
    QuizResultOut,
    QuizSubmitRequest,
    practice_score_label,
)
from app.services import quiz_generator
from app.services.auth import require_student

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quiz", tags=["quiz"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _attempt_out(attempt: QuizAttempt) -> QuizAttemptOut:
    questions = json.loads(attempt.questions_json)
    return QuizAttemptOut(
        id=attempt.id,
        scope_type=attempt.scope_type,
        scope_detail=json.loads(attempt.scope_detail),
        # Built field by field so correct_option_index can never ride along.
        questions=[
            QuizQuestionOut(question=q["question"], options=q["options"], source_citation=q["source_citation"])
            for q in questions
        ],
        max_score=attempt.max_score,
        submitted=attempt.submitted_at is not None,
        created_at=attempt.created_at,
    )


def _result_out(attempt: QuizAttempt) -> QuizResultOut:
    questions = json.loads(attempt.questions_json)
    answers = json.loads(attempt.student_answers_json)
    return QuizResultOut(
        attempt_id=attempt.id,
        scope_type=attempt.scope_type,
        scope_detail=json.loads(attempt.scope_detail),
        score=attempt.score,
        max_score=attempt.max_score,
        score_label=practice_score_label(attempt.score, attempt.max_score),
        questions=[
            QuizQuestionResultOut(
                question=q["question"],
                options=q["options"],
                source_citation=q["source_citation"],
                correct_option_index=q["correct_option_index"],
                student_answer_index=a,
                is_correct=q["correct_option_index"] == a,
            )
            for q, a in zip(questions, answers)
        ],
        created_at=attempt.created_at,
        submitted_at=attempt.submitted_at,
    )


def _get_own_attempt_or_error(attempt_id: int, student: User, db: Session) -> QuizAttempt:
    attempt = db.get(QuizAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Quiz attempt {attempt_id} not found.")
    if attempt.student_id != student.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only access your own quiz attempts.")
    return attempt


def _generate_or_http_error(db: Session, student: User, scope_type: str, scope_detail: dict, **kwargs) -> QuizAttempt:
    try:
        return quiz_generator.generate_quiz(db, student.id, scope_type, scope_detail, **kwargs)
    except quiz_generator.QuizScopeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except quiz_generator.QuizMaterialError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except quiz_generator.QuizGenerationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


# ── Generate ───────────────────────────────────────────────────────────────────

@router.post(
    "/generate",
    response_model=QuizAttemptOut,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a 5-question practice quiz from course material (student only)",
)
def generate_quiz(
    body: QuizGenerateRequest,
    db: Annotated[Session, Depends(get_db)],
    student: Annotated[User, Depends(require_student)],
) -> QuizAttemptOut:
    attempt = _generate_or_http_error(db, student, body.scope_type, body.scope_detail())
    return _attempt_out(attempt)


@router.post(
    "/generate/upload",
    response_model=QuizAttemptOut,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a practice quiz from a one-off uploaded .pptx/.ipynb, never stored (student only)",
)
def generate_quiz_from_upload(
    file: Annotated[UploadFile, File(description="A single .pptx or .ipynb file. Used for this quiz only.")],
    db: Annotated[Session, Depends(get_db)],
    student: Annotated[User, Depends(require_student)],
) -> QuizAttemptOut:
    filename = file.filename or "upload"
    file_type, type_error = quiz_generator.validate_quiz_upload_filename(filename)
    if type_error is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=type_error)

    attempt = _generate_or_http_error(
        db, student, "uploaded_file",
        {"original_filename": filename, "file_type": file_type},
        uploaded_bytes=file.file.read(),
    )
    return _attempt_out(attempt)


# ── Submit / read ──────────────────────────────────────────────────────────────

@router.post(
    "/{attempt_id}/submit",
    response_model=QuizResultOut,
    summary="Submit answers to a practice quiz — scored once, never affects real grades (student only)",
)
def submit_quiz(
    attempt_id: int,
    body: QuizSubmitRequest,
    db: Annotated[Session, Depends(get_db)],
    student: Annotated[User, Depends(require_student)],
) -> QuizResultOut:
    attempt = _get_own_attempt_or_error(attempt_id, student, db)
    already = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="This quiz attempt was already submitted and can't be re-scored.",
    )
    if attempt.submitted_at is not None:
        raise already
    try:
        quiz_generator.submit_quiz_attempt(db, attempt, body.answers)
    except quiz_generator.QuizAlreadySubmittedError as exc:
        raise already from exc
    return _result_out(attempt)


@router.get(
    "/history",
    response_model=QuizHistoryOut,
    summary="Your own submitted practice quizzes, most recent first (student only)",
)
def quiz_history(
    db: Annotated[Session, Depends(get_db)],
    student: Annotated[User, Depends(require_student)],
) -> QuizHistoryOut:
    attempts = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.student_id == student.id, QuizAttempt.submitted_at.isnot(None))
        .order_by(QuizAttempt.submitted_at.desc(), QuizAttempt.id.desc())
        .all()
    )
    return QuizHistoryOut(attempts=[
        QuizHistoryItem(
            attempt_id=a.id,
            scope_type=a.scope_type,
            scope_detail=json.loads(a.scope_detail),
            score=a.score,
            max_score=a.max_score,
            score_label=practice_score_label(a.score, a.max_score),
            created_at=a.created_at,
            submitted_at=a.submitted_at,
        )
        for a in attempts
    ])


@router.get(
    "/{attempt_id}",
    response_model=QuizResultOut | QuizAttemptOut,
    summary="One of your own practice quizzes — answers included only once submitted (student only)",
)
def get_quiz_attempt(
    attempt_id: int,
    db: Annotated[Session, Depends(get_db)],
    student: Annotated[User, Depends(require_student)],
) -> QuizResultOut | QuizAttemptOut:
    attempt = _get_own_attempt_or_error(attempt_id, student, db)
    if attempt.submitted_at is None:
        return _attempt_out(attempt)
    return _result_out(attempt)
