"""
Tests for llm_provider.call_llm_stream — Phase 7, Sub-feature 7.5.

Mirrors test_llm_provider.py's coverage of call_llm (same patching style:
the private per-model helper is patched, model names set via monkeypatch),
plus the streaming-specific rules: fallback only before the first chunk,
mid-stream failure raises without fallback, lazy start, and the same
"served by" log line only on clean completion.

All Gemini calls are mocked — no real API quota is consumed.

Run with:
    cd backend
    python -m pytest tests/test_llm_provider_stream.py -v
"""

import logging
from unittest.mock import MagicMock, patch

import google.api_core.exceptions
import pytest

from app.services.llm_provider import LLMProviderError, _chunk_text, call_llm, call_llm_stream

STREAM = "app.services.llm_provider._stream_gemini"


def _gen(*items):
    """A generator yielding strings, raising any Exception item when reached."""
    def generator():
        for item in items:
            if isinstance(item, Exception):
                raise item
            yield item
    return generator()


def _quota():
    return google.api_core.exceptions.ResourceExhausted("quota exceeded")


@pytest.fixture(autouse=True)
def _models(monkeypatch):
    monkeypatch.setattr("app.services.llm_provider.settings.gemini_primary_model", "gem-primary")
    monkeypatch.setattr("app.services.llm_provider.settings.gemini_fallback_model", "gem-fallback")


def _router(**by_model):
    """side_effect for _stream_gemini: pick a generator factory by model name."""
    def side_effect(prompt, model_name):
        return by_model[model_name]()
    return side_effect


class TestPrimarySuccess:
    def test_yields_every_chunk_in_order_and_never_calls_fallback(self):
        with patch(STREAM, side_effect=_router(**{"gem-primary": lambda: _gen("Hel", "lo", "!")})) as mock:
            assert list(call_llm_stream("p")) == ["Hel", "lo", "!"]
        assert [c.args[1] for c in mock.call_args_list] == ["gem-primary"]

    def test_logs_served_by_primary_only_after_completion(self, caplog):
        caplog.set_level(logging.INFO, logger="app.services.llm_provider")
        with patch(STREAM, side_effect=_router(**{"gem-primary": lambda: _gen("a", "b")})):
            stream = call_llm_stream("p", purpose="student_chat")
            assert next(stream) == "a"
            assert not any("served by" in r.message for r in caplog.records)
            list(stream)
        served = [r for r in caplog.records if "LLM call served by gemini primary" in r.message]
        assert len(served) == 1
        assert "gem-primary" in served[0].message and "purpose=student_chat" in served[0].message

    def test_is_lazy_nothing_called_until_iteration(self):
        with patch(STREAM) as mock:
            call_llm_stream("p")
        mock.assert_not_called()


class TestPrimaryQuotaFallback:
    def test_quota_error_before_first_chunk_falls_back_with_same_prompt(self, caplog):
        caplog.set_level(logging.INFO, logger="app.services.llm_provider")
        side_effect = _router(**{
            "gem-primary": lambda: _gen(_quota()),
            "gem-fallback": lambda: _gen("from", " fallback"),
        })
        with patch(STREAM, side_effect=side_effect) as mock:
            assert list(call_llm_stream("the prompt")) == ["from", " fallback"]
        assert [(c.args[0], c.args[1]) for c in mock.call_args_list] == [
            ("the prompt", "gem-primary"), ("the prompt", "gem-fallback"),
        ]
        assert any(r.levelno == logging.WARNING and "falling back" in r.message for r in caplog.records)
        assert any("LLM call served by gemini fallback" in r.message for r in caplog.records)
        assert not any("LLM call served by gemini primary" in r.message for r in caplog.records)

    def test_quota_detected_by_message_text_also_falls_back(self):
        side_effect = _router(**{
            "gem-primary": lambda: _gen(RuntimeError("429 rate limit")),
            "gem-fallback": lambda: _gen("ok"),
        })
        with patch(STREAM, side_effect=side_effect):
            assert list(call_llm_stream("p")) == ["ok"]


class TestPrimaryNonQuotaError:
    def test_non_quota_error_reraises_original_without_fallback(self):
        boom = ValueError("bad api key")
        with patch(STREAM, side_effect=_router(**{"gem-primary": lambda: _gen(boom)})) as mock:
            with pytest.raises(ValueError) as excinfo:
                list(call_llm_stream("p"))
        assert excinfo.value is boom
        assert [c.args[1] for c in mock.call_args_list] == ["gem-primary"]

    def test_empty_primary_stream_raises_without_fallback(self):
        with patch(STREAM, side_effect=_router(**{"gem-primary": lambda: _gen()})) as mock:
            with pytest.raises(ValueError, match="empty streamed response"):
                list(call_llm_stream("p"))
        assert [c.args[1] for c in mock.call_args_list] == ["gem-primary"]


class TestBothModelsFail:
    def test_raises_llm_provider_error_with_both_reasons(self, caplog):
        caplog.set_level(logging.INFO, logger="app.services.llm_provider")
        side_effect = _router(**{
            "gem-primary": lambda: _gen(_quota()),
            "gem-fallback": lambda: _gen(RuntimeError("fallback down")),
        })
        with patch(STREAM, side_effect=side_effect):
            with pytest.raises(LLMProviderError) as excinfo:
                list(call_llm_stream("p", purpose="student_chat"))
        message = str(excinfo.value)
        assert "Both Gemini models failed for purpose=student_chat" in message
        assert "quota exceeded" in message and "fallback down" in message
        assert any(r.levelno == logging.ERROR for r in caplog.records)


class TestMidStreamFailure:
    def test_failure_after_first_chunk_raises_without_fallback(self, caplog):
        caplog.set_level(logging.INFO, logger="app.services.llm_provider")
        side_effect = _router(**{"gem-primary": lambda: _gen("partial", _quota())})
        received = []
        with patch(STREAM, side_effect=side_effect) as mock:
            with pytest.raises(LLMProviderError, match="failed mid-response"):
                for text in call_llm_stream("p"):
                    received.append(text)
        assert received == ["partial"]
        assert [c.args[1] for c in mock.call_args_list] == ["gem-primary"]  # even a quota error: no fallback
        assert not any("served by" in r.message for r in caplog.records)


class TestModelSelection:
    def test_purpose_does_not_change_the_model(self):
        with patch(STREAM, side_effect=_router(**{"gem-primary": lambda: _gen("x")})) as mock:
            list(call_llm_stream("p", purpose="anything-else"))
        assert mock.call_args.args[1] == "gem-primary"


class TestCallLlmUnaffected:
    def test_call_llm_never_touches_the_streaming_path(self):
        with patch("app.services.llm_provider._call_gemini", return_value="whole") as mock_call, \
                patch(STREAM) as mock_stream:
            assert call_llm("p") == "whole"
        mock_call.assert_called_once()
        mock_stream.assert_not_called()


class TestChunkText:
    def test_chunk_without_text_parts_is_empty_not_an_error(self):
        chunk = MagicMock()
        type(chunk).text = property(lambda self: (_ for _ in ()).throw(ValueError("no parts")))
        assert _chunk_text(chunk) == ""

    def test_normal_chunk_text_returned(self):
        chunk = MagicMock()
        chunk.text = "hello"
        assert _chunk_text(chunk) == "hello"
