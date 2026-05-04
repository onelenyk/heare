"""Tests for src.pricing — static catalog + cost calculators.

The catalog is the single source of truth for cost calculation, so
these tests focus on:
  * known model → priced correctly
  * unknown model → returns None (not 0.0, so callers can show '?')
  * boundary inputs (zero tokens, empty model name)
"""
from __future__ import annotations

from src.pricing import is_known_model, llm_cost, stt_cost, tts_cost


class TestLLMCost:
    def test_gemini_flash_lite_known_pricing(self) -> None:
        # 0.075/1M input + 0.30/1M output
        cost = llm_cost(
            model="google/gemini-3.1-flash-lite-preview-20260303",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        assert cost == 0.075 + 0.30

    def test_partial_million_scales_linearly(self) -> None:
        cost = llm_cost(
            model="google/gemini-3.1-flash-lite",
            input_tokens=10_000,
            output_tokens=5_000,
        )
        # (10k/1M)*0.075 + (5k/1M)*0.30 = 0.00075 + 0.0015 = 0.00225
        assert abs(cost - 0.00225) < 1e-9

    def test_unknown_model_returns_none(self) -> None:
        """Unknown model must return None — callers render '?' so a
        missing catalog entry doesn't silently show $0.00."""
        assert llm_cost(model="some/unknown-model-9000", input_tokens=1000, output_tokens=500) is None

    def test_empty_model_returns_none(self) -> None:
        assert llm_cost(model="", input_tokens=1000, output_tokens=500) is None
        assert llm_cost(model=None, input_tokens=1000, output_tokens=500) is None

    def test_zero_tokens_returns_zero_for_known_model(self) -> None:
        assert llm_cost(
            model="google/gemini-3.1-flash-lite",
            input_tokens=0,
            output_tokens=0,
        ) == 0.0

    def test_input_only_call_priced(self) -> None:
        """Streamed responses can complete with prompt_tokens > 0 and
        completion_tokens == 0 (e.g. cancelled mid-flight)."""
        cost = llm_cost(
            model="google/gemini-3.1-flash-lite",
            input_tokens=2_000_000,
            output_tokens=0,
        )
        assert abs(cost - 0.15) < 1e-9


class TestSTTCost:
    def test_groq_whisper_per_hour(self) -> None:
        # $0.04 / hour = $0.04 / 3600s
        cost = stt_cost(provider="groq-whisper-large-v3", audio_seconds=3600)
        assert abs(cost - 0.04) < 1e-9

    def test_groq_provider_alias_works(self) -> None:
        """Bare ``groq`` provider must price like the catalogued model
        — the recorder may not always know the exact whisper variant."""
        a = stt_cost(provider="groq", audio_seconds=60)
        b = stt_cost(provider="groq-whisper-large-v3", audio_seconds=60)
        assert a == b

    def test_zero_audio_returns_none(self) -> None:
        # No audio → no cost; treat as None so the dashboard skips the row.
        assert stt_cost(provider="groq", audio_seconds=0) is None

    def test_unknown_provider_returns_none(self) -> None:
        assert stt_cost(provider="some-other-stt", audio_seconds=60) is None


class TestTTSCost:
    def test_edge_tts_is_free(self) -> None:
        assert tts_cost(provider="edge_tts", char_count=10_000) == 0.0

    def test_unknown_tts_provider_returns_none(self) -> None:
        assert tts_cost(provider="some-other-tts", char_count=100) is None

    def test_zero_chars_returns_none(self) -> None:
        assert tts_cost(provider="edge_tts", char_count=0) is None


class TestIsKnownModel:
    def test_known_returns_true(self) -> None:
        assert is_known_model("google/gemini-3.1-flash-lite") is True

    def test_unknown_returns_false(self) -> None:
        assert is_known_model("not-a-model") is False

    def test_empty_returns_false(self) -> None:
        assert is_known_model("") is False
        assert is_known_model(None) is False
