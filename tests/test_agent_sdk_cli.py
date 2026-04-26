"""Unit tests for AgentSDKCLI backend (claude-agent-sdk integration).

Uses FakeClient to avoid real SDK auth — all tests are offline.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent_sdk_cli import AgentSDKCLI, ClaudeCLIError
from src.config import Settings


# ─────────────────────────────────────────────────────────────────────────────
# Fakes / helpers
# ─────────────────────────────────────────────────────────────────────────────


def _settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.session_file = tmp_path / "session.json"
    s.log_dir = tmp_path / "logs"
    s.workspace_dir = tmp_path / "workspace"
    s.claude_timeout_seconds = 5
    s.claude_max_retries = 3
    s.claude_max_calls_per_minute = 100
    return s


class _TrackingIterator:
    """Async iterator that tracks aclose() calls."""

    def __init__(self, items: list[Any]) -> None:
        self._items = iter(items)
        self.closed = False

    def __aiter__(self) -> "_TrackingIterator":
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed = True


class FakeClient:
    """Minimal async-compatible replacement for ClaudeSDKClient."""

    def __init__(self, options: Any = None) -> None:
        self.options = options
        self.queries: list[str] = []
        self._response_items: list[Any] = []
        self.entered = False
        self.closed = False
        self._last_iterator: _TrackingIterator | None = None

    async def __aenter__(self) -> "FakeClient":
        self.entered = True
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.closed = True

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    def receive_response(self) -> _TrackingIterator:
        it = _TrackingIterator(self._response_items)
        self._last_iterator = it
        return it

    def set_messages(self, *items: Any) -> None:
        self._response_items = list(items)


def _text_msg(text: str) -> Any:
    from claude_agent_sdk import AssistantMessage, TextBlock

    block = MagicMock(spec=TextBlock)
    block.text = text
    msg = MagicMock(spec=AssistantMessage)
    msg.content = [block]
    return msg


def _result_msg(
    session_id: str | None = None,
    is_error: bool = False,
    errors: list[str] | None = None,
) -> Any:
    from claude_agent_sdk import ResultMessage

    msg = MagicMock(spec=ResultMessage)
    msg.session_id = session_id
    msg.is_error = is_error
    msg.errors = errors or []
    return msg


def _tool_use_msg(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """AssistantMessage carrying a single ToolUseBlock."""
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    block = MagicMock(spec=ToolUseBlock)
    block.id = "tool_use_1"
    block.name = tool_name
    block.input = tool_input
    msg = MagicMock(spec=AssistantMessage)
    msg.content = [block]
    return msg


def _tool_result_user_msg(content: Any, is_error: bool = False) -> Any:
    """UserMessage carrying a single ToolResultBlock.

    content mirrors the SDK shape: can be str, list[dict], or None.
    """
    from claude_agent_sdk import ToolResultBlock, UserMessage

    block = MagicMock(spec=ToolResultBlock)
    block.tool_use_id = "tool_use_1"
    block.content = content
    block.is_error = is_error
    msg = MagicMock(spec=UserMessage)
    msg.content = [block]
    return msg


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return _settings(tmp_path)


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def cli(settings: Settings, fake_client: FakeClient) -> AgentSDKCLI:
    """AgentSDKCLI with a pre-injected FakeClient (context already entered)."""
    c = AgentSDKCLI(settings)
    c._client = fake_client
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────────────


def test_sdk_aenter_and_aclose(settings: Settings) -> None:
    """__aenter__ enters the client; __aexit__ closes it."""
    client = FakeClient()

    async def run() -> None:
        with patch("src.agent_sdk_cli.ClaudeSDKClient", return_value=client):
            async with AgentSDKCLI(settings) as sdk:
                assert client.entered
                assert sdk._client is client
        assert client.closed

    asyncio.run(run())


def test_sdk_double_aclose_is_noop(settings: Settings) -> None:
    """Calling __aexit__ twice does not raise."""
    client = FakeClient()

    async def run() -> None:
        with patch("src.agent_sdk_cli.ClaudeSDKClient", return_value=client):
            sdk = AgentSDKCLI(settings)
            await sdk.__aenter__()
            await sdk.__aexit__(None, None, None)
            await sdk.__aexit__(None, None, None)  # second close — no-op

    asyncio.run(run())


# ─────────────────────────────────────────────────────────────────────────────
# call_decider
# ─────────────────────────────────────────────────────────────────────────────


def test_sdk_call_decider_valid_json(cli: AgentSDKCLI, fake_client: FakeClient) -> None:
    """Valid JSON assistant message → correct decision dict."""
    fake_client.set_messages(
        _text_msg('{"t": "s", "r": "hello"}'),
        _result_msg("sid1"),
    )

    async def run() -> None:
        result = await cli.call_decider("test prompt")
        assert result["type"] == "speak"
        assert result["reply"] == "hello"

    asyncio.run(run())


def test_sdk_call_decider_strips_markdown_fence(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """JSON wrapped in ```json fence is unwrapped correctly."""
    fake_client.set_messages(
        _text_msg('```json\n{"t": "s", "r": "hi"}\n```'),
        _result_msg(),
    )

    async def run() -> None:
        result = await cli.call_decider("prompt")
        assert result["type"] == "speak"
        assert result["reply"] == "hi"

    asyncio.run(run())


def test_sdk_call_decider_normalizes_short_keys_act(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """Short-key act decision is expanded to full keys."""
    fake_client.set_messages(
        _text_msg('{"t": "a", "c": 0.9, "i": "do thing", "x": "bash cmd"}'),
        _result_msg(),
    )

    async def run() -> None:
        result = await cli.call_decider("prompt")
        assert result["type"] == "act"
        assert result["confidence"] == 0.9
        assert result["intent"] == "do thing"
        assert result["action"] == "bash cmd"

    asyncio.run(run())


def test_sdk_call_decider_malformed_returns_nothing(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """Non-JSON assistant text → {type: nothing}."""
    fake_client.set_messages(
        _text_msg("this is not json"),
        _result_msg(),
    )

    async def run() -> None:
        result = await cli.call_decider("prompt")
        assert result.get("type") == "nothing"

    asyncio.run(run())


# ─────────────────────────────────────────────────────────────────────────────
# call_action / streaming
# ─────────────────────────────────────────────────────────────────────────────


def test_sdk_call_action_streams_each_text_block(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """on_line fires for segments across multiple text blocks."""
    fake_client.set_messages(
        _text_msg("line A"),
        _text_msg("line B"),
        _result_msg(),
    )
    lines: list[str] = []

    async def run() -> None:
        await cli.call_action("desc", on_line=lines.append)

    asyncio.run(run())
    assert "line A" in lines
    assert "line B" in lines


def test_sdk_call_action_splits_on_newlines(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """Single block with embedded newlines fires on_line 3 times."""
    fake_client.set_messages(
        _text_msg("a\nb\nc"),
        _result_msg(),
    )
    lines: list[str] = []

    async def run() -> None:
        await cli.call_action("desc", on_line=lines.append)

    asyncio.run(run())
    assert lines == ["a", "b", "c"]


def test_sdk_call_action_on_line_exception_does_not_break_stream(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """Exception in on_line callback is swallowed; stream continues."""
    fake_client.set_messages(
        _text_msg("first"),
        _text_msg("second"),
        _result_msg(),
    )
    lines: list[str] = []

    def bad_callback(line: str) -> None:
        lines.append(line)
        if line == "first":
            raise RuntimeError("simulated error")

    async def run() -> None:
        await cli.call_action("desc", on_line=bad_callback)

    asyncio.run(run())
    assert "second" in lines


def test_sdk_call_action_prefers_text_summary_over_tool_result(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """Phase AH2-03: when Claude emits a text summary, the returned summary
    contains ONLY that text (no duplicated raw tool output).

    Transcript:
      1. Assistant pre-text ("ok, running")
      2. Assistant ToolUseBlock
      3. User ToolResultBlock (raw 'hi' — not included in summary)
      4. Assistant post-text ("done")
    """
    fake_client.set_messages(
        _text_msg("ok, running"),
        _tool_use_msg("Bash", {"command": "echo hi"}),
        _tool_result_user_msg("hi"),
        _text_msg("done"),
        _result_msg(),
    )

    async def run() -> dict[str, str]:
        return await cli.call_action("desc")

    result = asyncio.run(run())
    summary = result["summary"]
    assert "ok, running" in summary
    assert "done" in summary
    # Prefer-text behavior: raw tool result "hi" must NOT appear in the
    # returned summary at all (substring, not just whole-line).
    assert "hi" not in summary


def test_sdk_call_action_falls_back_to_tool_result_when_no_text(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """Phase AH2-03: when Claude ends the turn silently (no TextBlock),
    the summary falls back to the tool-result content so the caller still
    has output to speak."""
    fake_client.set_messages(
        _tool_use_msg("Bash", {"command": "echo silent"}),
        _tool_result_user_msg("silent-output"),
        _result_msg(),
    )

    async def run() -> dict[str, str]:
        return await cli.call_action("desc")

    result = asyncio.run(run())
    assert "silent-output" in result["summary"]


def test_sdk_call_action_captures_tool_result_from_list_content(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """US-A-01: ToolResultBlock.content may be list[dict]; flattening picks text."""
    fake_client.set_messages(
        _tool_use_msg("Bash", {"command": "ls"}),
        _tool_result_user_msg([
            {"type": "text", "text": "file-one"},
            {"type": "text", "text": "file-two"},
        ]),
        _result_msg(),
    )

    async def run() -> dict[str, str]:
        return await cli.call_action("desc")

    result = asyncio.run(run())
    assert "file-one" in result["summary"]
    assert "file-two" in result["summary"]


def test_sdk_call_action_captures_tool_error_output(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """US-A-01: ToolResultBlock with is_error=True still contributes to summary.

    The caller needs the error text to speak a useful hint.
    """
    fake_client.set_messages(
        _tool_use_msg("Bash", {"command": "false"}),
        _tool_result_user_msg("command not found", is_error=True),
        _result_msg(),
    )

    async def run() -> dict[str, str]:
        return await cli.call_action("desc")

    result = asyncio.run(run())
    assert "command not found" in result["summary"]


def test_sdk_call_action_empty_tool_result_does_not_raise(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """US-A-01: None / empty ToolResultBlock content is tolerated silently."""
    fake_client.set_messages(
        _tool_use_msg("Bash", {"command": "true"}),
        _tool_result_user_msg(None),
        _text_msg("finished"),
        _result_msg(),
    )

    async def run() -> dict[str, str]:
        return await cli.call_action("desc")

    result = asyncio.run(run())
    assert "finished" in result["summary"]


# ─────────────────────────────────────────────────────────────────────────────
# Session persistence / loading
# ─────────────────────────────────────────────────────────────────────────────


def test_sdk_session_persisted_from_result_message(
    cli: AgentSDKCLI, fake_client: FakeClient, settings: Settings
) -> None:
    """ResultMessage.session_id is written to session_file after a call."""
    fake_client.set_messages(
        _text_msg("{}"),
        _result_msg(session_id="newsession123"),
    )

    async def run() -> None:
        await cli.call_decider("prompt")

    asyncio.run(run())
    data = json.loads(settings.session_file.read_text())
    assert data["session_id"] == "newsession123"


def test_sdk_session_loaded_on_first_call(settings: Settings) -> None:
    """Pre-written session.json causes resume= to be set in ClaudeAgentOptions."""
    sid = "existing-abc"
    settings.session_file.parent.mkdir(parents=True, exist_ok=True)
    settings.session_file.write_text(json.dumps({"session_id": sid}))

    captured_options: list[Any] = []
    client = FakeClient()

    def factory(options: Any) -> FakeClient:
        captured_options.append(options)
        return client

    async def run() -> None:
        with patch("src.agent_sdk_cli.ClaudeSDKClient", side_effect=factory):
            sdk = AgentSDKCLI(settings)
            await sdk.__aenter__()
            await sdk.__aexit__(None, None, None)

    asyncio.run(run())
    assert len(captured_options) == 1
    assert captured_options[0].resume == sid


def test_sdk_allowed_tools_uses_settings_list(settings: Settings) -> None:
    """Phase B-tools US-BT-01: ClaudeAgentOptions.allowed_tools mirrors
    Settings.agent_sdk_allowed_tools exactly (default expands beyond Bash)."""
    captured_options: list[Any] = []
    client = FakeClient()

    def factory(options: Any) -> FakeClient:
        captured_options.append(options)
        return client

    async def run() -> None:
        with patch("src.agent_sdk_cli.ClaudeSDKClient", side_effect=factory):
            sdk = AgentSDKCLI(settings)
            await sdk.__aenter__()
            await sdk.__aexit__(None, None, None)

    asyncio.run(run())
    assert len(captured_options) == 1
    tools = list(captured_options[0].allowed_tools)
    # Default covers bash + the five richer SDK tools.
    for expected in ("Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch"):
        assert expected in tools, f"missing {expected!r} in {tools}"


def test_sdk_allowed_tools_respects_custom_settings(settings: Settings) -> None:
    """Phase B-tools US-BT-01: overriding Settings.agent_sdk_allowed_tools
    propagates through to ClaudeAgentOptions — no hardcoded list."""
    settings.agent_sdk_allowed_tools = ["Bash", "Read"]
    captured_options: list[Any] = []
    client = FakeClient()

    def factory(options: Any) -> FakeClient:
        captured_options.append(options)
        return client

    async def run() -> None:
        with patch("src.agent_sdk_cli.ClaudeSDKClient", side_effect=factory):
            sdk = AgentSDKCLI(settings)
            await sdk.__aenter__()
            await sdk.__aexit__(None, None, None)

    asyncio.run(run())
    assert set(captured_options[0].allowed_tools) == {"Bash", "Read"}


def _write_mcp_json(workspace_dir: Path, servers: dict) -> None:
    """Write a .mcp.json into workspace_dir for MCP tests."""
    import json

    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))


def test_sdk_mcp_servers_empty_adds_no_mcp_entries(settings: Settings) -> None:
    """Empty workspace/.mcp.json leaves allowed_tools free of mcp__ entries."""
    _write_mcp_json(settings.workspace_dir, {})
    captured_options: list[Any] = []
    client = FakeClient()

    def factory(options: Any) -> FakeClient:
        captured_options.append(options)
        return client

    async def run() -> None:
        with patch("src.agent_sdk_cli.ClaudeSDKClient", side_effect=factory):
            sdk = AgentSDKCLI(settings)
            await sdk.__aenter__()
            await sdk.__aexit__(None, None, None)

    asyncio.run(run())
    tools = list(captured_options[0].allowed_tools)
    assert all(not t.startswith("mcp__") for t in tools), (
        f"expected no mcp__ entries when .mcp.json is empty, got {tools}"
    )


def test_sdk_mcp_servers_dedup_duplicate_names(settings: Settings) -> None:
    """Servers in .mcp.json each produce exactly one mcp__<name>__* entry (no duplication)."""
    _write_mcp_json(
        settings.workspace_dir,
        {
            "chrome-devtools": {"type": "stdio", "command": "npx"},
            "filesystem": {"type": "stdio", "command": "npx"},
        },
    )
    captured_options: list[Any] = []
    client = FakeClient()

    def factory(options: Any) -> FakeClient:
        captured_options.append(options)
        return client

    async def run() -> None:
        with patch("src.agent_sdk_cli.ClaudeSDKClient", side_effect=factory):
            sdk = AgentSDKCLI(settings)
            await sdk.__aenter__()
            await sdk.__aexit__(None, None, None)

    asyncio.run(run())
    mcp_entries = [
        t for t in captured_options[0].allowed_tools if t.startswith("mcp__")
    ]
    assert "mcp__chrome-devtools__*" in mcp_entries
    assert "mcp__filesystem__*" in mcp_entries
    # No duplicates
    assert len(mcp_entries) == len(set(mcp_entries)), (
        f"unexpected duplicates in mcp entries: {mcp_entries}"
    )


def test_sdk_mcp_servers_expand_to_wildcard_patterns(settings: Settings) -> None:
    """Each server in workspace/.mcp.json becomes mcp__<name>__* in allowed_tools."""
    base_tools = list(settings.get_sdk_allowed_tools())  # snapshot before
    _write_mcp_json(
        settings.workspace_dir,
        {
            "chrome-devtools": {"type": "stdio", "command": "npx"},
            "filesystem": {"type": "stdio", "command": "npx"},
        },
    )
    captured_options: list[Any] = []
    client = FakeClient()

    def factory(options: Any) -> FakeClient:
        captured_options.append(options)
        return client

    async def run() -> None:
        with patch("src.agent_sdk_cli.ClaudeSDKClient", side_effect=factory):
            sdk = AgentSDKCLI(settings)
            await sdk.__aenter__()
            await sdk.__aexit__(None, None, None)

    asyncio.run(run())
    tools = list(captured_options[0].allowed_tools)
    assert "mcp__chrome-devtools__*" in tools
    assert "mcp__filesystem__*" in tools
    # Base tools survive unchanged.
    for base in base_tools:
        assert base in tools, f"base tool {base!r} lost after mcp expansion"
    # settings.agent_sdk_allowed_tools was not mutated in place.
    assert list(settings.get_sdk_allowed_tools()) == base_tools


# ─────────────────────────────────────────────────────────────────────────────
# Retry / stale-session / timeout
# ─────────────────────────────────────────────────────────────────────────────


def test_sdk_stale_session_reconnects_and_retries(settings: Settings) -> None:
    """'no conversation found' error clears session, reopens client, retries."""
    client = FakeClient()
    call_count = 0

    async def run() -> None:
        nonlocal call_count
        with patch("src.agent_sdk_cli.ClaudeSDKClient", return_value=client):
            sdk = AgentSDKCLI(settings)
            await sdk.__aenter__()

            async def patched_attempt(prompt: str, *, on_line: Any = None) -> str:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise ClaudeCLIError("no conversation found")
                return '{"t": "n"}'

            with patch.object(sdk, "_attempt_query", side_effect=patched_attempt):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await sdk.call_decider("prompt")

            await sdk.__aexit__(None, None, None)

        assert result["type"] == "nothing"
        assert call_count == 2

    asyncio.run(run())


def test_sdk_retry_on_failure(settings: Settings) -> None:
    """Non-stale errors trigger backoff retry; 3rd attempt succeeds."""
    client = FakeClient()
    call_count = 0

    async def run() -> None:
        nonlocal call_count
        with patch("src.agent_sdk_cli.ClaudeSDKClient", return_value=client):
            sdk = AgentSDKCLI(settings)
            await sdk.__aenter__()

            async def patched_attempt(prompt: str, *, on_line: Any = None) -> str:
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise ClaudeCLIError("rate limit hit")
                return '{"t": "n"}'

            with patch.object(sdk, "_attempt_query", side_effect=patched_attempt):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await sdk.call_decider("prompt")

            await sdk.__aexit__(None, None, None)

        assert call_count == 3
        assert result["type"] == "nothing"

    asyncio.run(run())


def test_sdk_timeout_raises_and_kills_client(settings: Settings) -> None:
    """Stalled receive_response → ClaudeCLIError(timed out) + aclose called."""
    settings.claude_timeout_seconds = 0.05

    aclose_called = False

    class _SlowIterator:
        def __aiter__(self) -> "_SlowIterator":
            return self

        async def __anext__(self) -> Any:
            await asyncio.sleep(10)
            raise StopAsyncIteration

        async def aclose(self) -> None:
            nonlocal aclose_called
            aclose_called = True

    client = FakeClient()
    client.receive_response = lambda: _SlowIterator()  # type: ignore[assignment]

    async def run() -> None:
        with patch("src.agent_sdk_cli.ClaudeSDKClient", return_value=client):
            async with AgentSDKCLI(settings) as sdk:
                with pytest.raises(ClaudeCLIError, match="timed out"):
                    await sdk.call_decider("prompt")

    asyncio.run(run())
    assert aclose_called


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiter
# ─────────────────────────────────────────────────────────────────────────────


def test_sdk_rate_limiter_called(cli: AgentSDKCLI, fake_client: FakeClient) -> None:
    """Rate limiter acquire is awaited before each call."""
    fake_client.set_messages(_text_msg("{}"), _result_msg())
    acquire_calls: list[bool] = []

    async def fake_acquire() -> None:
        acquire_calls.append(True)

    async def run() -> None:
        with patch.object(cli._rate_limiter, "acquire", side_effect=fake_acquire):
            await cli.call_decider("prompt")

    asyncio.run(run())
    assert len(acquire_calls) == 1


# ─────────────────────────────────────────────────────────────────────────────
# bootstrap_identity
# ─────────────────────────────────────────────────────────────────────────────


def test_sdk_serializes_concurrent_calls(settings: Settings) -> None:
    """US-AH2-01: concurrent _run_query calls must NOT scramble streams.

    Drive two concurrent call_decider tasks. A SerializingFakeClient enforces
    that only one call is 'in flight' at a time and scripts a distinct
    response for each call (by call index). Asserts:
      - both calls return their own scripted JSON (no mixing),
      - at no point are two queries concurrently between query() and the end
        of the receive_response iteration.
    """

    class SerializingFakeClient:
        def __init__(self):
            self._call_index = 0
            self._queries: list[str] = []
            self._in_flight = 0
            self._max_in_flight = 0
            # Two scripted responses keyed by call order.
            self._scripted = [
                [_text_msg('{"t":"s","r":"first"}'), _result_msg("sid1")],
                [_text_msg('{"t":"s","r":"second"}'), _result_msg("sid2")],
            ]
            self._pending: list | None = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def query(self, prompt: str) -> None:
            self._in_flight += 1
            self._max_in_flight = max(self._max_in_flight, self._in_flight)
            self._queries.append(prompt)
            self._pending = self._scripted[self._call_index]
            self._call_index += 1

        def receive_response(self):
            pending = self._pending
            self._pending = None
            parent = self

            class _Iter:
                def __init__(self, items):
                    self._items = iter(items)

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    try:
                        return next(self._items)
                    except StopIteration:
                        # Turn complete; decrement in_flight
                        parent._in_flight -= 1
                        raise StopAsyncIteration

                async def aclose(self):
                    pass

            return _Iter(pending or [])

    client = SerializingFakeClient()

    async def run():
        with patch("src.agent_sdk_cli.ClaudeSDKClient", return_value=client):
            async with AgentSDKCLI(settings) as sdk:
                # Both tasks start concurrently but must serialize.
                results = await asyncio.gather(
                    sdk.call_decider("prompt-a"),
                    sdk.call_decider("prompt-b"),
                )
        return results

    results = asyncio.run(run())

    # Each caller got a distinct scripted reply — no mixing.
    replies = sorted(r.get("reply") for r in results)
    assert replies == ["first", "second"], f"scrambled: {replies}"
    # Lock enforced strict serialization — max concurrency was 1.
    assert client._max_in_flight == 1, (
        f"expected serialization, saw max_in_flight={client._max_in_flight}"
    )


def test_sdk_bootstrap_identity_uses_extract_decision(
    cli: AgentSDKCLI, fake_client: FakeClient
) -> None:
    """bootstrap_identity uses extract_decision (not parse_decider_response)."""
    payload = {
        "result": '{"name": "Gava", "creature": "fox", "vibe": "calm", "emoji": "🦊", "tagline": "hi"}'
    }
    fake_client.set_messages(
        _text_msg(json.dumps(payload)),
        _result_msg(),
    )

    async def run() -> None:
        result = await cli.bootstrap_identity("identity prompt")
        assert result["name"] == "Gava"
        assert result["creature"] == "fox"

    asyncio.run(run())
