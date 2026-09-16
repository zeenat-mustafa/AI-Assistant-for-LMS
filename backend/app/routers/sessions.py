"""
Session CRUD — instructor only for create/rename/delete, all authenticated
users for read.

POST   /sessions                              → create a new session
GET    /sessions                              → list all sessions (paginated)
GET    /sessions/{session_id}                 → get one session with its assignment files
PATCH  /sessions/{session_id}                 → rename a session (instructor only)
DELETE /sessions/{session_id}                 → delete session + all stored files (instructor only)
POST   /sessions/{session_id}/grade           → grade all ungraded submissions in a session (instructor only)
"""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.models.user import User
from app.schemas.session import SessionCreate, SessionList, SessionRead, SessionUpdate
from app.schemas.assignment_upload import AssignmentUploadRead
from app.schemas.unsolved_file import UnsolvedFileRead
from app.schemas.resource_file import ResourceFileRead
from app.schemas.lecture_file import LectureFileRead
from app.services.auth import get_current_user, require_instructor
from app.services.storage import delete_session_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_read(session: LMSSession) -> SessionRead:
    return SessionRead(
        id=session.id,
        title=session.title,
        instructor_id=session.instructor_id,
        instructor_name=session.instructor.name if session.instructor else None,
        created_at=session.created_at,
        assignment_uploads=[
            AssignmentUploadRead.from_orm_model(u) for u in session.assignment_uploads
        ],
        unsolved_files=[
            UnsolvedFileRead.from_orm_model(f) for f in session.unsolved_files
        ],
        resource_files=[
            ResourceFileRead.from_orm_model(f) for f in session.resource_files
        ],
        lecture_files=[
            LectureFileRead.from_orm_model(f) for f in session.lecture_files
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


def _quiz_attempts_for_session(
    db: Session, session_id: int, unsolved_file_ids: list[int],
) -> list["QuizAttempt"]:  # noqa: F821 -- imported locally in delete_session
    """
    Quiz attempts referencing *session_id* -- directly ("session" scope), via
    one of the session's own unsolved files ("assignment_file" scope, whose
    ids must be gathered from the still-live UnsolvedFile rows before the
    session cascade-deletes them), or as one of several sessions
    ("multiple_sessions" scope). "topic" and "uploaded_file" scopes can never
    reference a session and are excluded before any JSON is even read.

    Filters at the query level (scope_type, plus SQLite's JSON1
    json_extract for the two scalar cases) rather than loading every quiz
    attempt in the database — QuizAttempt.scope_detail is a JSON Text
    column with no FK (by design: attempt history must survive a file/
    session deletion elsewhere), so scope_type is the only indexed-ish
    column available to narrow the query before inspecting scope_detail.
    "multiple_sessions" (a JSON array) still needs an in-Python membership
    check, but only over that scope_type's own rows, not the whole table.
    """
    from app.models.quiz_attempt import QuizAttempt
    from sqlalchemy import func, or_

    conditions = [func.json_extract(QuizAttempt.scope_detail, "$.session_id") == session_id]
    if unsolved_file_ids:
        conditions.append(
            func.json_extract(QuizAttempt.scope_detail, "$.unsolved_file_id").in_(unsolved_file_ids)
        )
    direct_matches = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.scope_type.in_(["session", "assignment_file"]))
        .filter(or_(*conditions))
        .all()
    )

    multi_session_candidates = (
        db.query(QuizAttempt).filter(QuizAttempt.scope_type == "multiple_sessions").all()
    )
    multi_session_matches = []
    for attempt in multi_session_candidates:
        try:
            scope_detail = json.loads(attempt.scope_detail)
        except (json.JSONDecodeError, TypeError):
            continue  # malformed JSON — skip, never crash the delete
        if session_id in (scope_detail.get("session_ids") or []):
            multi_session_matches.append(attempt)

    return direct_matches + multi_session_matches


def _check_title_available(
    db: Session, title: str, *, exclude_session_id: int | None = None
) -> None:
    """
    Raise 409 if *title* is already taken by a DIFFERENT session.

    Global, not per-instructor: instructor access is a shared workspace, so
    a duplicate title is a conflict regardless of who created the existing
    session -- titles are what /chat instructions resolve by, and two
    instructors sharing one title is exactly the ambiguity that can't be
    resolved. `exclude_session_id` lets a rename keep its own current title
    without tripping over itself.
    """
    query = db.query(LMSSession).filter(LMSSession.title == title)
    if exclude_session_id is not None:
        query = query.filter(LMSSession.id != exclude_session_id)
    existing = query.first()
    if existing:
        owner = f" by {existing.instructor.name}" if existing.instructor else ""
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A session titled '{title}' already exists (id={existing.id}"
                f"{owner})."
            ),
        )


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
    _check_title_available(db, body.title)
    lms_session = LMSSession(title=body.title, instructor_id=_instructor.id)
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


