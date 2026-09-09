"""
Phase 3, Sub-feature 3.2: Filter parsing (optional student scope from an
instruction).

Given the same free-text instructor instruction used for session matching
(3.1), extract WHO within an already-identified session should be graded:
everyone (the default), or one specific named student. Session matching
itself is out of scope here — session_matcher.py is neither called nor
modified; the caller (3.3) is responsible for resolving session_id first.

Pure parsing only: no DB writes, no calls into the grading pipeline.

Return shapes
──────────────
    {"scope": "all"}
        No plausible student-name reference found in the instruction — the
        default, common case (most instructions won't name a student).
    {"scope": "student", "student_id": int, "student_name": str}
        Exactly one student in this session's candidate pool (students with
        at least one Submission for this session) was referenced.
    {"scope": "ambiguous", "candidates": [{"student_id": int, "student_name": str}, ...]}
        A name-like reference matched more than one student in this
        session's candidate pool (e.g. two students both named "Ali") —
        never guess, same philosophy as session_matcher.py.
    {"scope": "not_found", "attempted_name": str}
        A name-like reference was detected but matches no student who has a
        submission in THIS session (covers both a typo and a real student
        from a different session — either way, nobody in this session's
        pool matches).
    {"scope": "unsupported", "reason": str}
        The instruction names a student but with exclusionary phrasing
        ("everyone except Ali", "grade all without Bob") — the pipeline has
        no "exclude this student" concept today. This must never fall
        through to a normal "student" match: matching Ali as the INCLUSION
        target would silently do the opposite of what the instructor asked.
    {"scope": "unrecognized"}
        The instruction doesn't resemble a grading command at all — after
        removing the session's own words and grading vocabulary, what's left
        is a run of ordinary words (2+), not introduced by "for", that match
        no student (e.g. "how many assignments are ungraded"). This is a
        non-grading question that happened to sit next to a session name;
        returning "student_not_found" for it would wrongly imply the system
        understood the question and just couldn't find the first word as a
        student. A single leftover word, or any "for <name>" phrase, is still
        treated as a genuine (mistyped/absent) student reference — see
        not_found — so real grading commands are never misclassified.

Public API
──────────
    parse_grading_filter(instruction, session_id, db) -> dict
"""

import re

from sqlalchemy.orm import Session as DBSession

from app.models.session import LMSSession
from app.models.submission import Submission
from app.models.user import User

# Function/instructional words that are never themselves a student's name.
# Deliberately small — the session's own title words are excluded per-call
# separately (see _title_words), which is what actually guards against
# arbitrary course-topic vocabulary ("Classification", "Linear Regression",
# "RAG Pipeline", ...) being mistaken for a name reference.
_STOPWORDS = {
    "a", "an", "the", "this", "that", "these", "those", "in", "on", "at",
    "for", "to", "of", "and", "or", "is", "are", "be", "me",
    "please", "grade", "grading", "graded", "regrade", "only", "all",
    "everyone", "everybody", "students", "student",
    "session", "sessions", "week", "weeks", "day", "days",
    "assignment", "assignments", "submission", "submissions",
    "file", "files", "today", "now", "just",
    # Exclusion-phrasing words themselves are never a name.
    "except", "excluding", "without", "other", "than",
}

_MIN_TOKEN_LEN = 2

# Single-word exclusion triggers, plus the two-word "other than" phrase,
# checked immediately before a matched name-like token's position.
_EXCLUSION_TRIGGERS = {"except", "excluding", "without"}
_EXCLUSION_PHRASE = ("other", "than")


def _title_words(db: DBSession, session_id: int) -> set[str]:
    """Lowercased alphabetic words from this session's own title."""
    session = db.get(LMSSession, session_id)
    if session is None:
        return set()
    return {w.lower() for w in re.findall(r"[A-Za-z]+", session.title)}


def _has_exclusion_before(tokens_lower: list[str], idx: int) -> bool:
    """True if the word (or two-word phrase) immediately before tokens_lower[idx]
    is an exclusion trigger ("except"/"excluding"/"without"/"other than")."""
    if idx >= 1 and tokens_lower[idx - 1] in _EXCLUSION_TRIGGERS:
        return True
    if (
        idx >= 2
        and tokens_lower[idx - 2] == _EXCLUSION_PHRASE[0]
        and tokens_lower[idx - 1] == _EXCLUSION_PHRASE[1]
    ):
        return True
    return False


