from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class QuizAttempt(Base):
    """
    One student-triggered PRACTICE quiz and (once submitted) its result —
    Phase 7, Sub-feature 7.6.

    Completely separate from real grading: nothing here ever reads or writes
    Grade, and score is a practice score that never affects a real grade.

    The JSON columns are Text holding JSON strings, matching the existing
    rubric_json/rationale_json convention (decided in 7.6, D5):
      scope_detail          assignment_file   → {"unsolved_file_id": int}
                            session           → {"session_id": int}
                            multiple_sessions → {"session_ids": [int, ...]}
                            topic             → {"topic_text": str}
                            uploaded_file     → {"original_filename": str, "file_type": "pptx"|"ipynb"}
                            (never a stored path — an uploaded file is never persisted)
      questions_json        exactly 5 × {"question", "options" (4, stored in
                            shuffled order), "correct_option_index" (0-3),
                            "source_citation"}. correct_option_index must never
                            reach the client before submission.
      student_answers_json  5 option indices, NULL until submitted.

    Ids inside scope_detail are deliberately not foreign keys: a history row
    records what the quiz was about and must survive a session or file being
    deleted later.
    """

    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_detail: Mapped[str] = mapped_column(Text, nullable=False)
    questions_json: Mapped[str] = mapped_column(Text, nullable=False)
    student_answers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0-5 practice score, NULL until submitted. Never a real grade.
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_score: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────────
    student: Mapped["User"] = relationship(  # noqa: F821
        "User", back_populates="quiz_attempts"
    )

    def __repr__(self) -> str:
        return (
            f"<QuizAttempt id={self.id} student_id={self.student_id} "
            f"scope_type={self.scope_type!r} score={self.score}>"
        )
