from datetime import datetime

from pydantic import BaseModel


class AssignmentUploadRead(BaseModel):
    """
    Outward-facing representation of one AssignmentUpload — the exact file
    an instructor uploaded, whatever it was (a notebook, a resource, or a
    zip bundling either/both). This is the only shape listing/download ever
    serves for assignment files; it deliberately carries no notion of what
    the upload extracted into internally (no file_role, no rubric_generated).
    """
    id: int
    session_id: int
    original_filename: str
    content_type: str | None
    uploaded_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, obj) -> "AssignmentUploadRead":
        return cls(
            id=obj.id,
            session_id=obj.session_id,
            original_filename=obj.original_filename,
            content_type=obj.content_type,
            uploaded_at=obj.uploaded_at,
        )
