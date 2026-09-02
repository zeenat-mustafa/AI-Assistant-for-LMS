"""
Storage abstraction layer.

All file I/O goes through this module.  The backing store is the local
filesystem today, but every path is resolved through a single root so
switching to cloud storage later only requires replacing the functions here.

Directory layout under storage_root:
    {session_id}/
        assignments/
            {original_filename}       ← instructor-uploaded unsolved files
        submissions/
            {student_id}/
                {original_filename}   ← raw student upload (.ipynb or .zip)
                extracted/            ← .ipynb files unpacked from a .zip
"""

import shutil
import logging
from pathlib import Path

import aiofiles

from app.config import settings

logger = logging.getLogger(__name__)

# ── Root resolution ───────────────────────────────────────────────────────────

def _storage_root() -> Path:
    """
    Resolve the storage root to an absolute path.
    settings.storage_root can be absolute or relative to the backend/ directory.
    """
    root = Path(settings.storage_root)
    if not root.is_absolute():
        # Resolve relative to the backend/ directory (one level above app/).
        backend_dir = Path(__file__).resolve().parent.parent.parent
        root = backend_dir / root
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── Path helpers ──────────────────────────────────────────────────────────────

def assignment_dir(session_id: int) -> Path:
    """Directory for a session's instructor-uploaded assignment files."""
    p = _storage_root() / str(session_id) / "assignments"
    p.mkdir(parents=True, exist_ok=True)
    return p


def submission_dir(session_id: int, student_id: int) -> Path:
    """Directory for a specific student's raw upload within a session."""
    p = _storage_root() / str(session_id) / "submissions" / str(student_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def submission_extract_dir(session_id: int, student_id: int) -> Path:
    """Directory where .ipynb files extracted from a student's .zip are placed."""
    p = submission_dir(session_id, student_id) / "extracted"
    p.mkdir(parents=True, exist_ok=True)
    return p


def relative_path(absolute: Path) -> str:
    """
    Return the path string relative to storage_root.
    This is what gets stored in the database (portable, not machine-specific).
    """
    return str(absolute.relative_to(_storage_root()))


def absolute_path(relative: str) -> Path:
    """Reconstruct an absolute path from a relative path stored in the DB."""
    return _storage_root() / relative


# ── Write helpers ─────────────────────────────────────────────────────────────

async def save_assignment_file(session_id: int, filename: str, data: bytes) -> str:
    """
    Persist an instructor-uploaded assignment file.
    Returns the relative path stored in the DB.
    """
    dest = assignment_dir(session_id) / _safe_filename(filename)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(data)
    logger.info("Saved assignment file: %s", dest)
    return relative_path(dest)


async def save_submission_file(
    session_id: int, student_id: int, filename: str, data: bytes
) -> str:
    """
    Persist a student's raw upload (.ipynb or .zip).
    Returns the relative path stored in the DB.
    """
    dest = submission_dir(session_id, student_id) / _safe_filename(filename)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(data)
    logger.info("Saved submission file: %s", dest)
    return relative_path(dest)


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_assignment_file_path(session_id: int, filename: str) -> Path:
    """Return the absolute path to an assignment file, or raise FileNotFoundError."""
    p = assignment_dir(session_id) / _safe_filename(filename)
    if not p.exists():
        raise FileNotFoundError(f"Assignment file not found: {p}")
    return p


def get_submission_file_path(relative: str) -> Path:
    """Resolve a relative DB path back to an absolute path."""
    p = absolute_path(relative)
    if not p.exists():
        raise FileNotFoundError(f"Submission file not found: {p}")
    return p


# ── Delete helpers ────────────────────────────────────────────────────────────

def delete_session_storage(session_id: int) -> None:
    """Remove all stored files for a session (used if a session is deleted)."""
    session_dir = _storage_root() / str(session_id)
    if session_dir.exists():
        shutil.rmtree(session_dir)
        logger.info("Deleted storage for session %d", session_id)


# ── Utility ───────────────────────────────────────────────────────────────────

def _safe_filename(filename: str) -> str:
    """
    Sanitise a filename: keep only the final path component and replace
    any characters that are unsafe on Windows/Linux/macOS.
    """
    # Take only the basename in case the client sends a path like ../../evil
    name = Path(filename).name
    # Replace characters that cause issues on common filesystems
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "_")
    return name or "upload"
