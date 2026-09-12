"""Emit-only tools do not trigger another model round.

A call that returns a result keeps the agent loop going (the model needs it
to answer); a call that only emits something to the person — show it, remember
it — does not, and the loop must not pay for another model round because of it.
"""

from __future__ import annotations

import json

import pytest

from src.agent.hands import Hands
from src.agent.tools.system import TOOLS, returns_result
from src.config import Settings


# ── returns_result helper tests ───────────────────────────────────────


def test_returns_result_is_true_for_unknown_names() -> None:
    """Unknown tool names return True (safe default for unknown and MCP tools)."""
    assert returns_result("unknown_tool") is True
    assert returns_result("mcp__x__y") is True
    assert returns_result("mcp__foo__bar__baz") is True


def test_returns_result_is_true_for_tools_with_returns_true() -> None:
    """Tools that return results."""
    assert returns_result("bash") is True
    assert returns_result("read") is True
    assert returns_result("recall") is True
    assert returns_result("web_search") is True


def test_returns_result_is_false_for_emit_only_tools() -> None:
    """Only emit-only tools return False."""
    assert returns_result("show_text") is False
    assert returns_result("show_canvas") is False
    assert returns_result("remember") is False
    assert returns_result("forget") is False


def test_every_tool_with_returns_false_is_in_the_expected_set() -> None:
    """Pin which tools are emit-only to prevent accidental additions."""
    emit_only_names = {"show_text", "show_canvas", "remember", "forget"}
    actual_emit_only = {t.name for t in TOOLS if not t.returns}
    assert actual_emit_only == emit_only_names, (
        f"Emit-only tools changed: expected {emit_only_names}, got {actual_emit_only}"
    )


# ── agent loop tests ──────────────────────────────────────────────────


def test_show_text_reply_does_not_trigger_loop() -> None:
    """A reply with only show_text (emit-only) means no loop continues.

    (a) reply = content «Ось графік» + one `show_text` call → the tool still
    executes, but agent loop does not continue (no second chat call)
    """
    from src.agent.tools.system import returns_result

    # Simulate a reply with show_text
    reply = {
        "content": "Ось графік",
        "tool_calls": [
            {
                "id": "call_1",
                "function": {
                    "name": "show_text",
                    "arguments": json.dumps({"content": "chart here"}),
                },
            }
        ],
    }

    calls = reply.get("tool_calls") or []
    assert len(calls) == 1

    # Check: should not loop again because show_text doesn't return
    any_returns = any(
        returns_result(call.get("function", {}).get("name", ""))
        for call in calls
    )
    assert not any_returns, "show_text should not return"
    assert reply.get("content") == "Ось графік"


def test_bash_reply_triggers_loop() -> None:
    """A reply with bash (returns result) → agent loop continues.

    (b) reply = «Зараз гляну» + one `bash` call → should loop again
    because bash returns a result that the agent needs
    """
    from src.agent.tools.system import returns_result

    # Simulate a reply with bash
    reply = {
        "content": "Зараз гляну",
        "tool_calls": [
            {
                "id": "call_1",
                "function": {
                    "name": "bash",
                    "arguments": json.dumps({"command": "echo test"}),
                },
            }
        ],
    }

    calls = reply.get("tool_calls") or []
    assert len(calls) == 1

    # Check: should loop because bash returns
    any_returns = any(
        returns_result(call.get("function", {}).get("name", ""))
        for call in calls
    )
    assert any_returns, "bash should return"


def test_mixed_tools_with_returning_tool_triggers_loop() -> None:
    """A reply with show_text + bash → loop continues (bash returns).

    (c) reply with `show_text` + `bash` together → should loop because
    bash returns (show_text doesn't, but at least one does)
    """
    from src.agent.tools.system import returns_result

    # Simulate a reply with both show_text and bash
    reply = {
        "content": "Показую і перевіряю",
        "tool_calls": [
            {
                "id": "call_1",
                "function": {
                    "name": "show_text",
                    "arguments": json.dumps({"content": "info"}),
                },
            },
            {
                "id": "call_2",
                "function": {
                    "name": "bash",
                    "arguments": json.dumps({"command": "ls"}),
                },
            },
        ],
    }

    calls = reply.get("tool_calls") or []
    assert len(calls) == 2

    # Check: should loop because bash returns (show_text doesn't)
    any_returns = any(
        returns_result(call.get("function", {}).get("name", ""))
        for call in calls
    )
    assert any_returns, "At least bash should return"


@pytest.mark.asyncio
async def test_full_loop_with_emit_only_tools(monkeypatch) -> None:
    """Integration test: _loop returns immediately after emit-only tool."""
    call_count = 0

    async def fake_chat(client, messages, schemas):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "content": "Запам'ятав",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "remember",
                            "arguments": json.dumps(
                                {"type": "fact", "content": "test"}
                            ),
                        },
                    }
                ],
            }
        pytest.fail("Should not make a second chat call")

    async def fake_execute(name: str, args: str, settings=None):
        return {"success": True, "output": "stored"}

    monkeypatch.setattr("src.agent.tools.direct.execute_direct", fake_execute)

    hands = Hands(Settings())
    hands._chat = fake_chat  # type: ignore

    result = await hands._loop("remember the answer")

    assert call_count == 1, "Should only call _chat once"
    assert result == "Запам'ятав"


@pytest.mark.asyncio
async def test_full_loop_with_returning_tools(monkeypatch) -> None:
    """Integration test: _loop continues after returning tool."""
    call_count = 0

    async def fake_chat(client, messages, schemas):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "content": "Проверяю",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "bash",
                            "arguments": json.dumps(
                                {"command": "echo test"}
                            ),
                        },
                    }
                ],
            }
        elif call_count == 2:
            return {
                "content": "Готово",
                "tool_calls": [],
            }
        pytest.fail("Should only make two chat calls")

    async def fake_execute(name: str, args: str, settings=None):
        return {"success": True, "output": "test output"}

    monkeypatch.setattr("src.agent.tools.direct.execute_direct", fake_execute)

    hands = Hands(Settings())
    hands._chat = fake_chat  # type: ignore

    result = await hands._loop("check something")

    assert call_count == 2, "Should call _chat twice (second time for answer)"
    assert result == "Готово"
