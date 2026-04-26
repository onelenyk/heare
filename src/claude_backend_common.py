"""Shared decider parsing helpers and ClaudeBackend Protocol.

The Protocol exists so test doubles (FakeClaudeCLI etc.) and the production
backend (AgentSDKCLI) can be type-checked against the same structural
interface without inheritance.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Protocol


logger = logging.getLogger("heare.claude_backend")

# Short-key schema (saves output tokens):
# {"t":"n"} | {"t":"s","r":"reply"} | {"t":"a","c":0.9,"i":"intent","x":{...}}
DECISION_KEY_MAP: dict[str, str] = {
    "t": "type",
    "r": "reply",
    "c": "confidence",
    "i": "intent",
    "x": "action",
}
DECISION_TYPE_MAP: dict[str, str] = {"n": "nothing", "s": "speak", "a": "act"}


def strip_markdown_fence(text: str) -> str:
    """Strip ```json ... ``` or ``` ... ``` wrappers that smaller models add."""
    s = text.strip()
    if not s.startswith("```"):
        return s
    first_nl = s.find("\n")
    if first_nl == -1:
        return s
    s = s[first_nl + 1:]
    if s.rstrip().endswith("```"):
        s = s.rstrip()[:-3]
    return s.strip()


def extract_decision(payload: Any) -> Any:
    """Unwrap nested 'result' field from Claude JSON output format."""
    if isinstance(payload, dict) and "result" in payload:
        result = payload["result"]
        if isinstance(result, str):
            cleaned = strip_markdown_fence(result)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                return result
        return result
    return payload


def normalize_decision_keys(decision: dict[str, Any]) -> dict[str, Any]:
    """Normalize short-key decider output to long-key form expected by callers.

    Idempotent: long-key dicts pass through unchanged.
    """
    out: dict[str, Any] = {}
    for k, v in decision.items():
        out[DECISION_KEY_MAP.get(k, k)] = v
    if "type" in out and isinstance(out["type"], str):
        out["type"] = DECISION_TYPE_MAP.get(out["type"], out["type"])
    return out


def parse_decider_response(raw: str) -> dict[str, Any]:
    """Full decider parse pipeline: JSON decode → extract → normalize → type guard.

    Use for call_decider only. Do NOT use for bootstrap_identity (different
    key schema — identity payloads must not be run through normalize_decision_keys).

    The SDK path returns plain assistant text (possibly fenced). The CLI path
    returns a JSON-wrapped envelope {"result": "...", "session_id": "..."}.
    strip_markdown_fence is a no-op on a bare JSON object so both paths are safe.
    """
    try:
        payload = json.loads(strip_markdown_fence(raw))
    except json.JSONDecodeError:
        logger.warning("decider returned non-JSON, treating as nothing: %s", raw[:200])
        return {"type": "nothing", "reason": "malformed response (non-JSON)"}
    decision = extract_decision(payload)
    if not isinstance(decision, dict):
        logger.warning("decider payload is not a dict, treating as nothing: %r", decision)
        return {"type": "nothing", "reason": "malformed response (not a dict)"}
    decision = normalize_decision_keys(decision)
    if "type" not in decision:
        logger.warning("decider response missing 'type' key: %r", decision)
        decision["type"] = "nothing"
    return decision


class ClaudeBackend(Protocol):
    """Structural Protocol for the Claude backend.

    AgentSDKCLI satisfies this interface structurally; test doubles like
    FakeClaudeCLI in the tests/ tree do too. Use for TYPE_CHECKING
    annotations in decider.py, identity.py, and main.py so mypy can
    enforce the contract.
    """

    persona: str | None

    async def __aenter__(self) -> "ClaudeBackend": ...

    async def __aexit__(self, *exc: Any) -> None: ...

    async def version(self) -> str: ...

    async def call_decider(self, prompt: str) -> dict[str, Any]: ...

    async def call_action(
        self,
        description: str,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> dict[str, Any]: ...

    async def bootstrap_identity(self, prompt: str) -> dict[str, Any]: ...
