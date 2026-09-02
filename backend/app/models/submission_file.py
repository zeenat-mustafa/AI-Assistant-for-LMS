from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SubmissionFile(Base):
    """
    A single .ipynb notebook extracted from a student's Submission.
    - If the submission was a .ipynb directly, there is exactly one SubmissionFile.
    - If the submission was a .zip, there is one SubmissionFile per .ipynb found
      anywhere in the archive (recursive extraction).
    - matched_unsolved_file_id is NULL when no confident match was found during
      grading (instructor is notified).
    """

    __tablename__ = "submission_files"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # May be NULL — set during the grading pipeline's matching step.
    matched_unsolved_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("unsolved_files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Filename within the archive (or the original filename for a direct .ipynb upload).
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Path to the extracted .ipynb on disk, relative to storage_root.
    extracted_ipynb_path: Mapped[str] = mapped_column(String(500), nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────────
    submission: Mapped["Submission"] = relationship(  # noqa: F821
        "Submission", back_populates="submission_files"
    )
    matched_unsolved_file: Mapped["UnsolvedFile | None"] = relationship(  # noqa: F821
        "UnsolvedFile", back_populates="submission_files"
    )
    grade: Mapped["Grade | None"] = relationship(  # noqa: F821
        "Grade", back_populates="submission_file", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<SubmissionFile id={self.id} submission_id={self.submission_id} "
            f"matched={self.matched_unsolved_file_id}>"
        )
