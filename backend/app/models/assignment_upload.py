from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AssignmentUpload(Base):
    """
    One row per actual upload event to a session's assignment-files endpoint —
    the exact file the instructor submitted, regardless of what it contains
    (a lone notebook, a lone resource, or a zip bundling either/both, at any
    folder depth).

    bugfix-original-upload-preservation: this is the ONLY thing listing,
    display, and download ever serve for assignment files. A zip is one row
    with its own filename, never a browsable list of what is inside it.

    The existing extraction into UnsolvedFile (notebooks, for the grading
    pipeline) and ResourceFile (everything else found inside a zip, for
    download-only display that no longer happens) keeps running exactly as it
    did before this fix — file_matcher.py, grading_pipeline.py, evaluator.py,
    rubric.py and every Phase 4 MCP tool are completely unaware this table
    exists; they still just operate on whatever UnsolvedFile/SubmissionFile
    rows happen to be there. The only link between the two sides is the
    reverse FK (UnsolvedFile.source_upload_id / ResourceFile.source_upload_id),
    which exists solely so deleting an original upload also removes what it
    produced — grading code never reads it.
    """

    __tablename__ = "assignment_uploads"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("lms_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Exact filename as uploaded — a zip's own name, never what's inside it.
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # As reported by the client (e.g. "application/zip", "application/pdf").
    # Not authoritative or validated — passed straight through on download.
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Path to the RAW uploaded bytes, byte-for-byte, relative to storage_root.
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    session: Mapped["LMSSession"] = relationship(  # noqa: F821
        "LMSSession", back_populates="assignment_uploads"
    )

    def __repr__(self) -> str:
        return (
            f"<AssignmentUpload id={self.id} filename={self.original_filename!r} "
            f"session_id={self.session_id}>"
        )
