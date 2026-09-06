"""
Phase 3, Sub-feature 3.5: conversational summary formatting.

Turns a /chat or /chat/stream outcome into one short, human-readable
sentence a chat interface can display directly. Purely additive — the
structured fields (ids, counts, candidate lists, per-file events) are
unchanged and still carry the full detail; this only supplies the prose
that sits alongside them.

Messages are deliberately kept to 1-2 sentences: enumerating every
failure or candidate belongs in the structured data, not the summary
line.

Public API
──────────
    build_response_message(status, **context) -> str
"""


def _pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """"1 submission" / "3 submissions" — count included."""
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural or singular + 's'}"


def _join_names(names: list[str], limit: int = 5) -> str:
    """Comma-join candidate names, capped so the message stays short."""
    shown = [n for n in names if n][:limit]
    remaining = len(names) - len(shown)
    joined = ", ".join(shown)
    if remaining > 0:
        joined += f", and {remaining} more"
    return joined


def _graded_message(context: dict) -> str:
    session_title = context.get("session_title") or "that session"
    summary = context.get("summary") or {}
    total = summary.get("total", 0)
    graded = summary.get("graded", 0)
    failed = summary.get("failed", 0)

    scope = context.get("scope")
    student_name = context.get("student_name")
    who = (
        f" for {student_name}"
        if scope == "student" and student_name
        else ""
    )

    if total == 0:
        return (
            f"There was nothing new to grade{who} in {session_title} — "
            "everything there is already graded."
        )

    message = (
        f"Graded {graded} of {_pluralize(total, 'submission')}{who} "
        f"in {session_title}."
    )
    if failed:
        message += f" {failed} failed — see the details below."
    return message


def build_response_message(status: str, **context) -> str:
    """
    Build the conversational message for one /chat outcome.

    Parameters
    ----------
    status:
        One of "no_session_match", "ambiguous_session", "student_not_found",
        "ambiguous_student", "unsupported_filter", "graded".
    context:
        The rest of that outcome's response dict — each status reads only
        the keys it needs (e.g. "graded" reads session_title/scope/
        student_name/summary), so callers can pass the whole payload.

    An unrecognised status degrades to a neutral sentence rather than
    raising: this is display text, and it must never be the reason a
    grading request fails.
    """
    if status == "no_session_match":
        return (
            "I couldn't find a session matching that instruction. "
            "Could you double-check the session name or date?"
        )

    if status == "ambiguous_session":
        titles = [c.get("session_title", "") for c in context.get("candidates") or []]
        if not titles:
            return "I found more than one session that could match — which did you mean?"
        return (
            "I found a few sessions that could match — did you mean one of these? "
            f"{_join_names(titles)}"
        )

    if status == "student_not_found":
        attempted = context.get("attempted_name")
        if not attempted:
            return "I couldn't find that student in that session."
        return f"I couldn't find a student matching '{attempted}' in that session."

    if status == "ambiguous_student":
        names = [c.get("student_name", "") for c in context.get("candidates") or []]
        if not names:
            return "More than one student could match that name — which did you mean?"
        return f"More than one student could match that name — did you mean: {_join_names(names)}?"

    if status == "unsupported_filter":
        reason = context.get("reason")
        detail = f" ({reason})" if reason else ""
        return (
            f"I can't handle that kind of filtering yet{detail}. "
            "Try naming a single student instead, or grade everyone."
        )

    if status == "graded":
        return _graded_message(context)

    return "I've processed that instruction — see the details below."
