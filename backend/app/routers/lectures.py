"""
Lecture file upload (instructor) and list/download (any authenticated user) —
Phase 7, Sub-feature 7.1.

A lecture upload is always exactly one .pptx (never a zip bundling several
files), so — unlike assignments/submissions — there is no "raw upload event
vs. derived pieces" split needed on the file side. LectureFile IS the upload
event; only extracted TEXT (LectureChunk rows) is derived from it, and those
rows are internal (7.2's retrieval concern), never served here.

Visibility is locked to match ResourceFile's *intended* shape (supporting
material, downloadable by students, never graded) — but ResourceFile no
longer has standalone list/download endpoints in this codebase (folded into
AssignmentUpload by bugfix-original-upload-preservation). Since a lecture
upload has no derived-pieces split to fold into, it keeps its own top-level
list/download endpoints directly, using ResourceFile's AUTH pattern
(get_current_user, not require_instructor) for those two.

POST   /sessions/{session_id}/lectures                    → upload one .pptx (instructor only);
                                                               extracts + chunks synchronously
GET    /sessions/{session_id}/lectures                    → list lecture files for a session
GET    /sessions/{session_id}/lectures/{lecture_id}/download → download the original .pptx bytes

No delete endpoint — matches ResourceFile's deliberate "no delete" precedent
for supporting material; a known gap, not an oversight.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.session import LMSSession
from app.models.lecture_file import LectureFile
from app.models.lecture_chunk import LectureChunk
from app.models.user import User
from app.schemas.lecture_file import LectureFileRead
from app.services.auth import get_current_user, require_instructor
from app.services.lecture_extraction import (
    validate_lecture_filename,
    extract_pptx_content,
    chunk_slides,
)
from app.services.embeddings import upsert_chunk
from app.services.storage import absolute_path, save_lecture_file

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sessions/{session_id}/lectures",
    tags=["lectures"],
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_session_or_404(session_id: int, db: Session) -> LMSSession:
    session = db.get(LMSSession, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    return session


def _get_lecture_or_404(lecture_id: int, session_id: int, db: Session) -> LectureFile:
    lf = db.get(LectureFile, lecture_id)
    if lf is None or lf.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lecture file {lecture_id} not found in session {session_id}.",
        )
    return lf


def _existing_filename_conflict(session_id: int, filename: str, db: Session) -> bool:
    return (
        db.query(LectureFile)
        .filter(
            LectureFile.session_id == session_id,
            LectureFile.original_filename == filename,
        )
        .first()
        is not None
    )


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=LectureFileRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a lecture file (.pptx only) to a session (instructor only)",
)
async def upload_lecture(
    session_id: int,
    file: Annotated[UploadFile, File(description="A single .pptx lecture file.")],
    db: Annotated[Session, Depends(get_db)],
    instructor: Annotated[User, Depends(require_instructor)],
) -> LectureFileRead:
    _get_session_or_404(session_id, db)

    filename = file.filename or "upload"
    type_error = validate_lecture_filename(filename)
    if type_error is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=type_error,
        )

    if _existing_filename_conflict(session_id, filename, db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A lecture file named '{filename}' already exists in "
                f"session {session_id}. Use a different name."
            ),
        )

    data = await file.read()
    rel_path = await save_lecture_file(session_id, filename, data)

    lecture = LectureFile(
        session_id=session_id,
        instructor_id=instructor.id,
        original_filename=filename,
        content_type=file.content_type,
        file_path=rel_path,
        extracted=False,
    )
    db.add(lecture)
    db.flush()  # need lecture.id before creating chunks / on error paths

    # Extraction + chunking run synchronously in this request — no background
    # job infra exists in this project, and this sub-feature does not
    # introduce one. On failure, the raw file (already saved above) and this
    # LectureFile row are STILL persisted, marked failed with the real error —
    # never silently dropped, never fabricated.
    result = extract_pptx_content(absolute_path(rel_path))
    if result["valid"]:
        chunks = chunk_slides(result["slides"])
        chunk_rows: list[LectureChunk] = []
        for c in chunks:
            chunk_row = LectureChunk(
                lecture_file_id=lecture.id,
                slide_number=c["slide_number"],
                source=c["source"],
                chunk_index=c["chunk_index"],
                chunk_text=c["chunk_text"],
            )
            db.add(chunk_row)
            chunk_rows.append(chunk_row)
        lecture.extracted = True
        logger.info(
            "Lecture file extracted: %s → session %d (%d chunks)",
            filename, session_id, len(chunks),
        )

        # Embedding runs synchronously in this same request (no background
        # job infra exists in this project) — but a chunk row always exists
        # regardless of embedding outcome, and embedding failure never blocks
        # the upload's own success. db.flush() first so each chunk_row.id is
        # assigned before it's used to build the chunk's deterministic
        # Chroma id.
        db.flush()
        # Zipped with the original chunk dicts (not chunk_row.source) since a
        # freshly-constructed ORM attribute holds the plain string assigned
        # to it until the next DB round-trip — chunk_row.source.value would
        # raise AttributeError here, before any refresh has coerced it into
        # a real ChunkSource enum member.
        for c, chunk_row in zip(chunks, chunk_rows):
            try:
                upsert_chunk(
                    chunk_id=f"lecture:{chunk_row.id}",
                    text=chunk_row.chunk_text,
                    metadata={
                        "source_type": "lecture",
                        "source_file_id": lecture.id,
                        "session_id": session_id,
                        "slide_number": c["slide_number"],
                        "source": c["source"],
                    },
                )
                chunk_row.embedded = True
                chunk_row.embedding_error = None
            except Exception as exc:  # noqa: BLE001 — never let embedding block upload
                chunk_row.embedded = False
                chunk_row.embedding_error = str(exc)
                logger.warning(
                    "Embedding failed for lecture chunk (slide %d, %s) in "
                    "session %d: %s",
                    c["slide_number"], c["source"], session_id, exc,
                )
    else:
        lecture.extracted = False
        lecture.extraction_error = result["error"]
        logger.warning(
            "Lecture file extraction failed: %s → session %d: %s",
            filename, session_id, result["error"],
        )

    db.commit()
    db.refresh(lecture)
    return LectureFileRead.from_orm_model(lecture)


# ── List ──────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[LectureFileRead],
    summary="List lecture files for a session (any authenticated user)",
)
def list_lectures(
    session_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[LectureFileRead]:
    _get_session_or_404(session_id, db)
    lectures = (
        db.query(LectureFile)
        .filter(LectureFile.session_id == session_id)
        .order_by(LectureFile.uploaded_at)
        .all()
    )
    return [LectureFileRead.from_orm_model(lf) for lf in lectures]


# ── Download ──────────────────────────────────────────────────────────────────

@router.get(
    "/{lecture_id}/download",
    summary="Download a lecture file exactly as it was uploaded (any authenticated user)",
    response_class=FileResponse,
)
def download_lecture(
    session_id: int,
    lecture_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    lf = _get_lecture_or_404(lecture_id, session_id, db)
    abs_path = absolute_path(lf.file_path)
    if not abs_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File is recorded in the database but not found on disk.",
        )
    return FileResponse(
        path=str(abs_path),
        filename=lf.original_filename,
        media_type=lf.content_type or "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
