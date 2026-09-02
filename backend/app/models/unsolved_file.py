from datetime import datetime, timezone
from sqlalchemy import String, Text, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UnsolvedFile(Base):
    """
    An instructor-uploaded assignment file attached to a Session.
    Structurally separate from student submissions — the system never
    confuses these two categories regardless of filename.
    """

    __tablename__ = "unsolved_files"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("lms_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Original filename as uploaded by the instructor.
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Path relative to storage_root, e.g. "{session_id}/assignments/hw1.ipynb"
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # Extracted instruction/markdown/docstring text — parsed from the notebook
    # cells at upload time and stored for rubric generation + file matching.
    parsed_requirements_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Generated rubric stored as JSON (criteria + point breakdown, summing to 10).
    # NULL until the file is graded for the first time; reused for all students after.
    rubric_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    session: Mapped["LMSSession"] = relationship(  # noqa: F821
        "LMSSession", back_populates="unsolved_files"
    )
    submission_files: Mapped[list["SubmissionFile"]] = relationship(  # noqa: F821
        "SubmissionFile", back_populates="matched_unsolved_file"
    )

    def __repr__(self) -> str:
        return f"<UnsolvedFile id={self.id} filename={self.original_filename!r} session_id={self.session_id}>"
