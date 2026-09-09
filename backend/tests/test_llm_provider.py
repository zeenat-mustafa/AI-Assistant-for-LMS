"""
Tests for backend/app/services/llm_provider.py — Phase 2, Sub-feature 6,
updated for the two-tier Gemini-only provider chain (Groq/Ollama removed).

All Gemini calls are mocked — no real API quota is consumed.

Cases covered
─────────────
1. _is_quota_or_rate_limit_error
   - True for google.api_core.exceptions.ResourceExhausted.
   - True for messages containing "quota", "rate limit", "429", or
     "resource exhausted" (case-insensitive).
   - False for generic errors (bad key, malformed request, …).

2. call_llm — primary succeeds
   - Returns the primary result; the fallback is never called.
   - Logs INFO naming the primary.

3. call_llm — primary quota/rate-limit error → fallback succeeds
   - The fallback model is called with the same prompt and its result returned.
   - Logs WARNING about the quota hit and INFO about the fallback.

4. call_llm — primary non-quota error → re-raises immediately, no fallback call
   - The fallback model is never called; the original exception propagates
     (not wrapped in LLMProviderError).

5. call_llm — both models fail
   - LLMProviderError is raised with BOTH failure reasons in the message.

6. call_llm — model selection
   - The primary is called with settings.gemini_primary_model; on a quota
     error the fallback is called with settings.gemini_fallback_model.
   - purpose does NOT affect which model is used.

7. Integration: rubric.py / evaluator.py end-to-end via mocked call_llm.
"""

import logging
from unittest.mock import patch

import google.api_core.exceptions
import pytest

from app.services.llm_provider import (
    LLMProviderError,
    _is_quota_or_rate_limit_error,
    call_llm,
)


# ===========================================================================
# 1. _is_quota_or_rate_limit_error  (unchanged detection logic)
# ===========================================================================

class TestIsQuotaOrRateLimitError:
    def test_resource_exhausted_exception(self):
        exc = google.api_core.exceptions.ResourceExhausted("quota exceeded")
        assert _is_quota_or_rate_limit_error(exc) is True

    def test_message_contains_quota(self):
        assert _is_quota_or_rate_limit_error(RuntimeError("Quota limit reached")) is True

    def test_message_contains_quota_uppercase(self):
        assert _is_quota_or_rate_limit_error(RuntimeError("QUOTA EXCEEDED")) is True

    def test_message_contains_rate_limit(self):
        assert _is_quota_or_rate_limit_error(RuntimeError("rate limit hit")) is True

    def test_message_contains_429(self):
        assert _is_quota_or_rate_limit_error(RuntimeError("HTTP 429 Too Many Requests")) is True

    def test_message_contains_resource_exhausted(self):
        assert _is_quota_or_rate_limit_error(RuntimeError("resource exhausted")) is True

    def test_message_contains_resource_exhausted_mixed_case(self):
        assert _is_quota_or_rate_limit_error(RuntimeError("Resource Exhausted")) is True

    def test_generic_runtime_error(self):
        assert _is_quota_or_rate_limit_error(RuntimeError("Invalid argument")) is False

    def test_value_error(self):
        assert _is_quota_or_rate_limit_error(ValueError("bad input")) is False

    def test_connection_error(self):
        assert _is_quota_or_rate_limit_error(ConnectionError("timeout")) is False

    def test_invalid_api_key_error(self):
        # A bad API key is NOT a quota error — should not trigger fallback.
        assert _is_quota_or_rate_limit_error(
            PermissionError("API key not valid. Please pass a valid API key.")
        ) is False


# ===========================================================================
# 2. call_llm — primary succeeds
# ===========================================================================

