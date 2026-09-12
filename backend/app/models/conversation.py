from datetime import datetime, timezone
from sqlalchemy import Text, Integer, ForeignKey, DateTime, UniqueConstraint, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.database import Base


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class ConversationThread(Base):
    """
    One chat-memory thread per (student, LMSSession) pair — Phase 7.4.

    "Session" here always means an LMSSession (e.g. "Week 3 Day 1"), never an
    HTTP/browser session. A student's conversation about one LMSSession is one
    continuous thread; a different LMSSession is a separate thread with no
    shared memory.

    rolling_summary compresses messages older than the recent verbatim window
    (see chat_memory.py). summarized_through_message_id marks the newest
    message already folded into that summary, so the same messages are never
    re-summarized and none are silently skipped. Both stay NULL until the
    first summarization actually happens — never fabricated.
    """

    __tablename__ = "conversation_threads"
    __table_args__ = (
        UniqueConstraint(
            "student_id", "lms_session_id", name="uq_conversation_threads_student_lms_session"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lms_session_id: Mapped[int] = mapped_column(
        ForeignKey("lms_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rolling_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Plain integer, not a FK: it is a bookmark into this thread's own
    # messages, and messages are never deleted independently of the thread.
    summarized_through_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    student: Mapped["User"] = relationship(  # noqa: F821
        "User", back_populates="conversation_threads"
    )
    lms_session: Mapped["LMSSession"] = relationship(  # noqa: F821
        "LMSSession", back_populates="conversation_threads"
    )
    messages: Mapped[list["ConversationMessage"]] = relationship(
        "ConversationMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.id",
    )

    def __repr__(self) -> str:
        return (
            f"<ConversationThread id={self.id} student_id={self.student_id} "
            f"lms_session_id={self.lms_session_id}>"
        )


class ConversationMessage(Base):
    """
    One real user/assistant message in a ConversationThread. Raw messages are
    permanent: summarization only changes what is SENT to the LLM, never what
    is stored here.
    """

    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[MessageRole] = mapped_column(SAEnum(MessageRole), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    thread: Mapped["ConversationThread"] = relationship(
        "ConversationThread", back_populates="messages"
    )

    def __repr__(self) -> str:
        return f"<ConversationMessage id={self.id} thread_id={self.thread_id} role={self.role.value}>"
