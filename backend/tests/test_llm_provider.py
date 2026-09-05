"""
Tests for backend/app/services/llm_provider.py — Phase 2, Sub-feature 6.

All Gemini and Groq API calls are mocked — no real API quota is consumed.

Cases covered
─────────────
1. _is_quota_or_rate_limit_error
   - Returns True for google.api_core.exceptions.ResourceExhausted.
   - Returns True for exceptions whose message contains "quota", "rate limit",
     "429", or "resource exhausted" (case-insensitive).
   - Returns False for generic errors that do not match quota signals.

2. call_llm — Gemini succeeds
   - Returns Gemini result.
   - Groq is never called.
   - Logs INFO with "gemini" in the message.

3. call_llm — Gemini quota/rate-limit error → Groq succeeds
   - Gemini raises a quota-style error.
   - Groq is called and its result is returned.
   - Logs WARNING about Gemini quota/rate-limit.
   - Logs INFO with "groq" and "(fallback)" in the message.

4. call_llm — Gemini non-quota error → re-raises immediately, no Groq call
   - Groq is never called.
   - Original exception is re-raised (not wrapped in LLMProviderError).

5. call_llm — Gemini quota error → Groq fails (any reason) → Ollama succeeds
   - Ollama is called and its result is returned.
   - Logs INFO with "ollama" in the message.

6. call_llm — Gemini, Groq, and Ollama all fail
   - LLMProviderError is raised.
   - All three error descriptions appear in the exception message.

7. call_llm — model selection
   - purpose="fast" selects GEMINI_FAST_MODEL / GROQ_FAST_MODEL.
   - purpose="pro" selects GEMINI_PRO_MODEL / GROQ_PRO_MODEL.

8. Integration: rubric.py end-to-end via mocked call_llm
   - call_gemini_for_rubric returns the response text when call_llm succeeds.
   - call_gemini_for_rubric raises RubricGenerationError when call_llm raises.

9. Integration: evaluator.py end-to-end via mocked call_llm
   - call_gemini_for_evaluation returns the response text when call_llm succeeds.
   - call_gemini_for_evaluation raises EvaluationError when call_llm raises.
"""

import logging
from unittest.mock import MagicMock, call, patch

import google.api_core.exceptions
import pytest

from app.services.llm_provider import (
    LLMProviderError,
    _is_quota_or_rate_limit_error,
    call_llm,
)


# ===========================================================================
# 1. _is_quota_or_rate_limit_error
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
# 2. call_llm — Gemini succeeds
# ===========================================================================

