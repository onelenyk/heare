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
