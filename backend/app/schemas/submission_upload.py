from datetime import datetime

from pydantic import BaseModel


class SubmissionUploadRead(BaseModel):
    """
    Outward-facing representation of one SubmissionUpload — the exact file a
    student uploaded, whatever it was (a notebook, a non-notebook file, or a
    zip bundling either/both). This is the only shape listing, instructor
    download, and per-item delete ever serve for submission uploads; it
    deliberately carries no notion of what the upload extracted into
    internally (no matched_unsolved_file_id, no graded flag — see
    SubmissionFileRead for that).
    """
    id: int
    submission_id: int
    original_filename: str
    content_type: str | None
    uploaded_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, obj) -> "SubmissionUploadRead":
        return cls(
            id=obj.id,
            submission_id=obj.submission_id,
            original_filename=obj.original_filename,
            content_type=obj.content_type,
            uploaded_at=obj.uploaded_at,
        )
