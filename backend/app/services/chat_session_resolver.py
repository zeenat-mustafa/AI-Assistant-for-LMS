"""
Student chat session resolution — Phase 7, Sub-feature 7.5.

Decides which LMSSession a student's chat question belongs to, so the
student chatbot answers from the right material and uses the right 7.4
memory thread. The student-side counterpart of session_matcher.py (Phase 3):
same "never guess on ambiguity, ask instead" philosophy, but matched on
retrieved course CONTENT rather than on a typed session title.

The flow (signed off in 7.5's Step -1, proposal B):

  With a current_session_id C (the page the student is on, or the session the
  previous turn resolved to — the frontend echoes it back for continuity):
    Redirect to another session S only when the question is CLEARLY about S:
    S's best broad-search similarity >= BROAD_MIN_SIMILARITY and it beats C's
    best scoped similarity by >= REDIRECT_MARGIN. Otherwise stay in C — even
    when retrieval is weak, so follow-ups ("go deeper on that", "explain
    this") keep working in the same thread.

  Without context:
    If the question points at an unnamed "this assignment / lab / notebook /
    homework / exercise / task / week / session", ALWAYS ask — added after 7.5
    Step 6 (option (b), signed off): "What should I do for the TODO in this
    assignment?" scored 0.538 (asked) while "what do I do for the TODO in this
    assignment" scored 0.566 (resolved), so no similarity threshold can tell a
    vague reference from a specific question. "this code" is deliberately NOT
    included — students usually paste the code, which makes the question
    specific. Otherwise:
    Resolve to the top session when its best similarity >=
    BROAD_MIN_SIMILARITY AND it is a clear winner: it leads the runner-up
    session by >= BROAD_CLEAR_MARGIN, or holds >= BROAD_MAJORITY_SHARE of the
    broad-search hits. Otherwise ask the student to clarify, offering the top
    MAX_CLARIFICATION_CANDIDATES sessions. Zero relevant results → ask which
    session, with no candidates.

Real-data note (7.5 Step -1 probe): the same Week10_Day2.ipynb is uploaded to
sessions 5, 6 and 7, so some questions legitimately split across sessions —
asking is correct there, not a resolver bug.

Public API
──────────
    resolve_session(db, student_id, question, current_session_id=None) -> ResolutionResult
    build_clarification_message(candidates) -> str
"""

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session as DBSession

from app.models.session import LMSSession
from app.services.embeddings import retrieve

logger = logging.getLogger(__name__)

# Starting values from the Step -1 real probe — validated in Step 6's real
# testing; see the 7.5 report for final values.
BROAD_MIN_SIMILARITY = 0.55
REDIRECT_MARGIN = 0.15
BROAD_CLEAR_MARGIN = 0.10
BROAD_MAJORITY_SHARE = 0.70
SCOPED_TOP_K = 5
BROAD_TOP_K = 10
MAX_CLARIFICATION_CANDIDATES = 3

# Vague reference to an unnamed session-level thing — only consulted when no
# current_session_id was sent (with context, "this assignment" means the
# current one). See the module docstring for why this is a rule, not a threshold.
_VAGUE_SESSION_REFERENCE = re.compile(
    r"\bthis\s+(?:assignment|lab|notebook|homework|exercise|task|week|session)\b",
    re.IGNORECASE,
)


def has_vague_session_reference(question: str) -> bool:
    return bool(_VAGUE_SESSION_REFERENCE.search(question or ""))


@dataclass
class ResolutionResult:
    status: str  # "resolved" | "clarification_needed"
    session_id: int | None = None
    session_title: str | None = None
    # "current_session" | "redirected" | "broad_search" — only when resolved.
    resolution: str | None = None
    # [{"session_id", "session_title", "best_similarity"}] — only when clarifying.
    candidates: list[dict] = field(default_factory=list)


def _session_stats(results: list[dict]) -> dict[int, dict]:
    """session_id -> {"best": max similarity, "hits": result count}."""
    stats: dict[int, dict] = {}
    for result in results:
        sid = result.get("session_id")
        if sid is None:
            continue
        entry = stats.setdefault(sid, {"best": 0.0, "hits": 0})
        entry["best"] = max(entry["best"], float(result.get("similarity") or 0.0))
        entry["hits"] += 1
    return stats


