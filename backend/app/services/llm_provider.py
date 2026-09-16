"""
LLM provider abstraction layer — Phase 2, Sub-feature 6.

Centralises every LLM call behind a single public entry point: call_llm().
The provider chain is two Gemini models and nothing else:

  1. Primary   (settings.gemini_primary_model)  — serves every call.
  2. Fallback  (settings.gemini_fallback_model) — used ONLY when the primary
     hits a quota/rate-limit error.

There is no Groq or Ollama tier any more, and no fast/pro model split: one
primary/fallback pair handles every call regardless of ``purpose``.

Public API
----------
  call_llm(prompt, purpose="fast") -> str
      ``purpose`` is retained for logging/telemetry only — it no longer selects
      a model (every call uses the same primary/fallback pair).

  call_llm_stream(prompt, purpose="fast") -> Iterator[str]   (Phase 7.5)
      Same provider chain, yielding the answer incrementally. Fallback is only
      possible BEFORE the first chunk reaches the caller (streamed text cannot
      be taken back); logs the same "served by" line as call_llm on completion.

  LLMProviderError
      Raised only when BOTH Gemini models fail for the same prompt (or, for
      call_llm_stream, when a stream fails after it has started yielding).

Internal helpers (not part of public API)
-----------------------------------------
  _is_quota_or_rate_limit_error(exc) -> bool
  _call_gemini(prompt, model_name) -> str
  _stream_gemini(prompt, model_name) -> Iterator[str]
  _open_stream(prompt, model_name) -> (first_chunk, stream)
"""

import logging
from collections.abc import Iterator

import google.generativeai as genai
import google.api_core.exceptions

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class LLMProviderError(Exception):
    """Raised when both the primary and fallback Gemini models fail."""
    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_quota_or_rate_limit_error(exc: Exception) -> bool:
    """
    Return True if *exc* represents a quota or rate-limit condition that
    warrants falling back to the secondary model.

    Triggers on:
      - google.api_core.exceptions.ResourceExhausted (canonical Gemini quota type)
      - Any exception whose str() contains "quota", "rate limit", "429", or
        "resource exhausted" (case-insensitive).

    Returns False for all other errors (bad key, malformed request, etc.) —
    those would fail the same way on the fallback model, so retrying there is
    pointless.
    """
    if isinstance(exc, google.api_core.exceptions.ResourceExhausted):
        return True

    exc_str = str(exc).lower()
    quota_signals = ("quota", "rate limit", "429", "resource exhausted")
    return any(signal in exc_str for signal in quota_signals)


def _call_gemini(prompt: str, model_name: str) -> str:
    """
    Call Google Gemini with *model_name* and return the response text.

    Configures the genai client from settings.gemini_api_key each call so
    that the key can be changed at runtime (useful for tests via monkeypatch).
    Raises whatever exception the SDK raises — callers decide what to do.
    """
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(prompt)
    if not response or not response.text:
        raise ValueError("Gemini returned an empty response.")
    return response.text


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def call_llm(prompt: str, purpose: str = "fast") -> str:
    """
    Send *prompt* to Gemini and return the response text.

    Parameters
    ----------
    prompt:
        The full prompt string to send (callers own prompt construction).
    purpose:
        Free-text tag for logging/telemetry ONLY. It no longer selects a model
        — every call uses ``settings.gemini_primary_model`` first and
        ``settings.gemini_fallback_model`` as its quota fallback. Kept so the
        existing call sites (all passing ``purpose="fast"``) need no change and
        so log lines still carry the caller's intent.

    Fallback logic
    --------------
    1. Try the primary Gemini model.
       - Success → log INFO, return result.
       - Quota/rate-limit error → log WARNING, fall through to the fallback model.
       - Any other error (bad key, malformed request, …) → re-raise immediately;
         no fallback, because the same error would occur on the fallback model too.
    2. Try the fallback Gemini model (only reached on a primary quota/rate-limit).
       - Success → log INFO, return result.
       - Any failure → log ERROR with both failure reasons, raise LLMProviderError.

    Raises
    ------
    LLMProviderError
        Only when both Gemini models fail.
    Any other exception
        Re-raised directly when the primary fails with a non-quota error.
    """
    primary_model = settings.gemini_primary_model
    fallback_model = settings.gemini_fallback_model

    # ── Step 1: try the primary Gemini model ─────────────────────────────────
    primary_error: Exception | None = None
    try:
        result = _call_gemini(prompt, primary_model)
        logger.info(
            "LLM call served by gemini primary (%s), purpose=%s",
            primary_model, purpose,
        )
        return result
    except Exception as exc:
        if not _is_quota_or_rate_limit_error(exc):
            # Non-quota failure — re-raise immediately, do not waste a fallback call.
            raise
        primary_error = exc
        logger.warning(
            "Gemini primary (%s) quota/rate-limit hit for purpose=%s, "
            "falling back to %s",
            primary_model, purpose, fallback_model,
        )

    # ── Step 2: try the fallback Gemini model (only on a primary quota hit) ───
    try:
        result = _call_gemini(prompt, fallback_model)
        logger.info(
            "LLM call served by gemini fallback (%s), purpose=%s (fallback)",
            fallback_model, purpose,
        )
        return result
    except Exception as fallback_error:
        logger.error(
            "Both Gemini models failed for purpose=%s: "
            "primary_error=%s, fallback_error=%s",
            purpose, primary_error, fallback_error,
        )
        raise LLMProviderError(
            f"Both Gemini models failed for purpose={purpose}: "
            f"primary_error={primary_error}, fallback_error={fallback_error}"
        ) from fallback_error


