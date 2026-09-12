# Import all models here so that SQLAlchemy's metadata is fully populated
# before create_all() is called in main.py.
from app.models.user import User
from app.models.session import LMSSession
from app.models.assignment_upload import AssignmentUpload
from app.models.unsolved_file import UnsolvedFile
from app.models.resource_file import ResourceFile
from app.models.submission import Submission
from app.models.submission_upload import SubmissionUpload
from app.models.submission_file import SubmissionFile
from app.models.grade import Grade
from app.models.lecture_file import LectureFile
from app.models.lecture_chunk import LectureChunk, ChunkSource

__all__ = [
    "User",
    "LMSSession",
    "AssignmentUpload",
    "UnsolvedFile",
    "ResourceFile",
    "Submission",
    "SubmissionUpload",
    "SubmissionFile",
    "Grade",
    "LectureFile",
    "LectureChunk",
    "ChunkSource",
]
