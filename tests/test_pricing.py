"""Tests for src.agent.llm.pricing — static catalog + cost calculators.

The catalog is the single source of truth for cost calculation, so
these tests focus on:
  * known model → priced correctly
  * unknown model → returns None (not 0.0, so callers can show '?')
  * boundary inputs (zero tokens, empty model name)
"""
from __future__ import annotations

from src.agent.llm.pricing import is_known_model, llm_cost, stt_cost, tts_cost


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


class TestTheModelsThisAssistantActuallyRuns:
    """The catalog was built from ``PROVIDERS[*].pricing`` and every
    provider the daemon could reach shipped ``pricing=()``. So the one
    model in daily use was the one model with no price, ``llm_cost``
    returned None for it, and the recorder stored that as 0.0.

    1077 calls read $0.0000 while the balance fell to minus one cent.
    These assertions fail against that catalog.
    """

    def test_every_provider_prices_its_default_model_or_says_it_cannot(
        self,
    ) -> None:
        """The gap must be declared, never inherited from a blank field.

        ``pricing=()`` on DeepSeek looked like an ordinary empty tuple
        and silently zeroed a month of bills. Now a provider either
        prices the model it will actually be asked for, or its name
        appears in PRICES_UNKNOWN — where a human had to type it.
        """
        from src.agent.llm.providers import PRICES_UNKNOWN, PROVIDERS

        undeclared = [
            f"{key}/{cfg.default_model}"
            for key, cfg in PROVIDERS.items()
            if not is_known_model(cfg.default_model) and key not in PRICES_UNKNOWN
        ]
        assert undeclared == [], (
            "these providers will record every call as unpriced without "
            f"anyone having decided that: {undeclared}"
        )

    def test_a_declared_gap_still_has_to_be_a_real_provider(self) -> None:
        """PRICES_UNKNOWN must not outlive the provider it excuses, or it
        becomes a blanket that quietly covers a future name."""
        from src.agent.llm.providers import PRICES_UNKNOWN, PROVIDERS

        assert PRICES_UNKNOWN <= set(PROVIDERS)

    def test_deepseek_v4_flash_costs_real_money(self) -> None:
        # $0.14 / 1M in (cache miss), $0.28 / 1M out.
        cost = llm_cost(
            model="deepseek-v4-flash",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        assert cost == 0.14 + 0.28

    def test_the_retired_model_stays_priced_for_the_rows_it_left(self) -> None:
        """deepseek-chat was retired 24 July 2026 and is no longer the
        default, but 907 rows in usage_events still name it. Dropping it
        from the catalog would re-zero exactly the history this fix is
        meant to make readable."""
        assert is_known_model("deepseek-chat")

    def test_a_days_real_traffic_is_not_free(self) -> None:
        """The shape of one recorded day: ~9.6M input tokens against
        deepseek-chat. Whatever the catalog says, it must not say zero."""
        cost = llm_cost(
            model="deepseek-chat",
            input_tokens=9_588_174,
            output_tokens=61_368,
        )
        assert cost is not None and cost > 1.0


class TestEdgeTTSKeySpelling:
    """``SpineUsage.tts`` passes provider='edge'; the table said
    'edge_tts'. Every lookup missed and returned None. Both branches
    produced 0.0, so nothing showed — until the day speech costs money.
    """

    def test_the_name_the_recorder_actually_passes_is_priced(self) -> None:
        assert tts_cost(provider="edge", char_count=1000) == 0.0

    def test_the_recorders_default_matches_the_catalog(self) -> None:
        import inspect

        from src.spine.usage import SpineUsage

        default = inspect.signature(SpineUsage.tts).parameters["provider"].default
        assert tts_cost(provider=default, char_count=100) is not None, (
            f"SpineUsage.tts defaults to provider={default!r}, which the "
            "TTS price table does not know"
        )
