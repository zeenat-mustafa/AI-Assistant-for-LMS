from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ResourceFileRead(BaseModel):
    """
    Outward-facing representation of a supporting/resource file. Has no rubric
    or grading concept — resources are downloadable material only.
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


class AssignmentUploadItem(BaseModel):
    """
    One entry in the assignment-upload response. A unified view over the two
    distinct tables: notebooks come from UnsolvedFile, resources from
    ResourceFile. `file_role` is DERIVED from which table the row came from —
    it is never stored on either model — so the response can present both in
    one list while the underlying storage stays structurally separate.
    """
    id: int
    session_id: int
    original_filename: str
    file_role: Literal["notebook", "resource"]
    # True only for a graded notebook; always False for a resource.
    rubric_generated: bool
    uploaded_at: datetime

    @classmethod
    def from_notebook(cls, obj) -> "AssignmentUploadItem":
        return cls(
            id=obj.id,
            session_id=obj.session_id,
            original_filename=obj.original_filename,
            file_role="notebook",
            rubric_generated=obj.rubric_json is not None,
            uploaded_at=obj.uploaded_at,
        )

    @classmethod
    def from_resource(cls, obj) -> "AssignmentUploadItem":
        return cls(
            id=obj.id,
            session_id=obj.session_id,
            original_filename=obj.original_filename,
            file_role="resource",
            rubric_generated=False,
            uploaded_at=obj.uploaded_at,
        )
