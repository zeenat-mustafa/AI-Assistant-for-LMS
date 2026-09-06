"""
Phase 3, Sub-feature 3.1: Session matching (typed instruction -> LMSSession).

Given a free-text instructor instruction (e.g. "grade week 8 day 3"), identify
which existing LMSSession row it refers to. This is a lookup problem, not a
discovery problem — sessions already exist with real titles; the instruction
just phrases the reference loosely. Follows the same weighted-similarity /
confidence-threshold philosophy as app/services/file_matcher.py (Phase 2),
never force-matching when ambiguous.

Public API
──────────
    match_instruction_to_session(instruction, instructor_id, db) -> dict
"""

import difflib
import json
import logging
import re

from sqlalchemy.orm import Session as DBSession

from app.models.session import LMSSession
from app.services import llm_provider

logger = logging.getLogger(__name__)

# Separate from file_matcher.py's 0.55 threshold — same value for now, tuned
# independently later. Do not conflate the two.
SESSION_MATCH_THRESHOLD = 0.55
CLEAR_WINNER_MARGIN = 0.15
LLM_FALLBACK_LOWER_BOUND = 0.35
MAX_AMBIGUOUS_CANDIDATES = 5


# ── 1. _normalize_text ────────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """
    Clean a text string for comparison: lowercase, strip common punctuation,
    collapse whitespace. Mirrors file_matcher.py's normalize_text.
    """
    text = text.lower()
    text = re.sub(r"[#*\-_`]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── 2. _title_similarity_score ────────────────────────────────────────────────

def _tokenize_pairing_numbers(normalized_text: str) -> list[str]:
    """
    Split into word/number tokens, then merge each number into the word
    immediately preceding it ("week", "8" -> "week8") so a title's numbers
    stay tied to their role (week vs. day) instead of floating free in a
    bag of words. A leading number with no preceding word stays standalone.
    """
    raw_tokens = re.findall(r"[a-z]+|[0-9]+", normalized_text)
    merged: list[str] = []
    i = 0
    while i < len(raw_tokens):
        token = raw_tokens[i]
        if (
            token.isalpha()
            and i + 1 < len(raw_tokens)
            and raw_tokens[i + 1].isdigit()
        ):
            merged.append(token + raw_tokens[i + 1])
            i += 2
        else:
            merged.append(token)
            i += 1
    return merged


_WORD_NUMBER_RE = re.compile(r"^([a-z]*)([0-9]*)$")


def _split_word_number(token: str) -> tuple[str, str]:
    """Every token from _tokenize_pairing_numbers is alpha, digits, or an
    alpha prefix followed by digits — split it into those two parts."""
    match = _WORD_NUMBER_RE.match(token)
    if match:
        return match.group(1), match.group(2)
    return token, ""


def _token_similarity(a: str, b: str) -> float:
    """
    Compare two tokens from _tokenize_pairing_numbers.

    If both tokens carry a numeric part (a bare number, or the trailing
    digits of a merged "word+number" token like "day1"), the numbers must
    match EXACTLY or the pair scores 0 — regardless of how well the
    surrounding word matches. Found via real dev-data testing: plain
    character-level SequenceMatcher still gave "day1" vs "day2" a ~0.75
    ratio (3 of 4 characters shared), which was enough to keep an unrelated
    "Week 1 Day 1" / "Week 3 Day 1" inside the ambiguity margin against an
    instruction asking for "Week 2 Day 1" — a single-digit difference is a
    completely different week/day, not a "close" one, so it gets none of
    the partial credit a word typo would.

    Word-only tokens (no numeric part on either side) keep the existing
    fuzzy SequenceMatcher behavior — typos/partial word matches are a real,
    valid case there and are left alone.
    """
    a_word, a_num = _split_word_number(a)
    b_word, b_num = _split_word_number(b)

    if a_num and b_num:
        if a_num != b_num:
            return 0.0
        if not a_word and not b_word:
            return 1.0
        return difflib.SequenceMatcher(None, a_word, b_word).ratio()

    return difflib.SequenceMatcher(None, a, b).ratio()


def _title_similarity_score(instruction: str, session_title: str) -> float:
    """
    Measure how strongly session_title is referenced within instruction.

    The instruction is a full sentence ("please grade the week 8 day 3
    assignments") while the title is short ("Week 8 - Day 3"), so a naive
    whole-string SequenceMatcher ratio would unfairly punish a perfect
    reference just because the instruction has extra surrounding words —
    the same "does the shorter text's content appear within the longer text"
    problem file_matcher.py's content_similarity_score solves for submissions
    vs. unsolved-file text.

    We score per-word rather than per-character: each of the title's words
    is matched against its single best-fitting word anywhere in the
    instruction (via SequenceMatcher, so a typo still scores partial
    credit), and the final score is the average of those per-word best
    matches. Character-level containment alone was tried first and rejected
    — titles following a shared "Week N Day M" template all contain the
    same long common substrings ("week ", " day "), so a completely
    unrelated session ("Week 1 Day 1") scored a false-positive ~0.83
    against an instruction about "Week 8 Day 3" purely from those filler
    words, drowning out the one word (the day/week number) that actually
    distinguishes sessions. Scoring word-by-word means a mismatched number
    ("1" has no match among "8", "day", "3") drags that title's score down
    instead of being absorbed into a long shared substring.

    Word pairing is greedy one-to-one (repeatedly take the best remaining
    (title_word, instruction_word) pair, then remove both) rather than each
    title word independently picking its own best match — otherwise a title
    with a repeated word (e.g. "Week 1 Day 1", where "1" is both the week
    and day number) could have two different title words both match the
    same single instruction word, double-counting it.

    A word immediately followed by a number is merged into one compound
    token ("week"+"8" -> "week8") before matching — found via real dev-data
    testing: plain word-level tokens treat the title as a bag of words, so
    "Week 2 Day 1" and "Week 1 Day 2" both reduce to the same token set
    {week, 1, day, 2} and score identically (1.0) against an instruction
    naming either one, even though they're different sessions. Merging
    ties each number to whatever word precedes it, so "week2"/"day1" can
    no longer match "week1"/"day2" as exact hits — a swapped-number title
    now scores meaningfully lower instead of tying with the correct one.

    Numeric tokens are compared exact-match-or-nothing (see
    _token_similarity), not via fuzzy character overlap — "day1" and "day2"
    still share 3 of 4 characters, which was enough to keep an unrelated
    same-day-different-week session inside the ambiguity margin.
    """
    normalized_instruction = _normalize_text(instruction)
    normalized_title = _normalize_text(session_title)
    if not normalized_title or not normalized_instruction:
        return 0.0

    title_words = _tokenize_pairing_numbers(normalized_title)
    instruction_words = _tokenize_pairing_numbers(normalized_instruction)
    if not title_words or not instruction_words:
        return 0.0

    total = 0.0
    remaining_instruction_words = list(instruction_words)
    remaining_title_words = list(title_words)
    while remaining_title_words:
        best_score = -1.0
        best_ti = 0
        best_ii = 0
        for ti, tw in enumerate(remaining_title_words):
            for ii, iw in enumerate(remaining_instruction_words):
                s = _token_similarity(tw, iw)
                if s > best_score:
                    best_score = s
                    best_ti, best_ii = ti, ii
        total += best_score
        remaining_title_words.pop(best_ti)
        remaining_instruction_words.pop(best_ii)
        if not remaining_instruction_words:
            # No instruction words left to pair with — every further title
            # word contributes 0.
            break

    return total / len(title_words)


# ── 3. LLM fallback helpers ───────────────────────────────────────────────────

def _build_llm_prompt(instruction: str, sessions: list[LMSSession]) -> str:
    candidate_lines = "\n".join(
        f'- id={s.id}, title="{s.title}", created_at={s.created_at.isoformat()}'
        for s in sessions
    )
    return (
        "You are matching an instructor's free-text instruction to one of "
        "their existing grading sessions. The instruction may use loose "
        "phrasing, relative dates, or a nickname for the session instead of "
        "its exact title.\n\n"
        f"Instruction: \"{instruction}\"\n\n"
        "Candidate sessions:\n"
        f"{candidate_lines}\n\n"
        "Decide which session(s) the instruction most plausibly refers to. "
        "Respond with ONLY a strict JSON object, no markdown fences, no "
        "extra text, in exactly one of these three shapes:\n"
        '  {"status": "matched", "session_id": <id>}\n'
        '  {"status": "ambiguous", "session_ids": [<id>, <id>, ...]}\n'
        '  {"status": "no_match"}\n'
    )


def _parse_llm_response(raw_text: str, sessions_by_id: dict[int, LMSSession]) -> dict:
    text = (raw_text or "").strip()

    if "```" in text:
        fence_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
        match = re.search(fence_pattern, text, re.IGNORECASE)
        if match:
            text = match.group(1).strip()

    if not text.startswith("{"):
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1]

    data = json.loads(text)  # let json.JSONDecodeError propagate to the caller

    status = data.get("status")

    if status == "matched":
        session_id = data.get("session_id")
        session = sessions_by_id.get(session_id)
        if session is None:
            return {"status": "no_match"}
        return {
            "status": "matched",
            "session_id": session.id,
            "session_title": session.title,
            "confidence": None,
        }

    if status == "ambiguous":
        candidate_ids = data.get("session_ids") or []
        candidates = [
            {
                "session_id": sessions_by_id[sid].id,
                "session_title": sessions_by_id[sid].title,
                "confidence": None,
            }
            for sid in candidate_ids
            if sid in sessions_by_id
        ][:MAX_AMBIGUOUS_CANDIDATES]
        if len(candidates) < 2:
            return {"status": "no_match"}
        return {"status": "ambiguous", "candidates": candidates}

    return {"status": "no_match"}


def _match_via_llm(instruction: str, sessions: list[LMSSession]) -> dict:
    """
    Ask the LLM to interpret looser phrasing ("the RAG one", "yesterday's
    session") that pure string similarity couldn't resolve confidently.
    Never raises — any failure (LLM call, malformed JSON, unknown ids)
    degrades to {"status": "no_match"} since this is a lookup helper, not
    something that should ever crash the caller.
    """
    sessions_by_id = {s.id: s for s in sessions}
    try:
        prompt = _build_llm_prompt(instruction, sessions)
        raw_text = llm_provider.call_llm(prompt, purpose="fast")
        return _parse_llm_response(raw_text, sessions_by_id)
    except Exception as exc:
        logger.warning(
            "session_matcher: LLM fallback failed for instruction=%r: %s",
            instruction, exc,
        )
        return {"status": "no_match"}


# ── 4. match_instruction_to_session ───────────────────────────────────────────

def match_instruction_to_session(
    instruction: str, instructor_id: int, db: DBSession
) -> dict:
    """
    Identify which of this instructor's LMSSession rows *instruction* refers to.

    Returns one of:
        {"status": "matched", "session_id": int, "session_title": str, "confidence": float}
        {"status": "ambiguous", "candidates": [{"session_id", "session_title", "confidence"}, ...]}
        {"status": "no_match"}
    """
    sessions = (
        db.query(LMSSession)
        .filter(LMSSession.instructor_id == instructor_id)
        .all()
    )

    if not sessions:
        return {"status": "no_match"}

    scored = sorted(
        (
            {"session": s, "confidence": _title_similarity_score(instruction, s.title)}
            for s in sessions
        ),
        key=lambda entry: entry["confidence"],
        reverse=True,
    )

    best = scored[0]
    second = scored[1] if len(scored) > 1 else None

    if best["confidence"] >= SESSION_MATCH_THRESHOLD:
        clear_winner = second is None or (
            best["confidence"] - second["confidence"] >= CLEAR_WINNER_MARGIN
        )
        if clear_winner:
            return {
                "status": "matched",
                "session_id": best["session"].id,
                "session_title": best["session"].title,
                "confidence": best["confidence"],
            }

        # No clear winner — collect every candidate within CLEAR_WINNER_MARGIN
        # of the best score (not just those individually above threshold):
        # "not a clear winner" was decided by comparing best against the
        # single next-best score, so that runner-up — even if it happens to
        # sit just under SESSION_MATCH_THRESHOLD itself — is exactly why we
        # aren't confident, and must appear in the candidate list. Filtering
        # by SESSION_MATCH_THRESHOLD here instead can produce a nonsensical
        # single-item "ambiguous" list when the runner-up is close but
        # sub-threshold (seen on real data: "grade week 10" scored Week 10
        # Day 3 at 0.5625 with no other candidate above 0.55, so the old
        # threshold filter left only one entry despite reporting "ambiguous").
        candidates = [
            {
                "session_id": entry["session"].id,
                "session_title": entry["session"].title,
                "confidence": entry["confidence"],
            }
            for entry in scored
            if best["confidence"] - entry["confidence"] <= CLEAR_WINNER_MARGIN
        ][:MAX_AMBIGUOUS_CANDIDATES]
        return {"status": "ambiguous", "candidates": candidates}

    if best["confidence"] >= LLM_FALLBACK_LOWER_BOUND:
        return _match_via_llm(instruction, sessions)

    return {"status": "no_match"}