# ---------------------------------------------------------------------------
# Streaming (Phase 7.5) — additive; call_llm above is unchanged
# ---------------------------------------------------------------------------

def _chunk_text(chunk) -> str:
    """A streamed chunk with no text parts (e.g. a final finish-reason-only
    chunk) raises ValueError on .text — treat that as empty, not a failure."""
    try:
        return chunk.text or ""
    except ValueError:
        return ""


def _stream_gemini(prompt: str, model_name: str) -> Iterator[str]:
    """
    Stream Google Gemini output for *prompt*, yielding each non-empty text
    chunk. Nothing is sent until the first next() — so connection, auth and
    quota errors surface on that first pull. Raises whatever the SDK raises.
    """
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(prompt, stream=True)
    for chunk in response:
        text = _chunk_text(chunk)
        if text:
            yield text


def _open_stream(prompt: str, model_name: str) -> tuple[str, Iterator[str]]:
    """
    Start a stream and pull its first chunk, so every error that can still be
    recovered from by switching models is raised here, before the caller has
    seen any text.
    """
    stream = _stream_gemini(prompt, model_name)
    try:
        first = next(stream)
    except StopIteration:
        raise ValueError("Gemini returned an empty streamed response.") from None
    return first, stream


def call_llm_stream(prompt: str, purpose: str = "fast") -> Iterator[str]:
    """
    Stream *prompt*'s answer from Gemini, yielding text incrementally.

    Same two-tier chain as call_llm, with one unavoidable difference: text
    already yielded to the caller cannot be taken back, so fallback is only
    possible before the first chunk.

    1. Open the primary stream and pull its first chunk.
       - Quota/rate-limit error → log WARNING, try the fallback model.
       - Any other error → re-raise immediately (no fallback), like call_llm.
    2. Fallback (only on a primary quota error before any text): open and pull
       its first chunk; any failure → log ERROR, raise LLMProviderError with
       both reasons (same "Both Gemini models failed" message as call_llm).
    3. Yield the rest. A failure AFTER text has started flowing → log ERROR,
       raise LLMProviderError (no fallback — the answer is already partial).
    4. On clean completion, log the same "LLM call served by gemini
       primary/fallback" INFO line call_llm logs, so existing tier checks work.

    This is a generator: nothing runs, and nothing is raised, until the caller
    starts iterating.
    """
    primary_model = settings.gemini_primary_model
    fallback_model = settings.gemini_fallback_model

    try:
        first, stream = _open_stream(prompt, primary_model)
        tier, model_name = "primary", primary_model
    except Exception as exc:
        if not _is_quota_or_rate_limit_error(exc):
            raise
        primary_error = exc
        logger.warning(
            "Gemini primary (%s) quota/rate-limit hit for purpose=%s, "
            "falling back to %s",
            primary_model, purpose, fallback_model,
        )
        try:
            first, stream = _open_stream(prompt, fallback_model)
            tier, model_name = "fallback", fallback_model
        except Exception as fallback_error:
            logger.error(
                "Both Gemini models failed for purpose=%s: "
                "primary_error=%s, fallback_error=%s",
                purpose, primary_error, fallback_error,
            )
            raise LLMProviderError(
                f"Both Gemini models failed for purpose={purpose}: "
                f"primary_error={primary_error}, fallback_error={fallback_error}"
            ) from fallback_error

    yield first
    try:
        for text in stream:
            yield text
    except Exception as exc:
        logger.error(
            "Gemini %s (%s) stream failed mid-response for purpose=%s: %s",
            tier, model_name, purpose, exc,
        )
        raise LLMProviderError(
            f"Gemini {tier} stream failed mid-response for purpose={purpose}: {exc}"
        ) from exc

    if tier == "primary":
        logger.info("LLM call served by gemini primary (%s), purpose=%s", model_name, purpose)
    else:
        logger.info(
            "LLM call served by gemini fallback (%s), purpose=%s (fallback)", model_name, purpose,
        )
