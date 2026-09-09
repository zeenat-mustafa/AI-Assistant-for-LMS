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
    created_at: datetime
    # Gradeable notebooks (UnsolvedFile rows). Unchanged meaning — resources
    # live in their own table and never appear here.
    unsolved_files: list["UnsolvedFileRead"] = []  # noqa: F821
    # Supporting/downloadable-only files (ResourceFile rows): datasets, slides,
    # PDFs. Empty for sessions with no resource uploads.
    resource_files: list["ResourceFileRead"] = []  # noqa: F821

    model_config = {"from_attributes": True}


class SessionList(BaseModel):
    """Paginated session listing."""
    total: int
    items: list["SessionRead"]


# Avoid circular import — the file schemas are defined in their own modules
# and referenced here. We update the forward refs after all modules load.
from app.schemas.unsolved_file import UnsolvedFileRead  # noqa: E402
from app.schemas.resource_file import ResourceFileRead  # noqa: E402

SessionRead.model_rebuild()
