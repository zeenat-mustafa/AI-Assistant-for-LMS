from datetime import datetime
from pydantic import BaseModel, field_validator


class SessionCreate(BaseModel):
    """Body for POST /sessions — instructor creates a new grading session."""
    title: str

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Session title must not be empty.")
        return v


class SessionRead(BaseModel):
    """Full session detail including its assignment files."""
    id: int
    title: str
    instructor_id: int | None = None
    # Display only -- instructor access is a shared faculty workspace (see
    # README), so this is never used to gate who can see/edit a session,
    # only to show who created it.
    instructor_name: str | None = None
    created_at: datetime
    # What instructors actually uploaded, one row per upload event — a zip is
    # one row with its own filename, never a list of what's inside it. The
    # ONLY thing the assignment-files UI lists or downloads.
    assignment_uploads: list["AssignmentUploadRead"] = []  # noqa: F821
    # Gradeable notebooks (UnsolvedFile rows), extracted internally from the
    # uploads above for the grading pipeline. Kept here (not display, just
    # data) so totalAssignmentFiles-style counts keep working without a
    # separate request — never rendered as its own list anymore.
    unsolved_files: list["UnsolvedFileRead"] = []  # noqa: F821
    # Supporting files (ResourceFile rows) extracted the same way. Kept for
    # the same internal-count reason; also never rendered as its own list.
    resource_files: list["ResourceFileRead"] = []  # noqa: F821

    model_config = {"from_attributes": True}


class SessionList(BaseModel):
    """Paginated session listing."""
    total: int
    items: list["SessionRead"]


# Avoid circular import — the file schemas are defined in their own modules
# and referenced here. We update the forward refs after all modules load.
from app.schemas.assignment_upload import AssignmentUploadRead  # noqa: E402
from app.schemas.unsolved_file import UnsolvedFileRead  # noqa: E402
from app.schemas.resource_file import ResourceFileRead  # noqa: E402

SessionRead.model_rebuild()
