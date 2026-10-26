"""A model for a part: one request per turn, with tools, either protocol.

The brain streams its model word by word; a part that does work (the native
hands) needs only the whole turn — what was said and which tools were
called. It cannot import the brain's client (a part never imports another
part), so the two wires it needs are here, beside ``heare.part``:

* ``openai`` — ``POST {base}/chat/completions``, a bearer key;
* ``anthropic`` — ``POST {base}/v1/messages``, ``x-api-key``.

The conversation is given and returned in OpenAI's shape, whichever wire
carries it. Which provider, model and key is the caller's choice: this
module knows the wires, not the providers.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

TIMEOUT_S = 20.0
MAX_TOKENS = 1024
ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True)
class Wire:
    """Where a model is and how to speak to it."""

    base_url: str
    key: str
    model: str
    style: str = "openai"  # or "anthropic"


class Talk:
    """``reply(messages, tools)`` as ``heare.core.forms.Model`` has it."""

    def __init__(self, wire: Wire, client: httpx.AsyncClient) -> None:
        self._wire = wire
        self._client = client

    async def reply(self, messages: list[dict], verbs: list[dict]) -> AsyncIterator[dict]:
        if self._wire.style == "anthropic":
            events = await self._anthropic(messages, verbs)
        else:
            events = await self._openai(messages, verbs)
        for event in events:
            yield event

    async def _openai(self, messages: list[dict], verbs: list[dict]) -> list[dict]:
        body: dict = {"model": self._wire.model, "messages": messages, "temperature": 0.2}
        if verbs:
            body["tools"] = verbs
        answer = await self._client.post(
            f"{self._wire.base_url.rstrip('/')}/chat/completions", json=body,
            timeout=TIMEOUT_S, headers={"Authorization": f"Bearer {self._wire.key}"})
        answer.raise_for_status()
        said = (answer.json().get("choices") or [{}])[0].get("message") or {}
        events: list[dict] = []
        if said.get("content"):
            events.append({"type": "delta", "text": said["content"]})
        for call in said.get("tool_calls") or []:
            events.append({"type": "tool_call", "id": call.get("id"),
                           "name": call["function"]["name"],
                           "arguments": call["function"].get("arguments") or "{}"})
        return events

    async def _anthropic(self, messages: list[dict], verbs: list[dict]) -> list[dict]:
        system, turns = anthropic_turns(messages)
        body: dict = {"model": self._wire.model, "max_tokens": MAX_TOKENS,
                      "messages": turns, "temperature": 0.2}
        if system:
            body["system"] = system
        if verbs:
            body["tools"] = [
                {"name": v["function"]["name"],
                 "description": v["function"].get("description", ""),
                 "input_schema": v["function"].get("parameters")
                 or {"type": "object", "properties": {}}}
                for v in verbs
            ]
        answer = await self._client.post(
            f"{self._wire.base_url.rstrip('/')}/v1/messages", json=body, timeout=TIMEOUT_S,
            headers={"x-api-key": self._wire.key, "anthropic-version": ANTHROPIC_VERSION})
        answer.raise_for_status()
        events: list[dict] = []
        for block in answer.json().get("content") or []:
            if block.get("type") == "text" and block.get("text"):
                events.append({"type": "delta", "text": block["text"]})
            elif block.get("type") == "tool_use":
                events.append({"type": "tool_call", "id": block.get("id"),
                               "name": block.get("name", ""),
                               "arguments": json.dumps(block.get("input") or {},
                                                       ensure_ascii=False)})
        return events


def anthropic_turns(messages: list[dict]) -> tuple[str, list[dict]]:
    """An OpenAI-shaped conversation as Anthropic's: the system prompt apart,
    tool calls and results as blocks, and no two turns of one role in a row."""
    system: list[str] = []
    turns: list[dict] = []

    def add(role: str, blocks: list[dict]) -> None:
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": blocks})

    for message in messages:
        role, content = message.get("role"), message.get("content") or ""
        if role == "system":
            system.append(str(content))
        elif role == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": message.get("tool_call_id", ""),
                          "content": str(content)}])
        elif role == "assistant" and message.get("tool_calls"):
            blocks = [{"type": "text", "text": str(content)}] if content else []
            for call in message["tool_calls"]:
                function = call.get("function") or {}
                arguments = function.get("arguments") or "{}"
                try:
                    given = json.loads(arguments) if isinstance(arguments, str) else arguments
                except ValueError:
                    given = {}
                blocks.append({"type": "tool_use", "id": call.get("id", ""),
                               "name": function.get("name", ""),
                               "input": given if isinstance(given, dict) else {}})
            add("assistant", blocks)
        elif role in ("user", "assistant") and content:
            add(str(role), [{"type": "text", "text": str(content)}])
    return "\n\n".join(system), turns
