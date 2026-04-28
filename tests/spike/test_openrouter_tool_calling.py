"""PH2-G1 — Tool-call format compliance gate.

Validates that OpenRouter + Gemini Flash produces well-formed OpenAI-style
``tool_calls`` for the tool surface the agent will need. This gate MUST
pass before any production deletion in Phase 2.

The test hits the live OpenRouter API. It is skipped when
``OPENROUTER_API_KEY`` is unset, so CI without secrets stays green.
Run explicitly with:

    uv run pytest tests/spike/test_openrouter_tool_calling.py -q -s
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
MODEL = os.environ.get(
    "PH2_GATE_MODEL", "google/gemini-3.1-flash-lite-preview-20260303"
)

pytestmark = [
    pytest.mark.skipif(
        not OPENROUTER_API_KEY,
        reason="OPENROUTER_API_KEY not set; live API gate skipped.",
    ),
]


def _client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )


def _bash_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command and return stdout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    }
                },
                "required": ["command"],
            },
        },
    }


def _read_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read file contents from the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file."}
                },
                "required": ["path"],
            },
        },
    }


def _web_search_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for a query and return results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."}
                },
                "required": ["query"],
            },
        },
    }


def _assert_tool_call(message, expected_name: str) -> dict:
    """Pull the first tool_call off ``message`` and validate its shape."""
    tool_calls = getattr(message, "tool_calls", None)
    assert tool_calls, (
        f"expected non-empty tool_calls on assistant message, got {tool_calls!r}; "
        f"message content was {getattr(message, 'content', None)!r}"
    )
    call = tool_calls[0]
    assert call.type == "function", f"tool_call.type was {call.type!r}, expected 'function'"
    assert call.function.name == expected_name, (
        f"tool_call.function.name was {call.function.name!r}, expected {expected_name!r}"
    )
    parsed = json.loads(call.function.arguments)
    assert isinstance(parsed, dict), (
        f"tool_call.function.arguments did not parse to a dict: {call.function.arguments!r}"
    )
    return parsed


@pytest.mark.asyncio
async def test_g1_single_bash_tool_call():
    """Test 1: model emits a single well-formed bash tool_call."""
    client = _client()
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a terminal assistant. When the user asks for system "
                    "info, call the appropriate tool instead of replying in prose."
                ),
            },
            {"role": "user", "content": "Run `uptime` and show me the output."},
        ],
        tools=[_bash_tool()],
        tool_choice="auto",
    )
    msg = resp.choices[0].message
    args = _assert_tool_call(msg, "bash")
    assert "command" in args, f"bash tool_call args missing 'command': {args!r}"
    assert "uptime" in args["command"].lower(), (
        f"bash command did not reference uptime: {args['command']!r}"
    )


@pytest.mark.asyncio
async def test_g1_single_read_tool_call():
    """Test 2: model emits a single well-formed read tool_call."""
    client = _client()
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a file assistant. When asked about a file's contents, "
                    "call the read tool with its absolute path."
                ),
            },
            {"role": "user", "content": "Read the file /etc/hostname for me."},
        ],
        tools=[_read_tool()],
        tool_choice="auto",
    )
    msg = resp.choices[0].message
    args = _assert_tool_call(msg, "read")
    assert "path" in args, f"read tool_call args missing 'path': {args!r}"
    assert "/etc/hostname" in args["path"], (
        f"read path did not reference /etc/hostname: {args['path']!r}"
    )


@pytest.mark.asyncio
async def test_g1_single_web_search_tool_call():
    """Test 3: model emits a single well-formed web_search tool_call."""
    client = _client()
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a search assistant. When the user asks for current "
                    "information, call the web_search tool."
                ),
            },
            {
                "role": "user",
                "content": "Find the current weather in Reykjavik.",
            },
        ],
        tools=[_web_search_tool()],
        tool_choice="auto",
    )
    msg = resp.choices[0].message
    args = _assert_tool_call(msg, "web_search")
    assert "query" in args, f"web_search tool_call args missing 'query': {args!r}"
    assert args["query"].strip(), f"web_search query was empty: {args!r}"


@pytest.mark.asyncio
async def test_g1_multi_step_tool_chain():
    """Test 4: tool-A output feeds tool-B in the second LLM turn.

    Step 1: ask for ``uptime``. Model emits bash tool_call.
    Step 2: feed a synthetic uptime result back as a tool message AND
    add an explicit user follow-up that forces grounding on the
    fed-in result. The second tool_call must reference the unique
    sentinel value present only in the fed-in result.
    """
    client = _client()
    tools = [_bash_tool()]
    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "You are a terminal assistant. Use the bash tool to run shell "
                "commands. When a previous tool result is in context, do not "
                "re-run the command — operate on the result you already have."
            ),
        },
        {"role": "user", "content": "Run `uptime` for me."},
    ]

    first = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
        tool_choice="auto",
    )
    first_msg = first.choices[0].message
    first_args = _assert_tool_call(first_msg, "bash")
    assert "uptime" in first_args["command"].lower(), (
        f"first call should run uptime, got {first_args['command']!r}"
    )

    first_call = first_msg.tool_calls[0]
    sentinel = "0.4242"
    fake_uptime = (
        f"12:34:56 up 3 days,  4:21,  2 users,  "
        f"load average: {sentinel}, 0.55, 0.61"
    )

    messages.append(
        {
            "role": "assistant",
            "content": first_msg.content or "",
            "tool_calls": [
                {
                    "id": first_call.id,
                    "type": "function",
                    "function": {
                        "name": first_call.function.name,
                        "arguments": first_call.function.arguments,
                    },
                }
            ],
        }
    )
    messages.append(
        {
            "role": "tool",
            "tool_call_id": first_call.id,
            "content": fake_uptime,
        }
    )
    messages.append(
        {
            "role": "user",
            "content": (
                "Good. Now use bash to echo only the 1-minute load-average "
                "number from that uptime output (the first number after "
                "'load average:'). Use `echo` and inline the literal value — "
                "do not re-run uptime."
            ),
        }
    )

    second = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
        tool_choice="auto",
    )
    second_msg = second.choices[0].message
    second_args = _assert_tool_call(second_msg, "bash")
    cmd = second_args["command"]
    assert sentinel in cmd, (
        f"second call must reference the sentinel {sentinel!r} from the "
        f"fed-in tool result, got command={cmd!r}"
    )
