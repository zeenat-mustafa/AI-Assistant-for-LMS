# Import all models here so that SQLAlchemy's metadata is fully populated
# before create_all() is called in main.py.
from app.models.user import User
from app.models.session import LMSSession
from app.models.unsolved_file import UnsolvedFile
from app.models.submission import Submission
from app.models.submission_file import SubmissionFile
from app.models.grade import Grade

__all__ = [
    "User",
    "LMSSession",
    "UnsolvedFile",
    "Submission",
    "SubmissionFile",
    "Grade",
]