def _extract_name_like_tokens(
    instruction: str, exclude: set[str]
) -> tuple[list[str], bool, bool]:
    """
    Pull out words from *instruction* that could plausibly be a student's
    name: alphabetic, not a stopword, not part of this session's own title,
    and at least _MIN_TOKEN_LEN long (this also naturally drops the stray
    "s" left over from a possessive like "Ali's" once split on non-letters).
    Original casing is preserved (for a readable attempted_name), de-duped
    case-insensitively, order preserved.

    Returns (name_like_tokens, exclusion_detected, first_after_for).
    exclusion_detected is True if ANY qualifying occurrence — even a later,
    deduped-away repeat — was immediately preceded by an exclusion trigger,
    so the caller can refuse to match rather than silently including the
    excluded student. first_after_for is True when the FIRST emitted token
    was immediately preceded by the word "for" (the grading DSL's
    student-scoping preposition, "grade <session> for <name>"), which marks
    even a multi-word phrase ("for Ali Hassan") as a deliberate student
    reference rather than a stray non-grading question.
    """
    tokens = re.findall(r"[A-Za-z]+", instruction)
    tokens_lower = [t.lower() for t in tokens]

    seen_lower: set[str] = set()
    result: list[str] = []
    exclusion_detected = False
    first_after_for = False
    for idx, token in enumerate(tokens):
        lower = tokens_lower[idx]
        if len(lower) < _MIN_TOKEN_LEN:
            continue
        if lower in _STOPWORDS or lower in exclude:
            continue
        if _has_exclusion_before(tokens_lower, idx):
            exclusion_detected = True
        if lower in seen_lower:
            continue
        if not result and idx >= 1 and tokens_lower[idx - 1] == "for":
            first_after_for = True
        seen_lower.add(lower)
        result.append(token)
    return result, exclusion_detected, first_after_for


def _candidate_students(db: DBSession, session_id: int) -> list[User]:
    """Every User with at least one Submission for this session."""
    return (
        db.query(User)
        .join(Submission, Submission.student_id == User.id)
        .filter(Submission.session_id == session_id)
        .distinct()
        .all()
    )


def _name_matches_token(name_words: list[str], token_lower: str) -> bool:
    for name_word in name_words:
        if name_word == token_lower:
            return True
        # Partial/nickname match (e.g. "Nat" for "Natalie") — guarded by a
        # minimum length so short tokens can't substring-match everything.
        if len(token_lower) >= 3 and (
            token_lower in name_word or name_word in token_lower
        ):
            return True
    return False


def parse_grading_filter(instruction: str, session_id: int, db: DBSession) -> dict:
    """
    Determine whether *instruction* targets every student in *session_id*
    (the default) or one specific named student. See the module docstring
    for the exact five return shapes.
    """
    exclude = _title_words(db, session_id)
    name_like_tokens, exclusion_detected, first_after_for = _extract_name_like_tokens(
        instruction, exclude
    )

    if not name_like_tokens:
        return {"scope": "all"}

    if exclusion_detected:
        # Never let this fall through to a "student" match — that would
        # match the excluded student as an INCLUSION target, silently doing
        # the opposite of what the instructor asked.
        return {
            "scope": "unsupported",
            "reason": "exclusionary filters not yet supported",
        }

    candidates = _candidate_students(db, session_id)

    matched: list[User] = []
    matched_ids: set[int] = set()
    for student in candidates:
        name_words = student.name.lower().split()
        if any(
            _name_matches_token(name_words, token.lower())
            for token in name_like_tokens
        ):
            if student.id not in matched_ids:
                matched.append(student)
                matched_ids.add(student.id)

    if len(matched) == 1:
        student = matched[0]
        return {
            "scope": "student",
            "student_id": student.id,
            "student_name": student.name,
        }

    if len(matched) >= 2:
        return {
            "scope": "ambiguous",
            "candidates": [
                {"student_id": s.id, "student_name": s.name} for s in matched
            ],
        }

    # Nothing matched a student. Distinguish a genuine (mistyped/absent)
    # student reference from a non-grading instruction that merely sat next
    # to a session name. A run of 2+ leftover words NOT introduced by "for"
    # (e.g. "how many assignments are ungraded") is prose, not a name — a
    # single word, or any "for <name>" phrase, is still treated as a real
    # student reference so "grade Week 3 Day 1 for Zaid" keeps its precise
    # student_not_found message.
    if len(name_like_tokens) >= 2 and not first_after_for:
        return {"scope": "unrecognized"}

    return {"scope": "not_found", "attempted_name": name_like_tokens[0]}
