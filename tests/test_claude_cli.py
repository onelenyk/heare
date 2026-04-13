"""Tests for ClaudeCLI subprocess wrapper."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.claude_cli import ClaudeCLI, ClaudeCLIError
from src.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_proc(stdout=b"", stderr=b"", returncode=0):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = AsyncMock()
    return proc


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    """Settings wired to a temp directory so no real ~/.heare paths are touched."""
    s = Settings(
        workspace_dir=tmp_path / "workspace",
        session_file=tmp_path / "session.json",
        log_dir=tmp_path / "logs",
        claude_cli="claude",
        claude_timeout_seconds=10,
        claude_max_retries=3,
        claude_max_calls_per_minute=60,
    )
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def cli(tmp_settings: Settings) -> ClaudeCLI:
    return ClaudeCLI(tmp_settings)


# ---------------------------------------------------------------------------
# _build_args
# ---------------------------------------------------------------------------

def test_build_args_basic(cli: ClaudeCLI) -> None:
    args = cli._build_args("hello", json_output=False)
    assert args == ["claude", "-p", "hello", "--dangerously-skip-permissions"]


def test_build_args_with_session(cli: ClaudeCLI) -> None:
    cli._session_id = "sess-abc"
    args = cli._build_args("hello", json_output=False)
    assert "--resume" in args
    assert "sess-abc" in args


def test_build_args_with_json_output(cli: ClaudeCLI) -> None:
    args = cli._build_args("hello", json_output=True)
    assert "--output-format" in args
    assert "json" in args


def test_build_args_with_persona(cli: ClaudeCLI) -> None:
    cli.persona = "You are Gava."
    args = cli._build_args("hello", json_output=False)
    assert "--append-system-prompt" in args
    assert "You are Gava." in args


def test_build_args_with_model(cli: ClaudeCLI) -> None:
    args = cli._build_args("hello", json_output=False, model="haiku")
    assert "--model" in args
    assert "haiku" in args


def test_build_args_no_model_when_none(cli: ClaudeCLI) -> None:
    args = cli._build_args("hello", json_output=False, model=None)
    assert "--model" not in args


def test_call_decider_uses_decider_model(cli: ClaudeCLI) -> None:
    """call_decider must pass settings.claude_decider_model to subprocess."""
    cli.settings.claude_decider_model = "haiku"
    captured_args: list[list[str]] = []

    valid_response = json.dumps({"type": "nothing", "confidence": 0.9})
    proc = _make_mock_proc(stdout=valid_response.encode())

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_args.append(list(args))
        return proc

    async def run_test():
        with patch("asyncio.create_subprocess_exec", fake_create_subprocess_exec):
            await cli.call_decider("test prompt")

    asyncio.run(run_test())
    assert len(captured_args) == 1
    assert "--model" in captured_args[0]
    haiku_idx = captured_args[0].index("--model") + 1
    assert captured_args[0][haiku_idx] == "haiku"


def test_call_decider_strips_markdown_fence(cli: ClaudeCLI) -> None:
    """Haiku/smaller models wrap JSON in ```json ... ``` markdown fences. Must unwrap."""
    fenced = '```json\n{"type": "speak", "confidence": 0.99, "reply": "Йо!"}\n```'
    payload = json.dumps({
        "type": "result",
        "result": fenced,
        "session_id": "abc",
    })
    proc = _make_mock_proc(stdout=payload.encode())

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    async def run_test():
        with patch("asyncio.create_subprocess_exec", fake_create_subprocess_exec):
            return await cli.call_decider("test")

    decision = asyncio.run(run_test())
    assert decision["type"] == "speak"
    assert decision["confidence"] == 0.99
    assert decision["reply"] == "Йо!"


def test_call_decider_strips_plain_fence(cli: ClaudeCLI) -> None:
    """Some responses use ``` without language tag."""
    fenced = '```\n{"type": "nothing"}\n```'
    payload = json.dumps({"type": "result", "result": fenced})
    proc = _make_mock_proc(stdout=payload.encode())

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    async def run_test():
        with patch("asyncio.create_subprocess_exec", fake_create_subprocess_exec):
            return await cli.call_decider("test")

    decision = asyncio.run(run_test())
    assert decision["type"] == "nothing"


def test_strip_markdown_fence_helper() -> None:
    """Direct unit test for the fence stripper."""
    f = ClaudeCLI._strip_markdown_fence
    assert f('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert f('```\n{"a": 1}\n```') == '{"a": 1}'
    assert f('{"a": 1}') == '{"a": 1}'  # no fence — passthrough
    assert f('  ```json\n{"a": 1}\n```  ') == '{"a": 1}'  # leading/trailing whitespace


def _decider_response_via_subprocess(cli: ClaudeCLI, result_value: str) -> dict:
    """Helper: feed a JSON-wrapped result through call_decider and return the decision."""
    payload = json.dumps({"type": "result", "result": result_value})
    proc = _make_mock_proc(stdout=payload.encode())

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    async def run_test():
        with patch("asyncio.create_subprocess_exec", fake_create_subprocess_exec):
            return await cli.call_decider("test")

    return asyncio.run(run_test())


def test_call_decider_normalizes_short_keys_nothing(cli: ClaudeCLI) -> None:
    """{\"t\":\"n\"} → {\"type\":\"nothing\"}."""
    decision = _decider_response_via_subprocess(cli, '{"t":"n"}')
    assert decision["type"] == "nothing"


def test_call_decider_normalizes_short_keys_speak(cli: ClaudeCLI) -> None:
    """{\"t\":\"s\",\"r\":\"hi\"} → {\"type\":\"speak\",\"reply\":\"hi\"}."""
    decision = _decider_response_via_subprocess(cli, '{"t":"s","r":"hi"}')
    assert decision["type"] == "speak"
    assert decision["reply"] == "hi"


def test_call_decider_normalizes_short_keys_act(cli: ClaudeCLI) -> None:
    """{\"t\":\"a\",\"c\":0.9,\"i\":\"do x\",\"x\":{\"tool\":\"Bash\"}} → long-key form."""
    decision = _decider_response_via_subprocess(
        cli, '{"t":"a","c":0.9,"i":"do x","x":{"tool":"Bash"}}'
    )
    assert decision["type"] == "act"
    assert decision["confidence"] == 0.9
    assert decision["intent"] == "do x"
    assert decision["action"] == {"tool": "Bash"}


def test_call_decider_long_keys_still_work(cli: ClaudeCLI) -> None:
    """Legacy long-key format must still pass through unchanged."""
    decision = _decider_response_via_subprocess(
        cli, '{"type":"speak","reply":"hi","confidence":0.95}'
    )
    assert decision["type"] == "speak"
    assert decision["reply"] == "hi"
    assert decision["confidence"] == 0.95


def test_normalize_decision_keys_idempotent() -> None:
    """Calling the normalizer twice on the same dict yields the same result."""
    once = ClaudeCLI._normalize_decision_keys({"t": "s", "r": "hi"})
    twice = ClaudeCLI._normalize_decision_keys(once)
    assert once == twice == {"type": "speak", "reply": "hi"}


def test_normalize_decision_keys_unknown_type_passthrough() -> None:
    """Unknown type strings are not remapped (defensive)."""
    out = ClaudeCLI._normalize_decision_keys({"t": "unknown_type"})
    assert out == {"type": "unknown_type"}


def test_call_action_does_not_pass_model(cli: ClaudeCLI) -> None:
    """call_action must NOT pass --model so the user's default (Opus) is used for actions."""
    captured_args: list[list[str]] = []
    proc = _make_mock_proc(stdout=b"action result text")

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_args.append(list(args))
        return proc

    async def run_test():
        with patch("asyncio.create_subprocess_exec", fake_create_subprocess_exec):
            await cli.call_action("do something")

    asyncio.run(run_test())
    assert len(captured_args) == 1
    assert "--model" not in captured_args[0]


# ---------------------------------------------------------------------------
# call_decider
# ---------------------------------------------------------------------------

async def test_call_decider_valid_json(cli: ClaudeCLI) -> None:
    payload = json.dumps({"type": "speak", "reply": "hi"}).encode()
    proc = _make_mock_proc(stdout=payload)
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await cli.call_decider("what now?")
    assert result == {"type": "speak", "reply": "hi"}


async def test_call_decider_nested_result_dict(cli: ClaudeCLI) -> None:
    payload = json.dumps(
        {"result": {"type": "speak", "reply": "hi"}, "session_id": "abc"}
    ).encode()
    proc = _make_mock_proc(stdout=payload)
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await cli.call_decider("what now?")
    assert result["type"] == "speak"
    assert result["reply"] == "hi"
    # session_id should have been absorbed
    assert cli._session_id == "abc"


async def test_call_decider_nested_result_string(cli: ClaudeCLI) -> None:
    inner = json.dumps({"type": "nothing"})
    payload = json.dumps({"result": inner}).encode()
    proc = _make_mock_proc(stdout=payload)
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await cli.call_decider("what now?")
    assert result["type"] == "nothing"


async def test_call_decider_malformed_json_returns_nothing(cli: ClaudeCLI) -> None:
    proc = _make_mock_proc(stdout=b"not json at all")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await cli.call_decider("what now?")
    assert result["type"] == "nothing"


async def test_call_decider_missing_type_key(cli: ClaudeCLI) -> None:
    payload = json.dumps({"confidence": 0.9}).encode()
    proc = _make_mock_proc(stdout=payload)
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await cli.call_decider("what now?")
    assert result["type"] == "nothing"


async def test_call_decider_non_dict_returns_nothing(cli: ClaudeCLI) -> None:
    payload = json.dumps([1, 2, 3]).encode()
    proc = _make_mock_proc(stdout=payload)
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await cli.call_decider("what now?")
    assert result["type"] == "nothing"


# ---------------------------------------------------------------------------
# call_action
# ---------------------------------------------------------------------------

async def test_call_action_returns_summary(cli: ClaudeCLI) -> None:
    proc = _make_mock_proc(stdout=b"Task completed successfully.")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await cli.call_action("do the thing")
    assert result == {"summary": "Task completed successfully."}


# ---------------------------------------------------------------------------
# _run retry / timeout / stale-session
# ---------------------------------------------------------------------------

async def test_run_retry_on_failure(cli: ClaudeCLI) -> None:
    """Subprocess fails twice then succeeds — expect 3 total calls."""
    fail_proc = _make_mock_proc(stdout=b"", stderr=b"transient error", returncode=1)
    ok_proc = _make_mock_proc(stdout=b"ok", returncode=0)

    call_count = 0

    async def fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return fail_proc
        return ok_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await cli._run("prompt", json_output=False)

    assert result == "ok"
    assert call_count == 3


async def test_run_timeout_kills_process(cli: ClaudeCLI) -> None:
    """communicate() timing out must kill the process and raise ClaudeCLIError."""
    # Each retry gets its own proc so we can count kills accurately.
    procs: list[AsyncMock] = []

    def make_timeout_proc(*args, **kwargs):
        p = _make_mock_proc()
        p.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        procs.append(p)
        return p

    with patch("asyncio.create_subprocess_exec", side_effect=make_timeout_proc):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ClaudeCLIError, match="timed out"):
                await cli._run("prompt", json_output=False)

    # Every timed-out proc must have been killed.
    assert len(procs) > 0
    for p in procs:
        p.kill.assert_called()


async def test_stale_session_recovery(cli: ClaudeCLI, tmp_settings: Settings) -> None:
    """First call returns rc=1 + 'No conversation found' → session cleared, retry without --resume."""
    cli._session_id = "stale-session-id"

    captured_args: list[list[str]] = []
    call_count = 0

    stale_proc = _make_mock_proc(
        stdout=b"", stderr=b"No conversation found for session", returncode=1
    )
    ok_proc = _make_mock_proc(stdout=b"fresh", returncode=0)

    async def fake_exec(*args, **kwargs):
        nonlocal call_count
        captured_args.append(list(args))
        call_count += 1
        if call_count == 1:
            return stale_proc
        return ok_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await cli._run("prompt", json_output=False)

    assert result == "fresh"
    # First call had --resume; second must not
    assert "--resume" in captured_args[0]
    assert "--resume" not in captured_args[1]
    assert cli._session_id is None


# ---------------------------------------------------------------------------
# ensure_session / _persist_session
# ---------------------------------------------------------------------------

async def test_ensure_session_loads_from_file(cli: ClaudeCLI, tmp_settings: Settings) -> None:
    tmp_settings.session_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_settings.session_file.write_text(
        json.dumps({"session_id": "loaded-id", "created_at": time.time()})
    )
    sid = await cli.ensure_session()
    assert sid == "loaded-id"
    assert cli._session_id == "loaded-id"


async def test_ensure_session_no_file(cli: ClaudeCLI, tmp_settings: Settings) -> None:
    assert not tmp_settings.session_file.exists()
    sid = await cli.ensure_session()
    assert sid is None


def test_persist_session_writes_file(cli: ClaudeCLI, tmp_settings: Settings) -> None:
    cli._persist_session("new-session-id")
    assert tmp_settings.session_file.exists()
    data = json.loads(tmp_settings.session_file.read_text())
    assert data["session_id"] == "new-session-id"
    assert "created_at" in data


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

async def test_rate_limiter_called(cli: ClaudeCLI) -> None:
    """_rate_limiter.acquire() must be called before subprocess in _run."""
    proc = _make_mock_proc(stdout=b"result")
    acquire_mock = AsyncMock()
    cli._rate_limiter.acquire = acquire_mock

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        await cli._run("prompt", json_output=False)

    acquire_mock.assert_called_once()


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------

async def test_version(cli: ClaudeCLI) -> None:
    proc = _make_mock_proc(stdout=b"claude 1.0")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        v = await cli.version()
    assert v == "claude 1.0"