def _titles(db: DBSession, session_ids) -> dict[int, str]:
    ids = list(session_ids)
    if not ids:
        return {}
    rows = db.query(LMSSession.id, LMSSession.title).filter(LMSSession.id.in_(ids)).all()
    return {row.id: row.title for row in rows}


def _resolved(session_id: int, title: str, resolution: str) -> ResolutionResult:
    return ResolutionResult(
        status="resolved", session_id=session_id, session_title=title, resolution=resolution,
    )


def resolve_session(
    db: DBSession,
    student_id: int,
    question: str,
    current_session_id: int | None = None,
) -> ResolutionResult:
    """
    Resolve *question* to one LMSSession, or ask for clarification.

    student_id is accepted for the caller's convenience and future use (e.g.
    preferring sessions the student has worked in); it does not affect
    ranking today — every student can see every session's material.

    The caller must have already confirmed current_session_id exists. Never
    raises: any unexpected failure stays in the current session when one was
    given, or asks for clarification otherwise — never a fabricated match.
    """
    try:
        broad = retrieve(question, session_id=None, top_k=BROAD_TOP_K)
        stats = _session_stats(broad)
        titles = _titles(db, set(stats) | ({current_session_id} if current_session_id else set()))
        # Chunks whose session no longer exists can't be resolved to.
        stats = {sid: s for sid, s in stats.items() if sid in titles}

        if current_session_id is not None:
            scoped = retrieve(question, session_id=current_session_id, top_k=SCOPED_TOP_K)
            current_best = max((float(r.get("similarity") or 0.0) for r in scoped), default=0.0)
            others = [(sid, s) for sid, s in stats.items() if sid != current_session_id]
            if others:
                other_sid, other = max(others, key=lambda item: item[1]["best"])
                if (
                    other["best"] >= BROAD_MIN_SIMILARITY
                    and other["best"] - current_best >= REDIRECT_MARGIN
                ):
                    return _resolved(other_sid, titles[other_sid], "redirected")
            return _resolved(current_session_id, titles.get(current_session_id), "current_session")

        ranked = sorted(stats.items(), key=lambda item: item[1]["best"], reverse=True)
        candidates = [
            {"session_id": sid, "session_title": titles[sid], "best_similarity": round(s["best"], 4)}
            for sid, s in ranked[:MAX_CLARIFICATION_CANDIDATES]
        ]

        if has_vague_session_reference(question):
            return ResolutionResult(status="clarification_needed", candidates=candidates)

        if not stats:
            return ResolutionResult(status="clarification_needed")

        top_sid, top = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else None
        total_hits = sum(s["hits"] for _, s in ranked)
        clear_winner = (
            runner_up is None
            or top["best"] - runner_up["best"] >= BROAD_CLEAR_MARGIN
            or top["hits"] / total_hits >= BROAD_MAJORITY_SHARE
        )
        if top["best"] >= BROAD_MIN_SIMILARITY and clear_winner:
            return _resolved(top_sid, titles[top_sid], "broad_search")

        return ResolutionResult(status="clarification_needed", candidates=candidates)
    except Exception as exc:  # noqa: BLE001 — never raises, see docstring
        logger.warning(
            "resolve_session failed for student=%s question=%r current=%s: %s",
            student_id, question, current_session_id, exc,
        )
        if current_session_id is not None:
            title = None
            try:
                title = _titles(db, [current_session_id]).get(current_session_id)
            except Exception:  # noqa: BLE001
                pass
            return _resolved(current_session_id, title, "current_session")
        return ResolutionResult(status="clarification_needed")


def build_clarification_message(candidates: list[dict]) -> str:
    """Clarification wording, matching Phase 3's chat_response_formatter tone."""
    titles = [c.get("session_title") for c in candidates if c.get("session_title")]
    if len(titles) >= 2:
        return (
            "I found a few sessions that could match — did you mean one of these? "
            f"{', '.join(titles)}"
        )
    if len(titles) == 1:
        return f"I'm not sure which session that question is about — did you mean {titles[0]}?"
    return (
        "I couldn't find course material matching that question. "
        "Which session is it about?"
    )
