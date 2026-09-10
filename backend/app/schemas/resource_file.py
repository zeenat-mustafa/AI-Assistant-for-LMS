from datetime import datetime

from pydantic import BaseModel


class ResourceFileRead(BaseModel):
    """
    Outward-facing representation of a supporting/resource file. Has no rubric
    or grading concept — resources are downloadable material only.

    bugfix-original-upload-preservation: no longer independently listed or
    downloaded anywhere. Extraction into this table still happens internally
    exactly as before (nothing here changed), but display now serves the
    AssignmentUpload row it came from instead — this schema now exists only
    for that internal bookkeeping, not for any API response.
    """
    id: int
    session_id: int
    original_filename: str
    uploaded_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, obj) -> "ResourceFileRead":
        return cls(
            id=obj.id,
            session_id=obj.session_id,
            original_filename=obj.original_filename,
            uploaded_at=obj.uploaded_at,
        )
