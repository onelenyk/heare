"""Centralised LLM provider configuration — the single source of truth.

Every other module imports provider metadata from here instead of
hardcoding provider names, API keys, base URLs, or model lists.
Adding a new provider = add one ``ProviderConfig`` entry in the
``PROVIDERS`` dict — everything else auto-discovers it.

Dependency-free by design: standard library + dataclasses only.
No pipecat, no switchable, no build imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProviderConfig:
    """All the metadata the system needs to talk to one LLM provider.

    Frozen so a stray assignment cannot mutate the registry at runtime.
    Every field maps to exactly one concern: auth, routing, pricing,
    identity bootstrap, or dashboard presentation.
    """

    key: str
    display_name: str
    api_key_attr: str
    api_key_env: str
    base_url: str
    default_model: str
    api_style: str
    dashboard_color: str
    timeout: float = 30.0
    model_whitelist: tuple[str, ...] = ()
    pricing: tuple[tuple[str, float, float], ...] = ()
    identity_endpoint: str = ""
    identity_model: str = ""


PROVIDERS: dict[str, ProviderConfig] = {
    "deepseek": ProviderConfig(
        key="deepseek",
        display_name="DeepSeek",
        api_key_attr="deepseek_api_key",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        api_style="openai",
        dashboard_color="yellow",
        timeout=5.0,
        model_whitelist=(
            "deepseek-chat",
            "deepseek-reasoner",
        ),
        pricing=(),
        identity_endpoint="https://api.deepseek.com/v1/chat/completions",
        identity_model="",
    ),
    "zai": ProviderConfig(
        key="zai",
        display_name="Z.AI",
        api_key_attr="zai_api_key",
        api_key_env="ZAI_API_KEY",
        base_url="https://api.z.ai/api/anthropic",
        default_model="claude-3-5-sonnet",
        api_style="anthropic",
        dashboard_color="cyan",
        timeout=30.0,
        model_whitelist=(
            "claude-3-5-sonnet",
            "claude-3-7-sonnet",
            "claude-haiku-4.5",
        ),
        pricing=(
            ("claude-opus-4-7", 15.00, 75.00),
            ("claude-sonnet-4-6", 3.00, 15.00),
            ("claude-haiku-4-5", 0.80, 4.00),
        ),
        identity_endpoint="https://api.z.ai/api/anthropic/v1/messages",
        identity_model="",
    ),
    "opencode": ProviderConfig(
        key="opencode",
        display_name="OpenCode Go",
        api_key_attr="opencode_api_key",
        api_key_env="OPENCODE_API_KEY",
        base_url="https://opencode.ai/zen/go/v1",
        default_model="minimax-m2.7",
        api_style="openai",
        dashboard_color="green",
        timeout=30.0,
        model_whitelist=(
            "minimax-m2.7",
            "minimax-m2.5",
            "kimi-k2.6",
            "kimi-k2.5",
            "glm-5.1",
            "glm-5",
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "qwen3.7-max",
            "qwen3.6-plus",
            "qwen3.5-plus",
            "mimo-v2.5-pro",
            "mimo-v2.5",
            "mimo-v2-pro",
            "mimo-v2-omni",
            "hy3-preview",
        ),
        pricing=(),
        identity_endpoint="https://opencode.ai/zen/go/v1/chat/completions",
        identity_model="",
    ),
}


def all_keys() -> list[str]:
    return list(PROVIDERS.keys())


def get_config(key: str) -> ProviderConfig:
    return PROVIDERS[key]


def get_available(settings: Any) -> list[str]:
    available: list[str] = []
    for cfg in PROVIDERS.values():
        if getattr(settings, cfg.api_key_attr, None):
            available.append(cfg.key)
    return available


def get_active(settings: Any) -> str:
    configured: str = getattr(settings, "llm_provider", "deepseek")

    provider_file: Path = getattr(
        settings,
        "provider_file",
        Path.home() / ".heare" / "provider",
    )
    if provider_file.exists():
        raw = provider_file.read_text().strip()
        if raw and raw in PROVIDERS:
            cfg = PROVIDERS[raw]
            if getattr(settings, cfg.api_key_attr, None):
                return raw

    available = get_available(settings)
    if configured in available:
        return configured
    if available:
        return available[0]
    return "deepseek"


# ── Factory functions ────────────────────────────────────────────
# Pipecat imports are DEFERRED inside each function body to avoid
# pulling in the portaudio dependency during admin CLI commands
# (e.g. `heare status`, `heare mode`).  Nothing outside the audio
# daemon should ever import pipecat at module level.


def make_openai_service(
    config: ProviderConfig, api_key: str, **kwargs: Any
) -> Any:
    """Build an :class:`OpenAILLMService` from *config*.

    All kwargs (model, extra settings, etc.) are forwarded to the
    Pipecat constructor.  The import is deferred — see module
    docstring for rationale.
    """
    from pipecat.services.openai.llm import OpenAILLMService  # deferred

    return OpenAILLMService(
        api_key=api_key,
        base_url=config.base_url,
        settings=OpenAILLMService.Settings(
            model=kwargs.pop("model", config.default_model)
        ),
        **kwargs,
    )


def make_anthropic_service(
    config: ProviderConfig, api_key: str,
) -> Any:
    """Build an :class:`AnthropicLLMService` from *config*.

    The Anthropic client is constructed with the provider's base URL
    so the service talks to the correct API endpoint (z.ai, etc.).
    """
    from anthropic import AsyncAnthropic                     # deferred
    from pipecat.services.anthropic.llm import AnthropicLLMService  # deferred

    client = AsyncAnthropic(api_key=api_key, base_url=config.base_url)
    return AnthropicLLMService(
        api_key=api_key,
        settings=AnthropicLLMService.Settings(model=config.default_model),
        client=client,
    )


def make_identity_bootstrap(
    config: ProviderConfig,
    api_key: str,
    model: str,
    timeout: float = 30.0,
) -> Any:
    """Return an async ``bootstrap(prompt) -> dict`` backed by *config*.

    Routes to the correct API automatically based on
    ``config.api_style``:

    * ``"openai"``  → ``POST {endpoint}/chat/completions``
    * ``"anthropic"`` → ``POST {endpoint}/messages`` (Messages API)

    The caller gets back a plain async callable — no pipecat
    dependency is required to *create* the bootstrap.
    """
    import json                                          # deferred
    import httpx                                         # deferred

    endpoint = config.identity_endpoint or (
        config.base_url.rstrip("/") + "/chat/completions"
    )
    is_anthropic = config.api_style == "anthropic"

    async def _bootstrap(prompt: str) -> dict:
        headers: dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        if is_anthropic:
            payload: dict[str, Any] = {
                "model": model or config.default_model,
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            }
            headers.setdefault("anthropic-version", "2023-06-01")
        else:
            payload = {
                "model": model or config.default_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are inventing a small voice-assistant persona. "
                            "Reply with ONLY one compact JSON object — no prose, "
                            "no code fence."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 400,
                "temperature": 0.9,
            }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # Extract text content
        if is_anthropic:
            raw = data["content"][0]["text"]
        else:
            raw = data["choices"][0]["message"]["content"]

        text = raw.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError(f"identity bootstrap: no JSON object in reply")
        return json.loads(text[start:end + 1])

    return _bootstrap


__all__ = [
    "ProviderConfig",
    "PROVIDERS",
    "all_keys",
    "get_config",
    "get_available",
    "get_active",
    "make_openai_service",
    "make_anthropic_service",
    "make_identity_bootstrap",
]
