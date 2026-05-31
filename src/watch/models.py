"""Model whitelist + persisted selection for the watch dashboard.

The dashboard lets the operator pick which LLM model powers the agent. Each
provider's model shortlist is sourced from the central ``PROVIDERS`` registry;
the operator can extend it at runtime via the model-select dialog. Both the
shortlist and the custom additions are read here.

Files written under the same directory as ``provider_file``:
* ``model``                — the currently selected model id (plain text).
* ``custom_models.json``   — ``{"deepseek": [...], "zai": [...], "opencode": [...]}``.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.agent.llm.providers import PROVIDERS

DEFAULT_MODEL: dict[str, str] = {
    cfg.key: cfg.default_model for cfg in PROVIDERS.values()
}


def model_file(settings) -> Path:
    """File holding the currently-selected model id."""
    return Path.home() / ".heare" / "model"


def custom_models_file(settings) -> Path:
    """File holding user-added custom model ids per provider."""
    return Path.home() / ".heare" / "custom_models.json"


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
    empty: dict[str, list[str]] = {key: [] for key in PROVIDERS}
    if not f.exists():
        return empty
    try:
        data = json.loads(f.read_text())
    except (OSError, ValueError):
        return empty
    return {key: list(data.get(key) or []) for key in PROVIDERS}


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
    """Return the full ordered list (whitelist + custom) for a provider."""
    cfg = PROVIDERS.get(provider)
    base: tuple[str, ...] = cfg.model_whitelist if cfg else ()
    custom = read_custom_models(settings).get(provider, [])
    seen: set[str] = set()
    out: list[str] = []
    for m in (*base, *custom):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out
