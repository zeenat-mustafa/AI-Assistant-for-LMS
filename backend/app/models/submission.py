from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Submission(Base):
    """
    A student's upload for a Session.  This is the raw upload record —
    it may be a single .ipynb or a .zip archive.  Individual extracted
    notebooks are tracked in SubmissionFile.
    """

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("lms_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Original filename as uploaded by the student.
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Path relative to storage_root, e.g. "{session_id}/submissions/{student_id}/hw1.ipynb"
    uploaded_file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    session: Mapped["LMSSession"] = relationship(  # noqa: F821
        "LMSSession", back_populates="submissions"
    )
    student: Mapped["User"] = relationship(  # noqa: F821
        "User", back_populates="submissions"
    )
    submission_files: Mapped[list["SubmissionFile"]] = relationship(  # noqa: F821
        "SubmissionFile", back_populates="submission", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Submission id={self.id} student_id={self.student_id} session_id={self.session_id}>"
