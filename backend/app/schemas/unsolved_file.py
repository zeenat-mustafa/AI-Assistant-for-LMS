from datetime import datetime
from pydantic import BaseModel


class UnsolvedFileRead(BaseModel):
    """
    Outward-facing representation of an instructor-uploaded assignment file.
    Does not expose file_path (internal storage detail) or rubric_json
    (internal grading detail) to the frontend.
    """
    id: int
    session_id: int
    original_filename: str
    # True once the rubric has been generated (rubric_json is not NULL).
    rubric_generated: bool
    uploaded_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, obj) -> "UnsolvedFileRead":
        return cls(
            id=obj.id,
            session_id=obj.session_id,
            original_filename=obj.original_filename,
            rubric_generated=obj.rubric_json is not None,
            uploaded_at=obj.uploaded_at,
        )
