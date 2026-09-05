"""
Phase 2, Sub-feature 2: Content-based file matching.

Matches each SubmissionFile (a student's extracted .ipynb) against the
UnsolvedFile records for the same session, using a weighted combination of
content similarity and filename similarity.

Public API
──────────
    normalize_text(text)                            → cleaned str
    content_similarity_score(submitted, unsolved)   → float 0–1
    filename_similarity_score(submitted, unsolved)  → float 0–1
    match_submission_file_to_unsolved(...)          → dict
    match_all_files_in_submission(db, submission_id) → list[dict]
"""

import difflib
import logging
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session as DBSession

logger = logging.getLogger(__name__)


# ── 1. normalize_text ─────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """
    Clean a text string for comparison.

    Steps:
    - Lowercase
    - Remove markdown symbols: #, *, -, _, backticks
    - Collapse all whitespace (spaces, newlines, tabs) to single spaces
    - Strip leading/trailing whitespace
    """
    text = text.lower()
    # Remove markdown symbols
    text = re.sub(r"[#*\-_`]", " ", text)
    # Collapse all whitespace runs (including newlines/tabs) to a single space
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── 2. content_similarity_score ───────────────────────────────────────────────

def content_similarity_score(submitted_text: str, unsolved_text: str) -> float:
    """
    Measure how much the unsolved file's requirements text is reflected in the
    submitted text, using SequenceMatcher.

    The matcher is ordered (a=unsolved, b=submitted) so the ratio reflects how
    much of the unsolved file appears within the submission — a one-directional
    signal that's robust even when submissions contain extra student work.

    Returns 0.0 if unsolved_text is empty/None after normalization.
    """
    if not unsolved_text:
        return 0.0

    normalized_unsolved = normalize_text(unsolved_text)
    if not normalized_unsolved:
        return 0.0

    normalized_submitted = normalize_text(submitted_text) if submitted_text else ""

    return difflib.SequenceMatcher(
        None,
        a=normalized_unsolved,
        b=normalized_submitted,
    ).ratio()


# ── 3. filename_similarity_score ──────────────────────────────────────────────

def filename_similarity_score(submitted_filename: str, unsolved_filename: str) -> float:
    """
    Compare filenames (without extensions) using SequenceMatcher.

    Both names are lowercased and stripped of non-alphanumeric characters
    before comparison.
    """
    def _clean(name: str) -> str:
        # Strip the file extension
        stem = Path(name).stem
        # Lowercase and remove everything that isn't a letter or digit
        return re.sub(r"[^a-z0-9]", "", stem.lower())

    cleaned_submitted = _clean(submitted_filename)
    cleaned_unsolved = _clean(unsolved_filename)

    return difflib.SequenceMatcher(
        None,
        a=cleaned_unsolved,
        b=cleaned_submitted,
    ).ratio()


# ── 4. match_submission_file_to_unsolved ──────────────────────────────────────

def match_submission_file_to_unsolved(
    submitted_markdown: str,
    submitted_filename: str,
    unsolved_candidates: list[dict],
) -> dict:
    """
    Match a single submitted notebook against a list of unsolved file candidates.

    Parameters
    ----------
    submitted_markdown:
        The markdown text extracted from the student's notebook
        (from parse_notebook_file).
    submitted_filename:
        The original filename of the student's notebook (e.g. "hw1_solved.ipynb").
    unsolved_candidates:
        List of dicts: [{"id": int, "filename": str, "requirements_text": str}, ...]
        ``requirements_text`` may be empty/None for an unsolved file with no
        markdown cells at all (e.g. instructions given purely as code
        comments) — such a candidate is never excluded from the pool; see
        the per-candidate scoring note below.

    Returns
    -------
    dict with keys:
        matched_unsolved_file_id  – int or None
        confidence                – float 0–1
        status                    – "matched" | "ambiguous" | "no_unsolved_files"
    """
    if not unsolved_candidates:
        return {
            "matched_unsolved_file_id": None,
            "confidence": 0.0,
            "status": "no_unsolved_files",
        }

    # With only one candidate there is no ambiguity possible — always match.
    if len(unsolved_candidates) == 1:
        return {
            "matched_unsolved_file_id": unsolved_candidates[0]["id"],
            "confidence": 1.0,
            "status": "matched",
        }

    # Score every candidate and pick the best one.
    best_candidate: Optional[dict] = None
    best_score: float = -1.0

    for candidate in unsolved_candidates:
        requirements_text = candidate.get("requirements_text")
        filename_score = filename_similarity_score(
            submitted_filename,
            candidate["filename"],
        )
        if requirements_text:
            # Content carries 80% of the weight; filename is a tiebreaker signal.
            content_score = content_similarity_score(submitted_markdown, requirements_text)
            combined_score = (0.8 * content_score) + (0.2 * filename_score)
        else:
            # No requirements text was ever extracted for this candidate (e.g.
            # its unsolved notebook has no markdown cells — instructions given
            # purely as code comments/TODOs). There is no content signal to
            # compare, so fall back to filename similarity alone rather than
            # excluding this candidate from the pool: dropping it here would
            # shrink the candidate list and could trigger the single-candidate
            # shortcut above for an unrelated remaining file, force-matching
            # submissions to the wrong assignment.
            combined_score = filename_score

        if combined_score > best_score:
            best_score = combined_score
            best_candidate = candidate

    # Confidence threshold: below 0.55 means we couldn't reliably pick a winner.
    if best_score >= 0.55:
        return {
            "matched_unsolved_file_id": best_candidate["id"],
            "confidence": best_score,
            "status": "matched",
        }

    # TODO: Sub-feature 6 will add an LLM-based fallback here for ambiguous
    # cases before giving up.
    return {
        "matched_unsolved_file_id": None,
        "confidence": best_score,
        "status": "ambiguous",
    }


# ── 5. match_all_files_in_submission ─────────────────────────────────────────

def match_all_files_in_submission(db: DBSession, submission_id: int) -> list[dict]:
    """
    Orchestrate matching for every SubmissionFile in a submission.

    Steps
    -----
    1. Load the Submission and its SubmissionFile rows.
    2. Build unsolved_candidates from every UnsolvedFile row for the same
       session — including ones whose parsed_requirements_text is empty/None
       (e.g. a notebook with no markdown cells, instructions given purely as
       code comments). Such candidates are still scored by
       match_submission_file_to_unsolved(), just via filename similarity
       alone instead of content — never silently dropped from the pool.
    3. For each SubmissionFile, parse its .ipynb from disk and call
       match_submission_file_to_unsolved().
    4. Write matched_unsolved_file_id back to the DB and commit.
    5. Log a WARNING (not an error) for any "ambiguous" or "no_unsolved_files"
       result so instructors can be notified.

    Returns
    -------
    list[dict] — one entry per SubmissionFile:
        {
            "submission_file_id":     int,
            "original_filename":      str,
            "matched_unsolved_file_id": int | None,
            "confidence":             float,
            "status":                 str,
        }
    """
    # Deferred imports keep this module importable without a live DB session
    # and avoid circular imports at module load time.
    from app.models.submission import Submission
    from app.models.submission_file import SubmissionFile
    from app.models.unsolved_file import UnsolvedFile
    from app.services.notebook import parse_notebook_file
    from app.services.storage import absolute_path

    submission = db.get(Submission, submission_id)
    if submission is None:
        logger.warning(
            "match_all_files_in_submission: Submission %d not found.", submission_id
        )
        return []

    session_id = submission.session_id

    # Load every unsolved file for this session — including ones with no
    # extracted requirements text (e.g. code-comment-only notebooks). These
    # are still valid candidates; match_submission_file_to_unsolved() falls
    # back to filename-only scoring for them rather than dropping them.
    unsolved_rows = (
        db.query(UnsolvedFile)
        .filter(UnsolvedFile.session_id == session_id)
        .all()
    )

    unsolved_candidates = [
        {
            "id": uf.id,
            "filename": uf.original_filename,
            "requirements_text": uf.parsed_requirements_text,
        }
        for uf in unsolved_rows
    ]

    results: list[dict] = []

    submission_files = (
        db.query(SubmissionFile)
        .filter(SubmissionFile.submission_id == submission_id)
        .all()
    )

    for sf in submission_files:
        # Resolve the absolute path from the relative path stored in the DB.
        try:
            abs_path = absolute_path(sf.extracted_ipynb_path)
        except Exception as exc:
            logger.warning(
                "match_all_files_in_submission: could not resolve path for "
                "SubmissionFile %d ('%s'): %s",
                sf.id, sf.extracted_ipynb_path, exc,
            )
            results.append({
                "submission_file_id": sf.id,
                "original_filename": sf.original_filename,
                "matched_unsolved_file_id": None,
                "confidence": 0.0,
                "status": "ambiguous",
            })
            continue

        # Parse the notebook to get its markdown content.
        parsed = parse_notebook_file(abs_path)
        submitted_markdown = parsed["markdown_text"] if parsed["valid"] else ""

        if not parsed["valid"]:
            logger.warning(
                "match_all_files_in_submission: SubmissionFile %d could not be "
                "parsed: %s",
                sf.id, parsed["error"],
            )

        # Run the matcher.
        match_result = match_submission_file_to_unsolved(
            submitted_markdown=submitted_markdown,
            submitted_filename=sf.original_filename,
            unsolved_candidates=unsolved_candidates,
        )

        # Persist the result back to the DB.
        sf.matched_unsolved_file_id = match_result["matched_unsolved_file_id"]

        if match_result["status"] in ("ambiguous", "no_unsolved_files"):
            logger.warning(
                "match_all_files_in_submission: SubmissionFile %d ('%s') could "
                "not be confidently matched (status=%s, confidence=%.3f). "
                "Instructor review required.",
                sf.id,
                sf.original_filename,
                match_result["status"],
                match_result["confidence"],
            )

        results.append({
            "submission_file_id": sf.id,
            "original_filename": sf.original_filename,
            "matched_unsolved_file_id": match_result["matched_unsolved_file_id"],
            "confidence": match_result["confidence"],
            "status": match_result["status"],
        })

    db.commit()

    logger.info(
        "match_all_files_in_submission: submission %d — %d file(s) processed, "
        "%d matched, %d unmatched.",
        submission_id,
        len(results),
        sum(1 for r in results if r["status"] == "matched"),
        sum(1 for r in results if r["status"] != "matched"),
    )

    return results
