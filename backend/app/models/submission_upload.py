from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SubmissionUpload(Base):
    """
    One row per actual upload event a student makes to a session's
    submission area — the exact file they submitted, regardless of what it
    contains (a lone notebook, a lone non-notebook file, or a zip bundling
    either/both, at any folder depth). Mirrors AssignmentUpload exactly, on
    the submission side.

    Uploads are additive: a student can make many of these within one
    Submission (their per-session container) instead of each upload
    replacing the last. This is the ONLY thing listing, instructor download,
    and per-item delete ever operate on for raw submitted bytes.

    Internally, notebooks are still extracted into SubmissionFile rows for
    the grading pipeline exactly as before — file_matcher.py,
    grading_pipeline.py, evaluator.py, rubric.py, and every Phase 4 MCP tool
    are completely unaware this table exists; they still just operate on
    whatever SubmissionFile rows happen to be there. The only link between
    the two sides is the reverse FK (SubmissionFile.source_upload_id), which
    exists solely so deleting an upload also removes what it produced —
    grading code never reads it.
    """

    __tablename__ = "submission_uploads"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
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
    submission: Mapped["Submission"] = relationship(  # noqa: F821
        "Submission", back_populates="uploads"
    )

    def __repr__(self) -> str:
        return (
            f"<SubmissionUpload id={self.id} filename={self.original_filename!r} "
            f"submission_id={self.submission_id}>"
        )
