from datetime import datetime
from pydantic import BaseModel

from app.schemas.submission_upload import SubmissionUploadRead


class SubmissionFileRead(BaseModel):
    id: int
    original_filename: str
    matched_unsolved_file_id: int | None
    # True once a Grade row exists for this submission file.
    graded: bool
    # The SubmissionUpload this notebook was extracted from. None only for
    # rows created before this field existed.
    source_upload_id: int | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, obj) -> "SubmissionFileRead":
        return cls(
            id=obj.id,
            original_filename=obj.original_filename,
            matched_unsolved_file_id=obj.matched_unsolved_file_id,
            graded=obj.grade is not None,
            source_upload_id=obj.source_upload_id,
        )


class SubmissionRead(BaseModel):
    """
    Outward-facing representation of a student's submission for one session.

    Uploads are additive (multiple separate SubmissionUpload rows can
    accumulate here) so there is no single `original_filename`/uploaded-path
    for the submission as a whole any more — `uploads` is the authoritative,
    exact-bytes list (mirrors SessionRead.assignment_uploads). `files` is
    still every notebook extracted internally for grading, across every
    upload, unchanged in shape from before this rework.
    """
    id: int
    session_id: int
    student_id: int
    submitted_at: datetime
    uploads: list[SubmissionUploadRead] = []
    files: list[SubmissionFileRead] = []

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, obj) -> "SubmissionRead":
        return cls(
            id=obj.id,
            session_id=obj.session_id,
            student_id=obj.student_id,
            submitted_at=obj.submitted_at,
            uploads=[SubmissionUploadRead.from_orm_model(u) for u in obj.uploads],
            files=[SubmissionFileRead.from_orm_model(f) for f in obj.submission_files],
        )
