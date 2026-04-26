"""Streaming-safe extraction of <intent>...</intent> blocks from LLM text.

Invariants (see PRD US-P2.1-01):
  1. No '<' leakage — held until intent-prefix disproven.
  2. Markdown fences stripped from intent body before JSON parse.
  3. Opener tolerates case + whitespace: <intent>, <INTENT>, < intent >.
  4. Trailing text after </intent> reaches emittable_text.
  5. Invalid JSON → warning + drop (no tag-byte leakage).
  6. Nested <intent> → outer dropped with warning.
  7. Whitespace/control chars between opener and JSON consumed; emoji in
     body causes JSON parse failure → drop silently.

Multi-intent mode: Multiple intents per stream are now supported for
action chaining (e.g., browser automation workflows). Configurable via
Settings.max_intents_per_response.

Test-only env-var `HEARE_FAKE_OPENROUTER` is consumed by OpenRouterCLI,
not this module — the parser is deterministic on input.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("heare.intent_parser")


_OPEN_RE = re.compile(r"<\s*intent\s*>", re.IGNORECASE)
_CLOSE_RE = re.compile(r"</\s*intent\s*>", re.IGNORECASE)
# Any prefix of `<\s*intent\s*` — anchored. If a buffer matches, we should
# keep holding it instead of flushing to emittable.
_PARTIAL_OPEN_RE = re.compile(
    r"^<\s*(?:i(?:n(?:t(?:e(?:n(?:t\s*)?)?)?)?)?)?$",
    re.IGNORECASE,
)
# Markdown fence: ```lang?\n...\n```
_FENCE_RE = re.compile(r"^```\w*\s*\n(.+?)\n```\s*$", re.DOTALL)


class IntentStreamParser:
    """Anti-leakage streaming parser for `<intent>{...}</intent>` blocks."""

    def __init__(self, max_intents: int = 10) -> None:
        self._buffer: str = ""
        self._state: str = "text"  # "text" | "intent"
        self._intents_emitted: int = 0
        self._max_intents: int = max_intents

    def feed(self, chunk: str) -> tuple[str, list[dict]]:
        if not chunk:
            return "", []
        self._buffer += chunk
        return self._process()

    def flush(self) -> tuple[str, list[dict]]:
        emittable = ""
        if self._state == "text":
            # Any held bytes were non-matching '<...' tail; release as text.
            emittable = self._buffer
            self._buffer = ""
        else:
            logger.warning(
                "unclosed <intent> at end-of-stream; dropping body: %r",
                self._buffer[:80],
            )
            self._buffer = ""
            self._state = "text"
        return emittable, []

    def _process(self) -> tuple[str, list[dict]]:
        emittable = ""
        intents: list[dict] = []

        while True:
            if self._state == "text":
                open_m = _OPEN_RE.search(self._buffer)
                if open_m:
                    emittable += self._buffer[: open_m.start()]
                    self._buffer = self._buffer[open_m.end():]
                    self._state = "intent"
                    continue
                # No full opener — maybe a partial at the tail.
                lt_pos = self._buffer.find("<")
                if lt_pos == -1:
                    emittable += self._buffer
                    self._buffer = ""
                    break
                emittable += self._buffer[:lt_pos]
                tail = self._buffer[lt_pos:]
                if _PARTIAL_OPEN_RE.match(tail):
                    # Hold the tail; could still become <intent>
                    self._buffer = tail
                    break
                # Definitely not an intent prefix — release the '<' too.
                emittable += tail
                self._buffer = ""
                break
            else:  # state == "intent"
                close_m = _CLOSE_RE.search(self._buffer)
                nest_m = _OPEN_RE.search(self._buffer)
                if nest_m and (close_m is None or nest_m.start() < close_m.start()):
                    logger.warning("nested <intent> in body — dropping outer")
                    self._buffer = self._buffer[nest_m.end():]
                    # Stay in intent state with the nested tag as new body
                    continue
                if close_m:
                    body = self._buffer[: close_m.start()]
                    self._buffer = self._buffer[close_m.end():]
                    self._state = "text"
                    parsed = _parse_intent_body(body)
                    if parsed is not None:
                        if self._max_intents == 0 or self._intents_emitted < self._max_intents:
                            intents.append(parsed)
                            self._intents_emitted += 1
                        else:
                            logger.warning(
                                "intent limit reached (max=%d) — dropping additional intents",
                                self._max_intents,
                            )
                    continue
                # Incomplete body — wait for more bytes.
                break

        return emittable, intents


def _parse_intent_body(body: str) -> dict | None:
    stripped = _strip_fences(body.strip()).strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        logger.warning("malformed intent JSON: %s — body=%r", e, stripped[:80])
        return None
    if not isinstance(data, dict):
        logger.warning("intent body is not a dict: %r", stripped[:80])
        return None
    return data


def _strip_fences(body: str) -> str:
    m = _FENCE_RE.match(body)
    if m:
        return m.group(1).strip()
    return body
