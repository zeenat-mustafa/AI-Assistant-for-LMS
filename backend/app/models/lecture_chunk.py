from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, ForeignKey, DateTime, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.database import Base


class ChunkSource(str, enum.Enum):
    """Which part of a slide a chunk's text came from — never merged/flattened
    together, so 7.2's citations can say "slide text" vs. "speaker notes"."""
    slide_text = "slide_text"
    notes = "notes"


class LectureChunk(Base):
    """
    One chunk of extracted text from a LectureFile, scoped to exactly one
    (slide_number, source) pair — a chunk never spans two slides, and
    (deliberately stricter than the locked requirement) never spans slide
    text and speaker notes either, since those are kept as separate
    chunk-eligible pools. This gives 7.2's future citations unambiguous
    granularity: "Slide 14, speaker notes, part 2" rather than an arbitrary
    length-cut across slide or text/notes boundaries.

    No embedding-related fields here (no vector id, no `embedded` flag) —
    that is 7.2's job.
    """

    __tablename__ = "lecture_chunks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    lecture_file_id: Mapped[int] = mapped_column(
        ForeignKey("lecture_files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slide_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source: Mapped[ChunkSource] = mapped_column(SAEnum(ChunkSource), nullable=False)
    # Order of this chunk within its (slide_number, source) pair — 0-based.
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    lecture_file: Mapped["LectureFile"] = relationship(  # noqa: F821
        "LectureFile", back_populates="chunks"
    )

    def __repr__(self) -> str:
        return (
            f"<LectureChunk id={self.id} lecture_file_id={self.lecture_file_id} "
            f"slide={self.slide_number} source={self.source.value} idx={self.chunk_index}>"
        )
