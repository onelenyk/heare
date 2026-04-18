"""OpenRouter SSE streaming client for the generator role.

Streams completions via OpenRouter's OpenAI-compatible /chat/completions
endpoint. Yields text deltas as they arrive so the caller can push them
into TTS before the full response finishes.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import httpx

logger = logging.getLogger("heare.openrouter_cli")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(RuntimeError):
    """Raised for any failure talking to OpenRouter (HTTP error, timeout, parse)."""


class OpenRouterCLI:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 5.0,
        max_tokens: int = 200,
        *,
        referer: str = "https://github.com/onelenyk/heare",
        title: str = "heare",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._transport = transport  # optional, for tests
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": referer,
            "X-Title": title,
        }

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict = {"timeout": self.timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "stream": True,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }

        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", OPENROUTER_URL, json=payload, headers=self._headers
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode(errors="replace")[:500]
                        raise OpenRouterError(
                            f"OpenRouter HTTP {resp.status_code}: {body}"
                        )
                    async for chunk in self._iter_deltas(resp):
                        yield chunk
        except asyncio.TimeoutError as e:
            raise OpenRouterError(f"OpenRouter timed out after {self.timeout}s") from e
        except httpx.HTTPError as e:
            raise OpenRouterError(f"OpenRouter transport error: {e}") from e

    async def _iter_deltas(self, resp: httpx.Response) -> AsyncIterator[str]:
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            data = line[6:]
            if data.strip() == "[DONE]":
                return
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                logger.debug("skipping non-JSON SSE line: %r", data[:120])
                continue
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield content
