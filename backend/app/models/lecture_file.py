from datetime import datetime, timezone
from sqlalchemy import String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LectureFile(Base):
    """
    One row per instructor-uploaded lecture file (.pptx only) for a Session —
    Phase 7.1's first structural area beyond the existing three (Assignment
    Files, Submissions, Grades & Feedback).

    Unlike AssignmentUpload/SubmissionUpload, a lecture upload is never a zip
    bundling several files — it is always exactly one .pptx — so there is no
    "raw upload event vs. derived pieces" split needed on the file side.
    This row IS the upload event AND the one file it produced; only the
    extracted TEXT (LectureChunk rows) is derived from it.

    extracted / extraction_error are richer than UnsolvedFile's
    implicit-null-on-parse-failure convention, deliberately: an instructor
    must be able to see a real failure reason for a lecture upload, not just
    silence, per the sub-feature's "never silently drop the upload" rule.
    """

    __tablename__ = "lecture_files"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("lms_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Display-only, mirroring LMSSession.instructor_id — never used to gate
    # access (shared faculty workspace), only to show who uploaded it.
    instructor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # As reported by the client. Not authoritative — passed through on download.
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Path to the RAW uploaded .pptx bytes, byte-for-byte, relative to storage_root.
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # True once extraction + chunking completed successfully. False on failure
    # (the raw file is still saved and this row still created either way).
    extracted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Real failure reason when extracted=False. NULL when extracted=True.
    # Never fabricated — left NULL on success, populated only with the actual
    # exception/validation message on failure.
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    session: Mapped["LMSSession"] = relationship(  # noqa: F821
        "LMSSession", back_populates="lecture_files"
    )
    chunks: Mapped[list["LectureChunk"]] = relationship(  # noqa: F821
        "LectureChunk", back_populates="lecture_file", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<LectureFile id={self.id} filename={self.original_filename!r} "
            f"session_id={self.session_id} extracted={self.extracted}>"
        )