class TestCallLlmPrimarySuccess:
    def test_returns_primary_result(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")
        with patch("app.services.llm_provider._call_gemini", return_value="primary output") as mock_gem:
            result = call_llm("my prompt", purpose="fast")
        assert result == "primary output"
        # Exactly one Gemini call — the primary — and no fallback attempt.
        mock_gem.assert_called_once_with("my prompt", "gem-primary")

    def test_fallback_never_called_on_success(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")
        with patch("app.services.llm_provider._call_gemini", return_value="ok") as mock_gem:
            call_llm("prompt", purpose="fast")
        # Only one call total means the fallback model was never reached.
        assert mock_gem.call_count == 1

    def test_logs_info_primary(self, monkeypatch, caplog):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        with patch("app.services.llm_provider._call_gemini", return_value="result"):
            with caplog.at_level(logging.INFO, logger="app.services.llm_provider"):
                call_llm("prompt", purpose="fast")
        assert any("primary" in r.message.lower() for r in caplog.records)
        assert any(r.levelno == logging.INFO for r in caplog.records)


# ===========================================================================
# 3. call_llm — primary quota/rate-limit error → fallback succeeds
# ===========================================================================

class TestCallLlmPrimaryQuotaFallback:
    def test_returns_fallback_result_on_quota_error(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        # First call (primary) raises quota; second call (fallback) returns.
        with patch(
            "app.services.llm_provider._call_gemini",
            side_effect=[quota_exc, "fallback output"],
        ) as mock_gem:
            result = call_llm("my prompt", purpose="fast")
        assert result == "fallback output"
        assert mock_gem.call_count == 2
        # Second call must target the fallback model with the same prompt.
        assert mock_gem.call_args_list[1].args == ("my prompt", "gem-fallback")

    def test_returns_fallback_result_on_429_in_message(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")
        with patch(
            "app.services.llm_provider._call_gemini",
            side_effect=[RuntimeError("HTTP 429 Too Many Requests"), "fallback ok"],
        ):
            result = call_llm("prompt", purpose="fast")
        assert result == "fallback ok"

    def test_logs_warning_on_quota_hit(self, monkeypatch, caplog):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        with patch(
            "app.services.llm_provider._call_gemini",
            side_effect=[quota_exc, "ok"],
        ):
            with caplog.at_level(logging.WARNING, logger="app.services.llm_provider"):
                call_llm("prompt", purpose="fast")
        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("quota" in m.lower() or "rate" in m.lower() for m in warnings)

    def test_logs_info_fallback(self, monkeypatch, caplog):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        with patch(
            "app.services.llm_provider._call_gemini",
            side_effect=[quota_exc, "ok"],
        ):
            with caplog.at_level(logging.INFO, logger="app.services.llm_provider"):
                call_llm("prompt", purpose="fast")
        infos = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("fallback" in m.lower() for m in infos)


# ===========================================================================
# 4. call_llm — primary non-quota error → re-raises immediately, no fallback
# ===========================================================================

class TestCallLlmPrimaryNonQuotaError:
    def test_reraises_non_quota_error(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")
        non_quota_exc = ValueError("Invalid argument: bad prompt format")
        with patch(
            "app.services.llm_provider._call_gemini",
            side_effect=non_quota_exc,
        ) as mock_gem:
            with pytest.raises(ValueError, match="Invalid argument"):
                call_llm("prompt", purpose="fast")
        # No fallback attempt — exactly one Gemini call was made.
        assert mock_gem.call_count == 1

    def test_does_not_wrap_in_llm_provider_error(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")
        non_quota_exc = PermissionError("API key not valid")
        with patch("app.services.llm_provider._call_gemini", side_effect=non_quota_exc):
            with pytest.raises(PermissionError):
                call_llm("prompt", purpose="fast")
            # Confirm it is NOT swallowed into LLMProviderError.
            try:
                call_llm("prompt", purpose="fast")
            except LLMProviderError:
                pytest.fail("Should not raise LLMProviderError for a non-quota primary error")
            except PermissionError:
                pass  # expected


# ===========================================================================
# 5. call_llm — both Gemini models fail
# ===========================================================================

class TestCallLlmBothModelsFail:
    def test_raises_llm_provider_error_with_both_reasons(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")
        primary_exc = google.api_core.exceptions.ResourceExhausted("primary quota exhausted")
        fallback_exc = RuntimeError("fallback model unavailable")
        with patch(
            "app.services.llm_provider._call_gemini",
            side_effect=[primary_exc, fallback_exc],
        ):
            with pytest.raises(LLMProviderError) as exc_info:
                call_llm("prompt", purpose="fast")
        message = str(exc_info.value)
        assert "primary quota exhausted" in message
        assert "fallback model unavailable" in message
        assert "purpose=fast" in message

    def test_message_uses_the_pipeline_signature(self, monkeypatch):
        # grading_pipeline keys its user-facing sanitisation on this substring;
        # keep them in lockstep.
        from app.services.grading_pipeline import _ALL_PROVIDERS_FAILED_SIGNATURE

        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")
        with patch(
            "app.services.llm_provider._call_gemini",
            side_effect=[RuntimeError("rate limit exceeded"), ConnectionError("down")],
        ):
            with pytest.raises(LLMProviderError) as exc_info:
                call_llm("prompt", purpose="fast")
        assert _ALL_PROVIDERS_FAILED_SIGNATURE in str(exc_info.value)

    def test_logs_error_when_both_fail(self, monkeypatch, caplog):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")
        with patch(
            "app.services.llm_provider._call_gemini",
            side_effect=[google.api_core.exceptions.ResourceExhausted("quota"), RuntimeError("down")],
        ):
            with caplog.at_level(logging.ERROR, logger="app.services.llm_provider"):
                with pytest.raises(LLMProviderError):
                    call_llm("prompt", purpose="fast")
        assert any(r.levelno == logging.ERROR for r in caplog.records)


# ===========================================================================
# 6. call_llm — model selection (purpose no longer changes the model)
# ===========================================================================

class TestCallLlmModelSelection:
    def test_primary_model_comes_from_settings(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gemini-3.5-flash-lite")
        with patch("app.services.llm_provider._call_gemini", return_value="ok") as mock_gem:
            call_llm("prompt", purpose="fast")
        mock_gem.assert_called_once_with("prompt", "gemini-3.5-flash-lite")

    def test_fallback_model_comes_from_settings(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gemini-3.5-flash-lite")
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gemini-3.1-flash-lite")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        with patch(
            "app.services.llm_provider._call_gemini",
            side_effect=[quota_exc, "fallback result"],
        ) as mock_gem:
            result = call_llm("prompt", purpose="fast")
        assert result == "fallback result"
        assert mock_gem.call_args_list[1].args == ("prompt", "gemini-3.1-flash-lite")

    def test_purpose_does_not_change_the_model(self, monkeypatch):
        # Whatever the purpose, the same primary model is used.
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "the-one-model")
        for purpose in ("fast", "pro", "anything-else"):
            with patch("app.services.llm_provider._call_gemini", return_value="ok") as mock_gem:
                call_llm("prompt", purpose=purpose)
            mock_gem.assert_called_once_with("prompt", "the-one-model")

    def test_purpose_defaults_to_fast(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "the-one-model")
        with patch("app.services.llm_provider._call_gemini", return_value="ok") as mock_gem:
            # Called without an explicit purpose — must still work.
            result = call_llm("prompt")
        assert result == "ok"
        mock_gem.assert_called_once_with("prompt", "the-one-model")


# ===========================================================================
# 7. Integration — rubric.py / evaluator.py through llm_provider
# ===========================================================================

class TestRubricIntegration:
    def test_call_gemini_for_rubric_returns_response(self):
        from app.services.rubric import call_gemini_for_rubric
        with patch("app.services.llm_provider.call_llm", return_value='{"criteria": []}') as mock_llm:
            result = call_gemini_for_rubric("rubric prompt")
        assert result == '{"criteria": []}'
        mock_llm.assert_called_once_with("rubric prompt", purpose="fast")

    def test_call_gemini_for_rubric_wraps_llm_error(self):
        from app.services.rubric import RubricGenerationError, call_gemini_for_rubric
        with patch(
            "app.services.llm_provider.call_llm",
            side_effect=RuntimeError("network error"),
        ):
            with pytest.raises(RubricGenerationError, match="LLM call failed"):
                call_gemini_for_rubric("rubric prompt")

    def test_call_gemini_for_rubric_wraps_both_models_error(self):
        from app.services.rubric import RubricGenerationError, call_gemini_for_rubric
        with patch(
            "app.services.llm_provider.call_llm",
            side_effect=LLMProviderError(
                "Both Gemini models failed for purpose=fast: "
                "primary_error=quota, fallback_error=down"
            ),
        ):
            with pytest.raises(RubricGenerationError, match="LLM call failed"):
                call_gemini_for_rubric("rubric prompt")


class TestEvaluatorIntegration:
    def test_call_gemini_for_evaluation_returns_response(self):
        from app.services.evaluator import call_gemini_for_evaluation
        with patch("app.services.llm_provider.call_llm", return_value='{"score": 8}') as mock_llm:
            result = call_gemini_for_evaluation("eval prompt")
        assert result == '{"score": 8}'
        mock_llm.assert_called_once_with("eval prompt", purpose="fast")

    def test_call_gemini_for_evaluation_wraps_llm_error(self):
        from app.services.evaluator import EvaluationError, call_gemini_for_evaluation
        with patch(
            "app.services.llm_provider.call_llm",
            side_effect=RuntimeError("connection refused"),
        ):
            with pytest.raises(EvaluationError, match="LLM call failed"):
                call_gemini_for_evaluation("eval prompt")

    def test_call_gemini_for_evaluation_wraps_both_models_error(self):
        from app.services.evaluator import EvaluationError, call_gemini_for_evaluation
        with patch(
            "app.services.llm_provider.call_llm",
            side_effect=LLMProviderError(
                "Both Gemini models failed for purpose=fast: "
                "primary_error=quota, fallback_error=down"
            ),
        ):
            with pytest.raises(EvaluationError, match="LLM call failed"):
                call_gemini_for_evaluation("eval prompt")
