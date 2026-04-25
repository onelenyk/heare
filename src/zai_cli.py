"""z.ai streaming client with OpenRouter fallback.

Primary: z.ai via Anthropic-compatible API (fast, Haiku)
Fallback: OpenRouter via OpenAI-compatible API (backup)

Exposes the same streaming interface as OpenRouterCLI for drop-in
replacement in GeneratorProcessor.
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from .config import Settings

logger = logging.getLogger("heare.zai_cli")


class ZaiError(RuntimeError):
    """Raised for any failure talking to z.ai or OpenRouter."""
    pass


class ZaiCLI:
    """Primary z.ai client using Anthropic SDK with OpenRouter fallback."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.z.ai/api/anthropic",
        model: str = "claude-haiku-4.5-20250114",
        timeout: float = 5.0,
        max_tokens: int = 200,
        openrouter_api_key: str | None = None,
        openrouter_model: str = "google/gemini-2.0-flash-exp:free",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._openrouter_api_key = openrouter_api_key
        self._openrouter_model = openrouter_model
        self._anthropic_client: Any | None = None
        self._openrouter_cli: Any | None = None

    def _get_anthropic_client(self):
        """Lazy-import and cache Anthropic client."""
        if self._anthropic_client is None:
            from anthropic import AsyncAnthropic

            self._anthropic_client = AsyncAnthropic(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._anthropic_client

    def _get_openrouter_cli(self):
        """Lazy-import and cache OpenRouter client."""
        if self._openrouter_cli is None:
            from .openrouter_cli import OpenRouterCLI

            self._openrouter_cli = OpenRouterCLI(
                api_key=self._openrouter_api_key,
                model=self._openrouter_model,
                timeout=self.timeout,
                max_tokens=self.max_tokens,
            )
        return self._openrouter_cli

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream from z.ai, falling back to OpenRouter on failure."""
        try:
            async for chunk in self._generate_zai(prompt, system):
                yield chunk
        except Exception as e:
            logger.warning("z.ai generation failed: %s — falling back to OpenRouter", e)
            if self._openrouter_api_key:
                try:
                    async for chunk in self._generate_openrouter(prompt, system):
                        yield chunk
                except Exception as e2:
                    raise ZaiError(f"Both z.ai and OpenRouter failed: {e}, {e2}") from e2
            else:
                raise ZaiError(f"z.ai failed and no OpenRouter fallback: {e}") from e

    async def _generate_zai(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        """Stream from z.ai via Anthropic SDK."""
        client = self._get_anthropic_client()
        messages = [{"role": "user", "content": prompt}]

        try:
            async with client.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=messages,
                system=system,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            logger.debug("z.ai stream error: %s", e)
            raise

    async def _generate_openrouter(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        """Stream from OpenRouter fallback."""
        cli = self._get_openrouter_cli()
        async for chunk in cli.generate(prompt, system=system):
            yield chunk

    @classmethod
    def from_settings(cls, settings: "Settings") -> "ZaiCLI | None":
        """Create ZaiCLI from Settings, returning None if misconfigured."""
        # Try to get API key from env first, then from Claude config
        api_key = os.environ.get("ZAI_API_KEY")
        if not api_key:
            # Try to read from ~/.claude.json env
            claude_config_path = os.path.expanduser("~/.claude.json")
            try:
                with open(claude_config_path) as f:
                    claude_config = json.load(f)
                api_key = claude_config.get("env", {}).get("ANTHROPIC_AUTH_TOKEN")
            except (OSError, json.JSONDecodeError):
                pass

        if not api_key:
            logger.warning("ZAI_API_KEY not set and not found in ~/.claude.json")
            return None

        base_url = os.environ.get(
            "ZAI_BASE_URL", "https://api.z.ai/api/anthropic"
        )
        model = os.environ.get("ZAI_MODEL", "claude-haiku-4.5-20250114")

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=settings.openrouter_timeout_seconds,
            max_tokens=200,
            openrouter_api_key=settings.openrouter_api_key,
            openrouter_model=settings.openrouter_model,
        )
