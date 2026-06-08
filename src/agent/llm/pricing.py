"""Static price catalog for LLM / STT / TTS providers Heare uses.

The numbers here are public list prices in **USD per 1M input/output
tokens** for LLM models and **USD per second** for STT — copy them from
the provider's pricing page and update when they change. Prices that
are not in the catalog return ``None`` so the dashboard can render
"?" rather than silently zeroing the bill.

Pure module — no I/O, no async, no pipecat dependency — so it imports
cleanly from tests, the storage layer, and the dashboard widget.
"""

from __future__ import annotations

from typing import Optional

from src.agent.llm.providers import PROVIDERS


# (input_per_1m_tokens_usd, output_per_1m_tokens_usd)
# Built dynamically from PROVIDERS registry pricing entries so adding
# a new model to a provider's ``pricing`` tuple automatically
# populates the cost catalog — no second edit needed.
_LLM_PRICES_USD_PER_1M: dict[str, tuple[float, float]] = {}
for _cfg in PROVIDERS.values():
    for _model_id, _in_price, _out_price in _cfg.pricing:
        _LLM_PRICES_USD_PER_1M[_model_id] = (_in_price, _out_price)

# Google Gemini — public list prices, served via OpenRouter (which
# isn't a standalone ProviderConfig yet).  Source: ai.google.dev/pricing.
# These entries fill gaps until a dedicated OpenRouter config exists.
_GEMINI_FALLBACK: dict[str, tuple[float, float]] = {
    "google/gemini-3.1-flash-lite-preview-20260303": (0.075, 0.30),
    "google/gemini-3.1-flash-lite": (0.075, 0.30),
    "google/gemini-3.1-flash": (0.15, 0.60),
    "google/gemini-3.1-pro": (1.25, 5.00),
    "google/gemini-2.0-flash": (0.10, 0.40),
}
_LLM_PRICES_USD_PER_1M.update(_GEMINI_FALLBACK)


# Provider key → USD per second of input audio. Groq Whisper is sold at
# ``$0.04 / hour`` for whisper-large-v3 — convert to seconds for
# linear scaling. Free-tier credits exist; the catalog is the *list*
# price so the dashboard never under-reports cost.
_STT_PRICES_USD_PER_SECOND: dict[str, float] = {
    "groq-whisper-large-v3": 0.04 / 3600.0,
    "groq-whisper-large-v3-turbo": 0.04 / 3600.0,
    "groq": 0.04 / 3600.0,  # bare-provider fallback when model unknown
}


# TTS providers we use are free at the time of writing. Listed here so
# adding a paid TTS later only changes this dict, not the recorder.
_TTS_PRICES_USD_PER_CHAR: dict[str, float] = {
    "edge_tts": 0.0,
}


def llm_cost(
    *,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
) -> Optional[float]:
    """Return USD cost for a single LLM call, or ``None`` if the model
    isn't in the catalog. Returning ``None`` lets the dashboard
    distinguish "free" (cost 0.0) from "unknown" (cost ?)."""
    if not model:
        return None
    prices = _LLM_PRICES_USD_PER_1M.get(model)
    if prices is None:
        return None
    in_price, out_price = prices
    return (input_tokens / 1_000_000.0) * in_price + (
        output_tokens / 1_000_000.0
    ) * out_price


def stt_cost(*, provider: str | None, audio_seconds: float) -> Optional[float]:
    """Return USD cost for one STT call, or ``None`` if the provider
    isn't catalogued. ``audio_seconds`` is the duration of the input
    clip — pipecat tracks it as part of the STT service metrics."""
    if not provider or audio_seconds <= 0:
        return None
    rate = _STT_PRICES_USD_PER_SECOND.get(provider)
    if rate is None:
        return None
    return audio_seconds * rate


def tts_cost(*, provider: str | None, char_count: int) -> Optional[float]:
    """Return USD cost for one TTS call, or ``None`` if uncatalogued.
    Currently always 0.0 for ``edge_tts``."""
    if not provider or char_count <= 0:
        return None
    rate = _TTS_PRICES_USD_PER_CHAR.get(provider)
    if rate is None:
        return None
    return char_count * rate


def is_known_model(model: str | None) -> bool:
    return bool(model) and model in _LLM_PRICES_USD_PER_1M


__all__ = [
    "llm_cost",
    "stt_cost",
    "tts_cost",
    "is_known_model",
]
