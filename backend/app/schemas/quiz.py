"""
Schemas for the practice quiz endpoints — Phase 7, Sub-feature 7.6.

Two rules every response schema here follows:
  - correct_option_index is NEVER part of a schema returned before the
    attempt is submitted (QuizAttemptOut / QuizQuestionOut), so the answer
    can't leak to the client early.
  - Every score is explicitly labeled as a practice score that does not
    affect real grades (not_a_real_grade + notice + score_label).
"""

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictInt, model_validator

PRACTICE_QUIZ_NOTICE = "Practice quiz — this score does not affect your real grades."

TOPIC_TEXT_MAX_CHARS = 200

# The JSON-body scope modes. "uploaded_file" is multipart and has its own
# endpoint (POST /quiz/generate/upload), so it is not accepted here.
JsonScopeType = Literal["assignment_file", "session", "multiple_sessions", "topic"]

_SCOPE_FIELDS = ("unsolved_file_id", "session_id", "session_ids", "topic_text")
_FIELD_FOR_SCOPE = {
    "assignment_file": "unsolved_file_id",
    "session": "session_id",
    "multiple_sessions": "session_ids",
    "topic": "topic_text",
}


def practice_score_label(score: int, max_score: int) -> str:
    return f"{score}/{max_score} (practice quiz — does not affect your real grades)"


class QuizGenerateRequest(BaseModel):
    """Body for POST /quiz/generate. Exactly the one field that belongs to
    scope_type must be set; the others must be left out."""

    scope_type: JsonScopeType
    unsolved_file_id: int | None = None
    session_id: int | None = None
    session_ids: list[int] | None = None
    topic_text: str | None = None

    @model_validator(mode="after")
    def _check_scope_fields(self) -> "QuizGenerateRequest":
        required = _FIELD_FOR_SCOPE[self.scope_type]
        if getattr(self, required) is None:
            raise ValueError(f"{required} is required when scope_type is '{self.scope_type}'")
        extra = [f for f in _SCOPE_FIELDS if f != required and getattr(self, f) is not None]
        if extra:
            raise ValueError(
                f"only {required} may be set when scope_type is '{self.scope_type}' "
                f"(also got: {', '.join(extra)})"
            )

        if self.scope_type == "multiple_sessions":
            unique_ids = list(dict.fromkeys(self.session_ids))
            if len(unique_ids) < 2:
                raise ValueError(
                    "session_ids must contain at least 2 different sessions "
                    "(use scope_type 'session' for one)"
                )
            self.session_ids = unique_ids

        if self.scope_type == "topic":
            # Collapsed to one line: the topic is placed inside the prompt's
            # "Quiz scope:" line, so it must not be able to add lines of its own.
            topic = re.sub(r"\s+", " ", self.topic_text).strip()
            if not topic:
                raise ValueError("topic_text must not be empty")
            if len(topic) > TOPIC_TEXT_MAX_CHARS:
                raise ValueError(f"topic_text must be at most {TOPIC_TEXT_MAX_CHARS} characters")
            self.topic_text = topic

        return self

    def scope_detail(self) -> dict[str, Any]:
        field = _FIELD_FOR_SCOPE[self.scope_type]
        return {field: getattr(self, field)}


class QuizQuestionOut(BaseModel):
    """A question as shown BEFORE submission — never includes the answer."""

    question: str
    options: list[str]
    source_citation: str


class QuizAttemptOut(BaseModel):
    id: int
    scope_type: str
    scope_detail: dict[str, Any]
    questions: list[QuizQuestionOut]
    max_score: int
    submitted: bool
    created_at: datetime
    notice: str = PRACTICE_QUIZ_NOTICE


class QuizSubmitRequest(BaseModel):
    """Body for POST /quiz/{attempt_id}/submit. The attempt id comes from the
    path only — a second copy in the body could only ever disagree with it."""

    answers: Annotated[
        list[Annotated[StrictInt, Field(ge=0, le=3)]],
        Field(min_length=5, max_length=5),
    ]


class QuizQuestionResultOut(QuizQuestionOut):
    correct_option_index: int
    student_answer_index: int
    is_correct: bool


class QuizResultOut(BaseModel):
    attempt_id: int
    scope_type: str
    scope_detail: dict[str, Any]
    score: int
    max_score: int
    score_label: str
    not_a_real_grade: Literal[True] = True
    notice: str = PRACTICE_QUIZ_NOTICE
    questions: list[QuizQuestionResultOut]
    created_at: datetime
    submitted_at: datetime


class QuizHistoryItem(BaseModel):
    attempt_id: int
    scope_type: str
    scope_detail: dict[str, Any]
    score: int
    max_score: int
    score_label: str
    not_a_real_grade: Literal[True] = True
    created_at: datetime
    submitted_at: datetime


class QuizHistoryOut(BaseModel):
    """The student's own SUBMITTED attempts, most recent first. In-progress
    (unsubmitted) attempts are deliberately excluded."""

    attempts: list[QuizHistoryItem]
    not_a_real_grade: Literal[True] = True
    notice: str = PRACTICE_QUIZ_NOTICE
