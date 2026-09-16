"""
Post-7.8 Fix 3 (Prompt 3): lecture-file MCP tools.

Thin MCP wrappers over Phase 7.1's lecture file operations. No upload
or listing logic lives here — each tool opens a database session,
delegates to the existing router/service function, and returns its result
unaltered. Same register(server) pattern as Phase 4 tools.

Note on async: The real upload_lecture is async (FastAPI's UploadFile
necessitates it), but MCP tools are sync functions. We need to bridge this
with asyncio.run() to call the async function synchronously within the MCP
tool context.
"""

import asyncio
import io
import logging
from pathlib import Path

from mcp.server import MCPServer

from app.database import SessionLocal
from app.models.lecture_file import LectureFile
from app.models.session import LMSSession
from app.schemas.lecture_file import LectureFileRead

logger = logging.getLogger(__name__)


def list_lecture_files(session_id: int) -> list[dict]:
    """
    List all lecture files uploaded to a session.

    Returns a list of lecture file metadata dicts, each containing:
      id, session_id, instructor_id, original_filename, content_type,
      extracted (bool), extraction_error (if any), uploaded_at, chunk_count

    Wraps the same query logic the REST ``GET /lectures`` endpoint uses.
    An empty list when no lectures exist is normal, not an error.
    """
    db = SessionLocal()
    try:
        session = db.get(LMSSession, session_id)
        if session is None:
            return []  # Match Phase 4's never-raise pattern for missing resources

        # Query pattern matches app/routers/lectures.py list_lecture_files
        lectures = (
            db.query(LectureFile)
            .filter(LectureFile.session_id == session_id)
            .order_by(LectureFile.uploaded_at.desc())
            .all()
        )

        result = [
            {
                "id": lf.id,
                "session_id": lf.session_id,
                "instructor_id": lf.instructor_id,
                "original_filename": lf.original_filename,
                "content_type": lf.content_type,
                "extracted": lf.extracted,
                "extraction_error": lf.extraction_error,
                "uploaded_at": lf.uploaded_at.isoformat(),
                "chunk_count": len(lf.chunks),
            }
            for lf in lectures
        ]
    finally:
        db.close()

    logger.info("MCP list_lecture_files: session_id=%d -> %d file(s)", session_id, len(result))
    return result


def upload_lecture_file(
    session_id: int,
    filename: str,
    file_bytes: bytes,
    content_type: str = "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    instructor_id: int = 1,
) -> dict:
    """
    Upload a lecture file (.pptx) to a session.

    Takes raw bytes (not a file path) since MCP tools can't assume filesystem
    access. Wraps the same upload, extraction, chunking, and embedding logic
    the REST ``POST /lectures/upload`` endpoint uses, but runs it synchronously
    (not async) by bridging with asyncio.run().

    Returns the created LectureFile metadata on success, or raises if the
    session doesn't exist, filename conflicts, or .pptx validation fails.

    Note: instructor_id is kept for the same audit-trail reason as Phase 4's
    match_session — it's recorded in the LectureFile row but NOT used to gate
    access (instructor access is a shared workspace).
    """
    # Import here to avoid circular dependencies
    from app.services.lecture_extraction import extract_pptx_content, chunk_slides
    from app.services.storage import save_lecture_file, absolute_path
    from app.services.embeddings import upsert_chunk
    from app.models.lecture_chunk import LectureChunk

    db = SessionLocal()
    try:
        # Validate session exists
        session = db.get(LMSSession, session_id)
        if session is None:
            return {"error": f"Session {session_id} not found"}

        # Validate filename
        if not filename.lower().endswith(".pptx"):
            return {"error": "Only .pptx files are supported"}

        # Check for duplicate filename
        existing = (
            db.query(LectureFile)
            .filter(
                LectureFile.session_id == session_id,
                LectureFile.original_filename == filename
            )
            .first()
        )
        if existing is not None:
            return {"error": f"A lecture file named '{filename}' already exists in session {session_id}"}

        # Save file to disk (async function, need to run in event loop)
        async def save_async():
            return await save_lecture_file(session_id, filename, file_bytes)

        rel_path = asyncio.run(save_async())

        # Create LectureFile row
        lecture = LectureFile(
            session_id=session_id,
            instructor_id=instructor_id,
            original_filename=filename,
            content_type=content_type,
            file_path=rel_path,
            extracted=False,
        )
        db.add(lecture)
        db.flush()

        # Extract and chunk
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

            # Embed chunks
            db.flush()
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
                except Exception as exc:  # noqa: BLE001
                    chunk_row.embedded = False
                    chunk_row.embedding_error = str(exc)
                    logger.warning(
                        "Embedding failed for lecture chunk (slide %d, %s): %s",
                        c["slide_number"], c["source"], exc,
                    )
        else:
            lecture.extracted = False
            lecture.extraction_error = result["error"]

        db.commit()
        db.refresh(lecture)

        result_dict = {
            "id": lecture.id,
            "session_id": lecture.session_id,
            "instructor_id": lecture.instructor_id,
            "original_filename": lecture.original_filename,
            "content_type": lecture.content_type,
            "extracted": lecture.extracted,
            "extraction_error": lecture.extraction_error,
            "uploaded_at": lecture.uploaded_at.isoformat(),
            "chunk_count": len(lecture.chunks),
        }

    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error("MCP upload_lecture_file failed: %s", exc)
        return {"error": str(exc)}
    finally:
        db.close()

    logger.info(
        "MCP upload_lecture_file: session_id=%d filename=%r -> lecture_id=%d",
        session_id, filename, result_dict["id"]
    )
    return result_dict


def register(server: MCPServer) -> None:
    """Register this module's tools on *server*."""
    server.add_tool(
        list_lecture_files,
        name="list_lecture_files",
        description=(
            "List all lecture files (.pptx) uploaded to a session. Returns "
            "metadata for each file including extraction status, chunk count, "
            "and upload timestamp. Empty list when no lectures exist."
        ),
    )
    server.add_tool(
        upload_lecture_file,
        name="upload_lecture_file",
        description=(
            "Upload a lecture file (.pptx only) to a session. Extracts slide "
            "text and speaker notes, chunks them, and embeds into the vector "
            "store for the student chatbot to retrieve. Returns the created "
            "lecture file metadata on success. Filename must be unique within "
            "the session."
        ),
    )