@router.patch(
    "/{session_id}",
    response_model=SessionRead,
    summary="Rename a session (instructor only)",
)
def rename_session(
    session_id: int,
    body: SessionUpdate,
    db: Annotated[Session, Depends(get_db)],
    _instructor: Annotated[User, Depends(require_instructor)],
) -> SessionRead:
    lms_session = _get_session_or_404(session_id, db)
    _check_title_available(db, body.title, exclude_session_id=session_id)
    lms_session.title = body.title
    db.commit()
    db.refresh(lms_session)
    return _session_read(lms_session)


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
    """
    Delete a session and fully clean up all related data:
      - DB rows (cascaded via SQLAlchemy relationships)
      - Quiz attempts (manual cleanup since session_id is in JSON, not a FK) --
        "session", "assignment_file" (via the session's own unsolved files),
        and "multiple_sessions" scopes are all covered; "topic" and
        "uploaded_file" scopes never reference a session by construction.
      - Disk files (storage/sessions/{session_id}/)
      - Chroma vector embeddings (queried by session_id metadata, not reconstructed from files)
    """
    from app.services.embeddings import get_chroma_collection

    session = _get_session_or_404(session_id, db)

    # Gathered before the session (and its cascade-deleted UnsolvedFile rows)
    # are removed below — an "assignment_file"-scoped quiz attempt is only
    # matchable while these ids still exist.
    unsolved_file_ids = [
        row.id for row in db.query(UnsolvedFile.id).filter(UnsolvedFile.session_id == session_id).all()
    ]

    # ── 1. Query Chroma for ALL chunks with this session_id ──────────────────
    # Don't reconstruct chunk IDs from files/DB rows — query Chroma directly
    # by metadata, which is more reliable (files may be corrupt/missing, DB
    # rows may have embedded=True but the vector could have been manually
    # deleted, etc.). Chroma's where filter will find everything that actually
    # exists in the collection for this session.
    chunk_ids_to_delete = []
    try:
        collection = get_chroma_collection()
        # Query with where filter, requesting a very large n_results to get everything
        results = collection.get(
            where={"session_id": session_id},
            include=["metadatas"],  # We only need the IDs, not documents/embeddings
        )
        chunk_ids_to_delete = results.get("ids", [])
        if chunk_ids_to_delete:
            logger.info(
                "Found %d Chroma chunks to delete for session %d",
                len(chunk_ids_to_delete),
                session_id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to query Chroma chunks for session %d: %s (will proceed with deletion anyway)",
            session_id,
            exc,
        )

    # ── 2. Delete quiz attempts scoped to this session ───────────────────────
    # Quiz attempts store session_id (or unsolved_file_id) inside JSON
    # scope_detail, not as a FK, so they don't cascade. Found and deleted
    # manually — see _quiz_attempts_for_session's docstring for exactly
    # which scopes are covered and how the query is narrowed.
    session_quiz_attempts = _quiz_attempts_for_session(db, session_id, unsolved_file_ids)
    for attempt in session_quiz_attempts:
        db.delete(attempt)

    # ── 3. Delete the session (cascades to all FK-linked rows) ───────────────
    db.delete(session)
    db.commit()

    # ── 4. Clean up disk files ────────────────────────────────────────────────
    delete_session_storage(session_id)

    # ── 5. Clean up Chroma vectors ────────────────────────────────────────────
    if chunk_ids_to_delete:
        try:
            from app.services.embeddings import delete_chunks
            delete_chunks(chunk_ids_to_delete)
            logger.info(
                "Deleted %d Chroma chunks for session %d",
                len(chunk_ids_to_delete),
                session_id,
            )
        except Exception as exc:  # noqa: BLE001
            # Chroma cleanup failure should not prevent session deletion from
            # completing — the DB rows and disk files are already gone.
            # Log the error but don't raise (matches embeddings.retrieve convention).
            logger.warning(
                "Failed to delete Chroma chunks for session %d: %s",
                session_id,
                exc,
            )


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
    instructor: Annotated[User, Depends(require_instructor)],
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

    events: list[dict] = list(
        grade_session_batch(db, session_id, graded_by_instructor_id=instructor.id)
    )

    # The summary is always the last event yielded by the generator.
    summary = events[-1] if events else {
        "event": "summary", "total": 0, "graded": 0, "failed": 0, "failures": [],
    }

    return {"events": events, "summary": summary}
