from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LMSSession(Base):
    """
    A grading session created by an instructor for a specific week/day
    (e.g. "Week 8 Day 4").  Named LMSSession to avoid shadowing Python's
    built-in 'session' concept and SQLAlchemy's Session class.
    """

    __tablename__ = "lms_sessions"
    __table_args__ = (
        UniqueConstraint("instructor_id", "title", name="uq_lms_sessions_instructor_title"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # Nullable so pre-existing rows (created before instructor ownership was
    # tracked) and direct ORM test fixtures don't need a real User row —
    # every session created through the API always gets this set.
    instructor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Short, consistent title — e.g. "Week 8 Day 4". Used for fuzzy matching.
    # Unique per-instructor (see __table_args__), not globally.
    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    instructor: Mapped["User | None"] = relationship(  # noqa: F821
        "User", back_populates="owned_sessions"
    )
    unsolved_files: Mapped[list["UnsolvedFile"]] = relationship(  # noqa: F821
        "UnsolvedFile", back_populates="session", cascade="all, delete-orphan"
    )
    submissions: Mapped[list["Submission"]] = relationship(  # noqa: F821
        "Submission", back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<LMSSession id={self.id} title={self.title!r}>"
