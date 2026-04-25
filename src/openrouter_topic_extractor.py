"""Non-streaming OpenRouter client dedicated to topic extraction.

Sends a single prompt and returns a list[str] of topics. Designed for
ConversationManager.extract_topics() to replace the call_decider call
that currently goes through Claude Code. All error paths degrade to
[] (empty topics). Tracks consecutive failures and emits a WARN log
after 3+ in a row so silent degradation is observable.
"""
from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger("heare.openrouter_topic_extractor")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_CONSECUTIVE_FAILURE_THRESHOLD = 3
_TOPIC_CAP = 5


class OpenRouterTopicExtractorCLI:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 5.0,
        *,
        referer: str = "https://github.com/onelenyk/heare",
        title: str = "heare",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": referer,
            "X-Title": title,
        }
        self._consecutive_failures: int = 0

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict = {"timeout": self.timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def extract_topics(self, prompt: str) -> list[str]:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        try:
            async with self._client() as client:
                resp = await client.post(
                    OPENROUTER_URL, json=payload, headers=self._headers
                )
        except httpx.TimeoutException as e:
            logger.warning("OpenRouter topic extract timed out: %s", e)
            self._on_failure()
            return []
        except httpx.HTTPError as e:
            logger.warning("OpenRouter topic extract HTTP error: %s", e)
            self._on_failure()
            return []

        if resp.status_code != 200:
            body_preview = resp.text[:500]
            logger.warning(
                "OpenRouter topic extract HTTP %d: %s",
                resp.status_code,
                body_preview,
            )
            self._on_failure()
            return []

        try:
            data = resp.json()
            text: str = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError):
            logger.warning("OpenRouter topic extract: missing/empty choices content")
            self._on_failure()
            return []

        if not text:
            self._on_failure()
            return []

        topics = _parse_topics(text)
        if not topics:
            self._on_failure()
            return []

        self._on_success()
        return topics

    def _on_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _CONSECUTIVE_FAILURE_THRESHOLD:
            logger.warning(
                "[TOPIC EXTRACT consecutive_failures=%d backend=openrouter] "
                "degraded — check OpenRouter status or flip topic_extraction_backend=claude",
                self._consecutive_failures,
            )

    def _on_success(self) -> None:
        self._consecutive_failures = 0


def _parse_topics(text: str) -> list[str]:
    """Best-effort topic-list extraction from raw model output.

    Strategy:
      1. Strip surrounding whitespace and markdown fences (```...```).
      2. json.loads(text). If result is list -> return as list[str], capped at 5.
         If result is dict -> look for any value that is a list of str-able items;
         return the first such list (this handles {"topics":[...]} wrappers).
      3. If json.loads fails -> call _extract_first_json_array(text) which finds
         the first '[' and matching ']' substring, json.loads that.
      4. If everything fails -> return [].
    Final list items are coerced to str() and capped at 5.
    """
    text = text.strip()

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first line (```json or ```) and last line (```)
        inner_lines = lines[1:]
        if inner_lines and inner_lines[-1].strip() == "```":
            inner_lines = inner_lines[:-1]
        text = "\n".join(inner_lines).strip()

    # Try direct JSON parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed][:_TOPIC_CAP]
        if isinstance(parsed, dict):
            for val in parsed.values():
                if isinstance(val, list):
                    return [str(item) for item in val][:_TOPIC_CAP]
    except (json.JSONDecodeError, ValueError):
        pass

    # Fall back to scanning for first JSON array in text
    return _extract_first_json_array(text)


def _extract_first_json_array(text: str) -> list[str]:
    """Scan for the first '[' in text, find its matching ']' (bracket-depth aware),
    json.loads that substring. Return [] on any parse failure.
    """
    start = text.find("[")
    if start == -1:
        return []

    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                substring = text[start : i + 1]
                try:
                    parsed = json.loads(substring)
                    if isinstance(parsed, list):
                        return [str(item) for item in parsed][:_TOPIC_CAP]
                except (json.JSONDecodeError, ValueError):
                    return []
                break

    return []