class TestCallLlmGeminiSuccess:
    def test_returns_gemini_result(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        with patch("app.services.llm_provider._call_gemini", return_value="gemini output") as mock_gem, \
             patch("app.services.llm_provider._call_groq") as mock_groq:
            result = call_llm("my prompt", purpose="fast")
        assert result == "gemini output"

    def test_groq_never_called_on_success(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        with patch("app.services.llm_provider._call_gemini", return_value="ok"), \
             patch("app.services.llm_provider._call_groq") as mock_groq:
            call_llm("prompt", purpose="fast")
        mock_groq.assert_not_called()

    def test_logs_info_gemini(self, monkeypatch, caplog):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        with patch("app.services.llm_provider._call_gemini", return_value="result"), \
             patch("app.services.llm_provider._call_groq"):
            with caplog.at_level(logging.INFO, logger="app.services.llm_provider"):
                call_llm("prompt", purpose="fast")
        assert any("gemini" in r.message.lower() for r in caplog.records)
        assert any(r.levelno == logging.INFO for r in caplog.records)


# ===========================================================================
# 3. call_llm — Gemini quota error → Groq succeeds (fallback)
# ===========================================================================

class TestCallLlmGeminiQuotaFallback:
    def test_returns_groq_result_on_quota_error(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_fast_model", "groq-fast")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        with patch("app.services.llm_provider._call_gemini", side_effect=quota_exc), \
             patch("app.services.llm_provider._call_groq", return_value="groq output") as mock_groq:
            result = call_llm("my prompt", purpose="fast")
        assert result == "groq output"
        mock_groq.assert_called_once_with("my prompt", "groq-fast")

    def test_returns_groq_result_on_429_in_message(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_fast_model", "groq-fast")
        with patch("app.services.llm_provider._call_gemini",
                   side_effect=RuntimeError("HTTP 429 Too Many Requests")), \
             patch("app.services.llm_provider._call_groq", return_value="groq ok"):
            result = call_llm("prompt", purpose="fast")
        assert result == "groq ok"

    def test_logs_warning_on_quota_hit(self, monkeypatch, caplog):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_fast_model", "groq-fast")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        with patch("app.services.llm_provider._call_gemini", side_effect=quota_exc), \
             patch("app.services.llm_provider._call_groq", return_value="ok"):
            with caplog.at_level(logging.WARNING, logger="app.services.llm_provider"):
                call_llm("prompt", purpose="fast")
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("quota" in m.lower() or "rate" in m.lower() for m in warning_messages)

    def test_logs_info_groq_fallback(self, monkeypatch, caplog):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_fast_model", "groq-fast")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        with patch("app.services.llm_provider._call_gemini", side_effect=quota_exc), \
             patch("app.services.llm_provider._call_groq", return_value="ok"):
            with caplog.at_level(logging.INFO, logger="app.services.llm_provider"):
                call_llm("prompt", purpose="fast")
        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("groq" in m.lower() and "fallback" in m.lower() for m in info_messages)


# ===========================================================================
# 4. call_llm — Gemini non-quota error → re-raises immediately, no Groq call
# ===========================================================================

class TestCallLlmGeminiNonQuotaError:
    def test_reraises_non_quota_error(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        non_quota_exc = ValueError("Invalid argument: bad prompt format")
        with patch("app.services.llm_provider._call_gemini", side_effect=non_quota_exc), \
             patch("app.services.llm_provider._call_groq") as mock_groq:
            with pytest.raises(ValueError, match="Invalid argument"):
                call_llm("prompt", purpose="fast")
        mock_groq.assert_not_called()

    def test_does_not_wrap_in_llm_provider_error(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        non_quota_exc = PermissionError("API key not valid")
        with patch("app.services.llm_provider._call_gemini", side_effect=non_quota_exc), \
             patch("app.services.llm_provider._call_groq"):
            with pytest.raises(PermissionError):
                call_llm("prompt", purpose="fast")
            # Confirm it is NOT an LLMProviderError
            try:
                call_llm("prompt", purpose="fast")
            except LLMProviderError:
                pytest.fail("Should not raise LLMProviderError for non-quota Gemini error")
            except PermissionError:
                pass  # expected


# ===========================================================================
# 5. call_llm — Gemini quota error, Groq fails (any reason) → Ollama succeeds
# ===========================================================================

class TestCallLlmGroqFailsOllamaFallback:
    def test_returns_ollama_result_on_groq_failure(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_fast_model", "groq-fast")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        groq_exc = RuntimeError("groq service down")  # non-quota Groq failure — still falls through
        with patch("app.services.llm_provider._call_gemini", side_effect=quota_exc), \
             patch("app.services.llm_provider._call_groq", side_effect=groq_exc), \
             patch("app.services.llm_provider._call_ollama", return_value="ollama output") as mock_ollama:
            result = call_llm("my prompt", purpose="fast")
        assert result == "ollama output"
        mock_ollama.assert_called_once_with("my prompt")

    def test_logs_info_ollama_fallback(self, monkeypatch, caplog):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_fast_model", "groq-fast")
        monkeypatch.setattr("app.services.llm_provider.settings.ollama_model", "llama3.1:8b")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        groq_exc = RuntimeError("groq down")
        with patch("app.services.llm_provider._call_gemini", side_effect=quota_exc), \
             patch("app.services.llm_provider._call_groq", side_effect=groq_exc), \
             patch("app.services.llm_provider._call_ollama", return_value="ok"):
            with caplog.at_level(logging.INFO, logger="app.services.llm_provider"):
                call_llm("prompt", purpose="fast")
        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("ollama" in m.lower() and "fallback" in m.lower() for m in info_messages)

    def test_logs_warning_on_groq_failure(self, monkeypatch, caplog):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_fast_model", "groq-fast")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        groq_exc = RuntimeError("groq down")
        with patch("app.services.llm_provider._call_gemini", side_effect=quota_exc), \
             patch("app.services.llm_provider._call_groq", side_effect=groq_exc), \
             patch("app.services.llm_provider._call_ollama", return_value="ok"):
            with caplog.at_level(logging.WARNING, logger="app.services.llm_provider"):
                call_llm("prompt", purpose="fast")
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("ollama" in m.lower() for m in warning_messages)


# ===========================================================================
# 6. call_llm — Gemini, Groq, and Ollama all fail
# ===========================================================================

class TestCallLlmAllProvidersFail:
    def test_raises_llm_provider_error(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_fast_model", "groq-fast")
        quota_exc = google.api_core.exceptions.ResourceExhausted("gemini quota")
        groq_exc = RuntimeError("groq service down")
        ollama_exc = LLMProviderError("Ollama call failed: connection refused")
        with patch("app.services.llm_provider._call_gemini", side_effect=quota_exc), \
             patch("app.services.llm_provider._call_groq", side_effect=groq_exc), \
             patch("app.services.llm_provider._call_ollama", side_effect=ollama_exc):
            with pytest.raises(LLMProviderError) as exc_info:
                call_llm("prompt", purpose="fast")
        message = str(exc_info.value)
        assert "gemini quota" in message
        assert "groq service down" in message
        assert "connection refused" in message

    def test_error_message_contains_all_three_failure_reasons(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_fast_model", "groq-fast")
        quota_exc = RuntimeError("rate limit exceeded on gemini")
        groq_exc = ConnectionError("groq timed out")
        ollama_exc = LLMProviderError("Ollama call failed: connection refused")
        with patch("app.services.llm_provider._call_gemini", side_effect=quota_exc), \
             patch("app.services.llm_provider._call_groq", side_effect=groq_exc), \
             patch("app.services.llm_provider._call_ollama", side_effect=ollama_exc):
            with pytest.raises(LLMProviderError) as exc_info:
                call_llm("prompt", purpose="fast")
        msg = str(exc_info.value)
        assert "rate limit exceeded on gemini" in msg
        assert "groq timed out" in msg
        assert "connection refused" in msg
        assert "purpose=fast" in msg

    def test_logs_error_on_all_three_fail(self, monkeypatch, caplog):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-fast")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_fast_model", "groq-fast")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        groq_exc = RuntimeError("groq down")
        ollama_exc = LLMProviderError("Ollama call failed: connection refused")
        with patch("app.services.llm_provider._call_gemini", side_effect=quota_exc), \
             patch("app.services.llm_provider._call_groq", side_effect=groq_exc), \
             patch("app.services.llm_provider._call_ollama", side_effect=ollama_exc):
            with caplog.at_level(logging.ERROR, logger="app.services.llm_provider"):
                with pytest.raises(LLMProviderError):
                    call_llm("prompt", purpose="fast")
        assert any(r.levelno == logging.ERROR for r in caplog.records)


# ===========================================================================
# 7. call_llm — model selection (fast vs pro)
# ===========================================================================

class TestCallLlmModelSelection:
    def test_fast_purpose_uses_fast_models(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_fast_model", "gemini-flash-latest")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_fast_model", "llama-3.1-8b-instant")
        with patch("app.services.llm_provider._call_gemini", return_value="ok") as mock_gem:
            call_llm("prompt", purpose="fast")
        mock_gem.assert_called_once_with("prompt", "gemini-flash-latest")

    def test_pro_purpose_uses_pro_models(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_pro_model", "gemini-3.1-pro-preview")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_pro_model", "llama-3.3-70b-versatile")
        with patch("app.services.llm_provider._call_gemini", return_value="ok") as mock_gem:
            call_llm("prompt", purpose="pro")
        mock_gem.assert_called_once_with("prompt", "gemini-3.1-pro-preview")

    def test_pro_fallback_uses_pro_groq_model(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_provider.settings.gemini_pro_model", "gemini-3.1-pro-preview")
        monkeypatch.setattr("app.services.llm_provider.settings.groq_pro_model", "llama-3.3-70b-versatile")
        quota_exc = google.api_core.exceptions.ResourceExhausted("quota")
        with patch("app.services.llm_provider._call_gemini", side_effect=quota_exc), \
             patch("app.services.llm_provider._call_groq", return_value="groq pro result") as mock_groq:
            result = call_llm("prompt", purpose="pro")
        assert result == "groq pro result"
        mock_groq.assert_called_once_with("prompt", "llama-3.3-70b-versatile")


# ===========================================================================
# 8. Integration — rubric.py end-to-end through llm_provider
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

    def test_call_gemini_for_rubric_wraps_both_providers_error(self):
        from app.services.rubric import RubricGenerationError, call_gemini_for_rubric
        with patch(
            "app.services.llm_provider.call_llm",
            side_effect=LLMProviderError("Both Gemini and Groq failed for purpose=fast: ..."),
        ):
            with pytest.raises(RubricGenerationError, match="LLM call failed"):
                call_gemini_for_rubric("rubric prompt")


# ===========================================================================
# 9. Integration — evaluator.py end-to-end through llm_provider
# ===========================================================================

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

    def test_call_gemini_for_evaluation_wraps_both_providers_error(self):
        from app.services.evaluator import EvaluationError, call_gemini_for_evaluation
        with patch(
            "app.services.llm_provider.call_llm",
            side_effect=LLMProviderError("Both Gemini and Groq failed for purpose=fast: ..."),
        ):
            with pytest.raises(EvaluationError, match="LLM call failed"):
                call_gemini_for_evaluation("eval prompt")
