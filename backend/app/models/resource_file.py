from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ResourceFile(Base):
    """
    A supporting (non-notebook) file an instructor uploaded alongside a
    session's assignment notebooks — a dataset a notebook reads, slides
    explaining the task, a reference PDF.

    Deliberately a SEPARATE table from UnsolvedFile, not a role flag on it.
    Resource files are downloadable material only: they are never matched to
    a submission, never given a rubric, and never counted toward a session's
    assignment total. Because they live in their own table, those grading
    paths (file_matcher's candidate pool, rubric generation,
    _get_total_assignment_count) exclude them structurally — by querying
    UnsolvedFile, which simply does not contain them — rather than by
    remembering to filter a role column. This mirrors the project's core
    principle that a file's role comes from which area it was uploaded to,
    not from anything inferred about the file itself.
    """

    __tablename__ = "resource_files"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("lms_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Original filename as uploaded by the instructor.
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Path relative to storage_root — stored in the same per-session
    # assignments directory as notebooks, since it is the same upload action.
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    session: Mapped["LMSSession"] = relationship(  # noqa: F821
        "LMSSession", back_populates="resource_files"
    )

    def __repr__(self) -> str:
        return (
            f"<ResourceFile id={self.id} filename={self.original_filename!r} "
            f"session_id={self.session_id}>"
        )
