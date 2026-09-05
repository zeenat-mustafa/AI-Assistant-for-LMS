"""
LLM provider abstraction layer — Phase 2, Sub-feature 6.

Centralises every LLM call behind a single public entry point: call_llm().
Primary provider is Google Gemini; if a quota or rate-limit error is detected,
the call is automatically retried against Groq. If Groq fails for ANY reason,
the call falls through to a locally-running Ollama model before raising to
the caller.

Public API
----------
  call_llm(prompt, purpose) -> str
      purpose is "fast" or "pro", selecting the appropriate model tier from
      settings on both providers.

  LLMProviderError
      Raised only when Gemini, Groq, AND Ollama all fail for the same prompt.

Internal helpers (not part of public API)
-----------------------------------------
  _is_quota_or_rate_limit_error(exc) -> bool
  _call_gemini(prompt, model_name) -> str
  _call_groq(prompt, model_name) -> str
  _call_ollama(prompt) -> str
"""

import logging
from typing import Literal

import google.generativeai as genai
import google.api_core.exceptions
import httpx
from groq import Groq

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class LLMProviderError(Exception):
    """Raised when Gemini, Groq, and Ollama all fail for the same prompt."""
    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_quota_or_rate_limit_error(exc: Exception) -> bool:
    """
    Return True if *exc* represents a quota or rate-limit condition that
    warrants a provider fallback.

    Triggers on:
      - google.api_core.exceptions.ResourceExhausted (canonical Gemini quota type)
      - Any exception whose str() contains "quota", "rate limit", "429", or
        "resource exhausted" (case-insensitive) — catches Groq HTTP 429s and
        any provider that surfaces quota text in a generic exception message.

    Returns False for all other errors (bad key, malformed request, etc.) —
    those will fail the same way on the fallback provider, so retrying there
    is pointless.
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


def _call_groq(prompt: str, model_name: str) -> str:
    """
    Call Groq with *model_name* and return the response text.

    Uses the groq SDK's synchronous client, authenticating from
    settings.groq_api_key. Raises whatever exception the SDK raises.
    """
    client = Groq(api_key=settings.groq_api_key)
    completion = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
    )
    text = completion.choices[0].message.content
    if not text:
        raise ValueError("Groq returned an empty response.")
    return text


def _call_ollama(prompt: str) -> str:
    """
    Call a locally-running Ollama server and return the response text.

    No quota/rate-limit detection needed — local model has no quota.
    Any failure raises LLMProviderError prefixed "Ollama call failed: ".
    """
    url = f"{settings.ollama_base_url}/api/generate"
    payload = {"model": settings.ollama_model, "prompt": prompt, "stream": False}
    try:
        response = httpx.post(url, json=payload, timeout=60.0)
        response.raise_for_status()
        data = response.json()
        text = data.get("response", "")
        if not text:
            raise ValueError("Ollama returned an empty 'response' field.")
        return text
    except Exception as exc:
        raise LLMProviderError(f"Ollama call failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def call_llm(prompt: str, purpose: Literal["fast", "pro"]) -> str:
    """
    Send *prompt* to the configured LLM provider and return the response text.

    Parameters
    ----------
    prompt:
        The full prompt string to send (callers own prompt construction).
    purpose:
        ``"fast"`` → uses GEMINI_FAST_MODEL / GROQ_FAST_MODEL.
        ``"pro"``  → uses GEMINI_PRO_MODEL  / GROQ_PRO_MODEL.

    Fallback logic
    --------------
    1. Try Gemini with the appropriate model.
       - Success → log INFO, return result.
       - Quota/rate-limit error → log WARNING, fall through to Groq.
       - Any other error (bad key, malformed request, …) → re-raise immediately;
         no fallback, because the same error would occur on Groq too.
    2. Try Groq with the appropriate model.
       - Success → log INFO, return result.
       - Any failure (quota or otherwise) → log WARNING, fall through to Ollama.
    3. Try Ollama (local model, no quota concept).
       - Success → log INFO, return result.
       - Any failure → log ERROR with all three failure reasons, raise LLMProviderError.

    Raises
    ------
    LLMProviderError
        Only when Gemini, Groq, and Ollama all fail.
    Any other exception
        Re-raised directly when Gemini fails with a non-quota error.
    """
    if purpose == "fast":
        gemini_model = settings.gemini_fast_model
        groq_model = settings.groq_fast_model
    else:  # "pro"
        gemini_model = settings.gemini_pro_model
        groq_model = settings.groq_pro_model

    # ── Step 1: try Gemini ────────────────────────────────────────────────────
    gemini_error: Exception | None = None
    try:
        result = _call_gemini(prompt, gemini_model)
        logger.info("LLM call served by gemini (%s), purpose=%s", gemini_model, purpose)
        return result
    except Exception as exc:
        if not _is_quota_or_rate_limit_error(exc):
            # Non-quota failure — re-raise immediately, do not try Groq.
            raise
        gemini_error = exc
        logger.warning(
            "Gemini quota/rate-limit hit for purpose=%s, falling back to Groq",
            purpose,
        )

    # ── Step 2: try Groq (only reached on Gemini quota/rate-limit) ───────────
    groq_error: Exception | None = None
    try:
        result = _call_groq(prompt, groq_model)
        logger.info(
            "LLM call served by groq (%s), purpose=%s (fallback)", groq_model, purpose
        )
        return result
    except Exception as exc:
        groq_error = exc
        logger.warning(
            "Groq failed for purpose=%s (%s), falling back to Ollama",
            purpose, groq_error,
        )

    # ── Step 3: try Ollama (only reached on Groq failure) ────────────────────
    try:
        result = _call_ollama(prompt)
        logger.info(
            "LLM call served by ollama (%s), purpose=%s (fallback)",
            settings.ollama_model, purpose,
        )
        return result
    except Exception as ollama_exc:
        logger.error(
            "Gemini, Groq, and Ollama all failed for purpose=%s: "
            "gemini_error=%s, groq_error=%s, ollama_error=%s",
            purpose, gemini_error, groq_error, ollama_exc,
        )
        raise LLMProviderError(
            f"Gemini, Groq, and Ollama all failed for purpose={purpose}: "
            f"gemini_error={gemini_error}, groq_error={groq_error}, ollama_error={ollama_exc}"
        ) from ollama_exc
