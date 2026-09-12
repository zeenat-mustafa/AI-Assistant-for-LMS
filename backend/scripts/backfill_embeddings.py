"""
One-time backfill: embed every existing LectureChunk and every existing
UnsolvedFile's instructional cells not yet marked embedded=True — Phase 7.2.

Migration gap (per the project's own standing rule): LectureFile/LectureChunk
rows (7.1) and UnsolvedFile rows (Phases 1-2) already exist in the dev DB
from before Chroma/sentence-transformers existed, so nothing about them has
ever been embedded. This is that gap being closed, once.

Backs up the SQLite DB file via sqlite3's own backup API before writing
anything, mirroring this project's existing "lms.db.pre-<description>-
backup-<timestamp>" convention (see e.g. 1e98ad501108's own migration).

Run with:
    cd backend
    python scripts/backfill_embeddings.py
"""

import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.lecture_chunk import LectureChunk  # noqa: E402
from app.models.unsolved_file import UnsolvedFile  # noqa: E402
from app.services.embeddings import upsert_chunk  # noqa: E402
from app.services.notebook import extract_notebook_structure  # noqa: E402
from app.services.storage import absolute_path  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s [%(name)s] %(message)s")
logger = logging.getLogger("backfill_embeddings")


def _backup_sqlite_db() -> Path | None:
    """Back up the real SQLite file before this script writes anything to it.
    Returns the backup path, or None if the DB isn't SQLite / isn't found —
    logged loudly either way, never silently skipped."""
    url = settings.database_url
    if not url.startswith("sqlite:///"):
        logger.warning("database_url is not sqlite (%s) — skipping file-level backup.", url)
        return None

    backend_dir = Path(__file__).resolve().parent.parent
    db_path = Path(url[len("sqlite:///"):])
    if not db_path.is_absolute():
        db_path = backend_dir / db_path

    if not db_path.exists():
        logger.warning("DB file not found at %s — skipping backup.", db_path)
        return None

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.pre-embedding-backfill-backup-{ts}")
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup_path))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    logger.info("Backed up %s -> %s", db_path, backup_path)
    return backup_path


def backfill_lecture_chunks(db) -> tuple[int, int, int]:
    """Embed every not-yet-embedded LectureChunk. Returns (embedded, skipped, failed)."""
    chunks = db.query(LectureChunk).filter(LectureChunk.embedded.is_(False)).all()
    embedded = skipped = failed = 0
    for chunk in chunks:
        if not chunk.chunk_text or not chunk.chunk_text.strip():
            skipped += 1
            continue
        try:
            upsert_chunk(
                chunk_id=f"lecture:{chunk.id}",
                text=chunk.chunk_text,
                metadata={
                    "source_type": "lecture",
                    "source_file_id": chunk.lecture_file_id,
                    "session_id": chunk.lecture_file.session_id,
                    "slide_number": chunk.slide_number,
                    "source": chunk.source.value,
                },
            )
            chunk.embedded = True
            chunk.embedding_error = None
            embedded += 1
        except Exception as exc:  # noqa: BLE001 — deliberate, see module docstring
            chunk.embedded = False
            chunk.embedding_error = str(exc)
            failed += 1
            logger.warning("Failed to embed LectureChunk id=%s: %s", chunk.id, exc)
    db.commit()
    return embedded, skipped, failed


def backfill_unsolved_files(db) -> tuple[int, int, int, int]:
    """
    Embed every non-blank markdown/code cell of every not-yet-embedded
    UnsolvedFile. Returns (files_embedded, cells_embedded, cells_skipped,
    files_failed).

    Never touches Submission/SubmissionFile — only ever queries UnsolvedFile,
    the instructor-uploaded unsolved assignment files.
    """
    files = db.query(UnsolvedFile).filter(UnsolvedFile.embedded.is_(False)).all()
    files_embedded = files_failed = cells_embedded = cells_skipped = 0
    for unsolved in files:
        structure = extract_notebook_structure(str(absolute_path(unsolved.file_path)))
        if not structure["valid"]:
            unsolved.embedded = False
            unsolved.embedding_error = structure["error"]
            files_failed += 1
            logger.warning(
                "Failed to parse UnsolvedFile id=%s for embedding: %s",
                unsolved.id, structure["error"],
            )
            continue

        errors: list[str] = []
        for cell_index, cell in enumerate(structure["cells"]):
            content = cell["content"]
            if not content or not content.strip():
                cells_skipped += 1
                continue
            try:
                upsert_chunk(
                    chunk_id=f"notebook:{unsolved.id}:{cell_index}",
                    text=content,
                    metadata={
                        "source_type": "notebook",
                        "source_file_id": unsolved.id,
                        "session_id": unsolved.session_id,
                        "cell_index": cell_index,
                        "cell_type": cell["type"],
                    },
                )
                cells_embedded += 1
            except Exception as exc:  # noqa: BLE001 — deliberate, see module docstring
                errors.append(f"cell {cell_index} ({cell['type']}): {exc}")

        if errors:
            unsolved.embedded = False
            unsolved.embedding_error = "; ".join(errors)
            files_failed += 1
            logger.warning(
                "Embedding failed for %d cell(s) of UnsolvedFile id=%s: %s",
                len(errors), unsolved.id, unsolved.embedding_error,
            )
        else:
            unsolved.embedded = True
            unsolved.embedding_error = None
            files_embedded += 1

    db.commit()
    return files_embedded, cells_embedded, cells_skipped, files_failed


def main() -> None:
    _backup_sqlite_db()

    db = SessionLocal()
    try:
        lc_embedded, lc_skipped, lc_failed = backfill_lecture_chunks(db)
        uf_embedded, uf_cells_embedded, uf_cells_skipped, uf_failed = backfill_unsolved_files(db)
    finally:
        db.close()

    logger.info(
        "LectureChunk backfill: embedded=%d skipped(blank)=%d failed=%d",
        lc_embedded, lc_skipped, lc_failed,
    )
    logger.info(
        "UnsolvedFile backfill: files_embedded=%d cells_embedded=%d cells_skipped(blank)=%d files_failed=%d",
        uf_embedded, uf_cells_embedded, uf_cells_skipped, uf_failed,
    )


if __name__ == "__main__":
    main()
