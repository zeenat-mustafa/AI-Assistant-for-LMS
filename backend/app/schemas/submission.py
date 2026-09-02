from datetime import datetime
from pydantic import BaseModel


class SubmissionFileRead(BaseModel):
    id: int
    original_filename: str
    matched_unsolved_file_id: int | None
    # True once a Grade row exists for this submission file.
    graded: bool

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, obj) -> "SubmissionFileRead":
        return cls(
            id=obj.id,
            original_filename=obj.original_filename,
            matched_unsolved_file_id=obj.matched_unsolved_file_id,
            graded=obj.grade is not None,
        )


class SubmissionRead(BaseModel):
    """
    Outward-facing representation of a student's submission.
    Does not expose internal file paths.
    """
    id: int
    session_id: int
    student_id: int
    original_filename: str
    submitted_at: datetime
    files: list[SubmissionFileRead] = []

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, obj) -> "SubmissionRead":
        return cls(
            id=obj.id,
            session_id=obj.session_id,
            student_id=obj.student_id,
            original_filename=obj.original_filename,
            submitted_at=obj.submitted_at,
            files=[SubmissionFileRead.from_orm_model(f) for f in obj.submission_files],
        )
