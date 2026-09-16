from datetime import datetime

from pydantic import BaseModel


class LectureFileRead(BaseModel):
    """
    Outward-facing representation of one LectureFile upload. Carries
    extraction status/error so the instructor can see a real failure reason
    (per Locked Decisions: never fabricate placeholder extracted content).
    Never carries chunk text itself — that's internal, for 7.2's retrieval use.
    """
    id: int
    session_id: int
    instructor_id: int | None
    original_filename: str
    content_type: str | None
    extracted: bool
    extraction_error: str | None
    uploaded_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, obj) -> "LectureFileRead":
        return cls(
            id=obj.id,
            session_id=obj.session_id,
            instructor_id=obj.instructor_id,
            original_filename=obj.original_filename,
            content_type=obj.content_type,
            extracted=obj.extracted,
            extraction_error=obj.extraction_error,
            uploaded_at=obj.uploaded_at,
        )
