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

  LLMProviderError
      Raised only when BOTH Gemini models fail for the same prompt.

Internal helpers (not part of public API)
-----------------------------------------
  _is_quota_or_rate_limit_error(exc) -> bool
  _call_gemini(prompt, model_name) -> str
"""

import logging

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
