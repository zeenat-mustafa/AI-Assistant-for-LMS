from datetime import datetime
from pydantic import BaseModel
import json


class RationaleEntry(BaseModel):
    """One row in the criterion-by-criterion rubric breakdown."""
    criterion: str
    points_possible: float
    points_awarded: float
    explanation: str


class GradeRead(BaseModel):
    """
    Full grade detail for one SubmissionFile.
    rationale is parsed from rationale_json for typed access.
    """
    id: int
    submission_file_id: int
    original_filename: str        # for display — denormalised from SubmissionFile
    score: float                  # out of 10
    feedback_text: str
    rationale: list[RationaleEntry] | None = None
    graded_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, grade_obj, submission_file_obj) -> "GradeRead":
        rationale = None
        if grade_obj.rationale_json:
            try:
                rationale = [RationaleEntry(**r) for r in json.loads(grade_obj.rationale_json)]
            except Exception:
                rationale = None  # malformed JSON — surface score/feedback anyway
        return cls(
            id=grade_obj.id,
            submission_file_id=grade_obj.submission_file_id,
            original_filename=submission_file_obj.original_filename,
            score=grade_obj.score,
            feedback_text=grade_obj.feedback_text,
            rationale=rationale,
            graded_at=grade_obj.graded_at,
        )


class GradeSummary(BaseModel):
    """
    Per-student summary for a Session.
    per_file_scores: list of (unsolved_filename, score) pairs.
    combined_score: average of per_file_scores, normalised to 0–10.
    """
    student_id: int
    student_name: str
    per_file: list[GradeRead]
    combined_score: float | None  # None if no files were graded yet


class SessionGradeReport(BaseModel):
    """Full grading report for a Session — one GradeSummary per student."""
    session_id: int
    session_title: str
    students: list[GradeSummary]
