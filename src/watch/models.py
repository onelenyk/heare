"""Model whitelist + persisted selection for the watch dashboard.

The dashboard lets the operator pick which LLM model powers the agent. Each
provider (openrouter / zai) has a hardcoded shortlist of well-known models;
the operator can extend it at runtime via the model-select dialog. Both the
shortlist and the custom additions are read here.

Files written under the same directory as ``provider_file``:
* ``model``                — the currently selected model id (plain text).
* ``custom_models.json``   — ``{"openrouter": [...], "zai": [...]}``.
"""
from __future__ import annotations

import json
from pathlib import Path

OPENROUTER_MODELS: tuple[str, ...] = (
    "google/gemini-2.5-flash",
    "google/gemini-3.1-flash-lite-preview-20260303",
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4.6",
    "openai/gpt-4o-mini",
    "deepseek/deepseek-v3",
)

ZAI_MODELS: tuple[str, ...] = (
    "claude-3-5-sonnet",
    "claude-3-7-sonnet",
    "claude-haiku-4.5",
)

DEFAULT_MODEL: dict[str, str] = {
    "openrouter": OPENROUTER_MODELS[0],
    "zai": ZAI_MODELS[0],
}


def model_file(settings) -> Path:
    """File holding the currently-selected model id."""
    return settings.provider_file.parent / "model"


def custom_models_file(settings) -> Path:
    """File holding user-added custom model ids per provider."""
    return settings.provider_file.parent / "custom_models.json"


def read_current_model(settings, provider: str) -> str:
    """Return the active model id, falling back to the provider default."""
    f = model_file(settings)
    if f.exists():
        raw = f.read_text().strip()
        if raw:
            return raw
    return DEFAULT_MODEL.get(provider, "")


def write_current_model(settings, model: str) -> None:
    """Persist the selected model id."""
    f = model_file(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(model)


def read_custom_models(settings) -> dict[str, list[str]]:
    f = custom_models_file(settings)
    if not f.exists():
        return {"openrouter": [], "zai": []}
    try:
        data = json.loads(f.read_text())
    except (OSError, ValueError):
        return {"openrouter": [], "zai": []}
    return {
        "openrouter": list(data.get("openrouter") or []),
        "zai": list(data.get("zai") or []),
    }


def add_custom_model(settings, provider: str, model: str) -> None:
    """Append a user-supplied model id to the custom list (deduped)."""
    if not model.strip():
        return
    data = read_custom_models(settings)
    bucket = data.setdefault(provider, [])
    if model not in bucket:
        bucket.append(model)
    f = custom_models_file(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2))


def models_for_provider(settings, provider: str) -> list[str]:
    """Return the full ordered list (hardcoded + custom) for a provider."""
    base = OPENROUTER_MODELS if provider == "openrouter" else ZAI_MODELS
    custom = read_custom_models(settings).get(provider, [])
    seen: set[str] = set()
    out: list[str] = []
    for m in (*base, *custom):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out
