"""
Session CRUD — instructor only for create/delete, all authenticated users for read.

POST   /sessions                              → create a new session
GET    /sessions                              → list all sessions (paginated)
GET    /sessions/{session_id}                 → get one session with its assignment files
DELETE /sessions/{session_id}                 → delete session + all stored files (instructor only)
POST   /sessions/{session_id}/grade           → grade all ungraded submissions in a session (instructor only)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User
from app.schemas.session import SessionCreate, SessionList, SessionRead
from app.schemas.unsolved_file import UnsolvedFileRead
from app.services.auth import get_current_user, require_instructor
from app.services.storage import delete_session_storage

router = APIRouter(prefix="/sessions", tags=["sessions"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_read(session: LMSSession) -> SessionRead:
    return SessionRead(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        unsolved_files=[
            UnsolvedFileRead.from_orm_model(f) for f in session.unsolved_files
        ],
    )


def _get_session_or_404(session_id: int, db: Session) -> LMSSession:
    session = db.get(LMSSession, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    return session


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=SessionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new grading session (instructor only)",
)
def create_session(
    body: SessionCreate,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> SessionRead:
    # Prevent duplicate titles — they're used for fuzzy matching.
    existing = db.query(LMSSession).filter(LMSSession.title == body.title).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A session titled '{body.title}' already exists (id={existing.id}).",
        )
    lms_session = LMSSession(title=body.title)
    db.add(lms_session)
    db.commit()
    db.refresh(lms_session)
    return _session_read(lms_session)


@router.get(
    "",
    response_model=SessionList,
    summary="List all sessions",
)
def list_sessions(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> SessionList:
    total = db.query(LMSSession).count()
    sessions = (
        db.query(LMSSession)
        .order_by(LMSSession.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return SessionList(total=total, items=[_session_read(s) for s in sessions])


@router.get(
    "/{session_id}",
    response_model=SessionRead,
    summary="Get one session with its assignment files",
)
def get_session(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> SessionRead:
    return _session_read(_get_session_or_404(session_id, db))


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session and all its stored files (instructor only)",
)
def delete_session(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> None:
    session = _get_session_or_404(session_id, db)
    db.delete(session)
    db.commit()
    delete_session_storage(session_id)


# ── Batch session grading ─────────────────────────────────────────────────────

@router.post(
    "/{session_id}/grade",
    summary="Grade all ungraded submissions in a session (instructor only)",
    response_model=None,
    status_code=status.HTTP_200_OK,
)
def grade_session(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> dict:
    """
    Eagerly drains the ``grade_session_batch`` generator and returns every
    event plus the final summary in a single JSON response.

    Processes every ungraded SubmissionFile in the session one at a time.
    Individual file failures never abort the batch — each is recorded in the
    ``failures`` list and reflected in the summary counts.

    Phase 3 will replace this with a streaming SSE response that consumes
    the same generator incrementally.

    Response shape::

        {
            "events": [
                {"event": "checking",  "student_id": 2, "filename": "hw1.ipynb"},
                {"event": "graded",    "student_id": 2, "filename": "hw1.ipynb", "score": 8.5},
                {"event": "failed",    "student_id": 3, "filename": "bad.ipynb",  "error": "..."},
                ...
                {"event": "summary",   "total": 3, "graded": 2, "failed": 1,
                 "failures": [{"student_id": 3, "filename": "bad.ipynb", "error": "..."}]}
            ],
            "summary": {"event": "summary", "total": 3, "graded": 2, "failed": 1, "failures": [...]}
        }
    """
    from app.services.grading_pipeline import grade_session_batch

    _get_session_or_404(session_id, db)

    events: list[dict] = list(grade_session_batch(db, session_id))

    # The summary is always the last event yielded by the generator.
    summary = events[-1] if events else {
        "event": "summary", "total": 0, "graded": 0, "failed": 0, "failures": [],
    }

    return {"events": events, "summary": summary}
