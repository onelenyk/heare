"""MCP servers can be swapped while the daemon keeps talking.

Nothing here spawns a process or touches the network: a fake bridge
factory stands in for ``connect_mcp_servers``, so the tests decide what
"connected" and "failed to start" mean. What is being checked is the
part that can go wrong on its own — that the shared ToolDef registry
ends up holding exactly one live set, that a broken config or a server
that will not start costs nothing, that teardown closes the bridge that
is actually running, and that the file watcher fires once per edit
rather than once per syscall.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import types
from typing import Any

import anyio
import pytest

from src.agent.hands import Hands
from src.agent.mcp_bridge import McpBridge
from src.daemon.spine_engine import (
    _McpConfigWatcher,
    _mcp_fingerprint,
    reload_mcp,
)


# --- doubles ----------------------------------------------------------------


class _FakeSession:
    """An MCP session that never leaves the process."""

    async def call_tool(self, name: str, arguments: dict) -> Any:  # pragma: no cover
        return types.SimpleNamespace(content=[], isError=False, structuredContent=None)


class _TrackingBridge(McpBridge):
    """A real bridge (real registry behaviour) that records its closes."""

    def __init__(self) -> None:
        super().__init__()
        self.closes: list[bool] = []

    async def aclose(self, *, unregister: bool = True) -> None:
        self.closes.append(unregister)
        await super().aclose(unregister=unregister)


def _bridge_with(*names: str) -> _TrackingBridge:
    """A connected-looking bridge, without connecting to anything."""
    bridge = _TrackingBridge()
    session = _FakeSession()
    for name in names:
        slug = name.split("__", 2)[1]
        bridge._tools.append(
            (
                name,
                f"{name} description",
                {"type": "object", "properties": {"path": {"type": "string"}}},
                session,
            )
        )
        if slug not in bridge._connected_servers:
            bridge._connected_servers.append(slug)
    return bridge


class _FakeState:
    """State's three verbs, as the engine uses them."""

    def __init__(self) -> None:
        self.cache: dict[str, str] = {}

    def get(self, key: str, default: str = "") -> str:
        return self.cache.get(key, default)

    async def set(self, key: str, value: str) -> None:
        self.cache[key] = value

    def set_cache_only(self, key: str, value: str) -> None:
        self.cache[key] = value


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


def _connect_returning(bridge: Any):
    async def _connect(_settings: Any) -> Any:
        return bridge

    return _connect


def _connect_raising(exc: Exception):
    async def _connect(_settings: Any) -> Any:
        raise exc

    return _connect


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is process-wide; a leaked tool would follow the suite."""
    McpBridge.unregister_worker_tools()
    yield
    McpBridge.unregister_worker_tools()


def _settings(tmp_path) -> Any:
    return types.SimpleNamespace(
        mcp_dir=tmp_path, capability_install_enabled=False
    )


def _loop(bridge: Any = None) -> Any:
    """What the engine hangs on the spine's loop object."""
    loop = types.SimpleNamespace(mcp=bridge, _closers=[])
    if bridge is not None:
        loop._closers.append(bridge.aclose)
    return loop


def _write_config(tmp_path, servers: dict) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": servers}), encoding="utf-8"
    )


def _mcp_names() -> list[str]:
    settings = types.SimpleNamespace(capability_install_enabled=False)
    return [
        s["function"]["name"]
        for s in Hands(settings)._tool_schemas()
        if s["function"]["name"].startswith("mcp__")
    ]


# --- the swap ---------------------------------------------------------------


async def test_reload_swaps_the_tool_set(tmp_path) -> None:
    old = _bridge_with("mcp__files__read_file")
    old.register_worker_tools()
    loop = _loop(old)
    _write_config(tmp_path, {"notes": {"command": "notes-server"}})
    new = _bridge_with("mcp__notes__search", "mcp__notes__append")

    status = await reload_mcp(
        _settings(tmp_path), loop, _FakeState(), connect=_connect_returning(new)
    )

    assert loop.mcp is new
    assert sorted(_mcp_names()) == ["mcp__notes__append", "mcp__notes__search"]
    assert status["ok"] is True
    # The dead bridge is stopped, but never through the shared registry —
    # that would have emptied the set the new bridge just filled.
    assert old.closes == [False]


async def test_the_worker_sees_a_server_added_after_it_was_built(tmp_path) -> None:
    """No restart: the same Hands instance picks the tools up next job."""
    worker = Hands(types.SimpleNamespace(capability_install_enabled=False))
    loop = _loop()
    _write_config(tmp_path, {"notes": {"command": "notes-server"}})

    await reload_mcp(
        _settings(tmp_path),
        loop,
        _FakeState(),
        connect=_connect_returning(_bridge_with("mcp__notes__search")),
    )

    names = [s["function"]["name"] for s in worker._tool_schemas()]
    assert "mcp__notes__search" in names


async def test_emptying_the_config_is_obeyed(tmp_path) -> None:
    """Fail-soft is not "never shrink" — an empty config means empty."""
    old = _bridge_with("mcp__files__read_file")
    old.register_worker_tools()
    loop = _loop(old)
    _write_config(tmp_path, {})

    status = await reload_mcp(
        _settings(tmp_path),
        loop,
        _FakeState(),
        connect=_connect_returning(_bridge_with()),
    )

    assert _mcp_names() == []
    assert status["ok"] is True and status["servers"] == []
    assert old.closes == [False]


# --- fail soft --------------------------------------------------------------


async def test_a_failing_reconnect_keeps_the_old_tools(tmp_path, caplog) -> None:
    old = _bridge_with("mcp__files__read_file", "mcp__files__list_directory")
    old.register_worker_tools()
    loop = _loop(old)
    _write_config(tmp_path, {"files": {"command": "npx"}})

    with caplog.at_level("WARNING", logger="heare.spine_engine"):
        status = await reload_mcp(
            _settings(tmp_path),
            loop,
            _FakeState(),
            connect=_connect_raising(RuntimeError("spawn failed")),
        )

    assert loop.mcp is old
    assert sorted(_mcp_names()) == [
        "mcp__files__list_directory",
        "mcp__files__read_file",
    ]
    assert old.closes == []  # the live bridge was not touched
    assert status["ok"] is False and status["tools"] == 2
    assert "spawn failed" in caplog.text


async def test_a_server_that_will_not_start_keeps_the_old_tools(
    tmp_path, caplog
) -> None:
    """The realistic case: the config gained an entry that cannot spawn,
    and the bridge came back empty because every server failed."""
    old = _bridge_with("mcp__files__read_file")
    old.register_worker_tools()
    loop = _loop(old)
    _write_config(
        tmp_path,
        {
            "files": {"command": "npx"},
            "broken": {"command": "does-not-exist"},
        },
    )
    empty = _bridge_with()

    with caplog.at_level("WARNING", logger="heare.spine_engine"):
        status = await reload_mcp(
            _settings(tmp_path),
            loop,
            _FakeState(),
            connect=_connect_returning(empty),
        )

    assert loop.mcp is old
    assert _mcp_names() == ["mcp__files__read_file"]
    assert empty.closes == [False]  # the useless new bridge was disposed of
    assert status["ok"] is False and status["error"] == "no server connected"
    assert "keeping the previous 1 tool(s)" in caplog.text


async def test_a_partly_failing_reload_keeps_whatever_did_start(tmp_path) -> None:
    old = _bridge_with("mcp__files__read_file")
    old.register_worker_tools()
    loop = _loop(old)
    _write_config(
        tmp_path,
        {"files": {"command": "npx"}, "broken": {"command": "does-not-exist"}},
    )

    status = await reload_mcp(
        _settings(tmp_path),
        loop,
        _FakeState(),
        connect=_connect_returning(_bridge_with("mcp__files__read_file")),
    )

    assert _mcp_names() == ["mcp__files__read_file"]
    assert status["ok"] is True and status["servers"] == ["files"]


async def test_broken_json_keeps_the_old_tools(tmp_path, caplog) -> None:
    old = _bridge_with("mcp__files__read_file")
    old.register_worker_tools()
    loop = _loop(old)
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {', encoding="utf-8")
    called: list[int] = []

    async def _connect(_settings: Any) -> Any:  # must never run
        called.append(1)
        return _bridge_with()

    with caplog.at_level("WARNING", logger="heare.spine_engine"):
        status = await reload_mcp(
            _settings(tmp_path), loop, _FakeState(), connect=_connect
        )

    assert called == []
    assert loop.mcp is old
    assert _mcp_names() == ["mcp__files__read_file"]
    assert status["ok"] is False and status["error"] == "config unreadable"
    assert "not valid JSON" in caplog.text


async def test_a_config_that_vanished_keeps_the_old_tools(tmp_path) -> None:
    old = _bridge_with("mcp__files__read_file")
    old.register_worker_tools()
    loop = _loop(old)

    status = await reload_mcp(
        _settings(tmp_path),
        loop,
        _FakeState(),
        connect=_connect_returning(_bridge_with()),
    )

    assert loop.mcp is old
    assert _mcp_names() == ["mcp__files__read_file"]
    assert status["ok"] is False


# --- teardown ---------------------------------------------------------------


async def test_the_closers_close_the_new_bridge_not_the_old(tmp_path) -> None:
    old = _bridge_with("mcp__files__read_file")
    old.register_worker_tools()
    loop = _loop(old)
    other_closed: list[str] = []
    loop._closers.insert(0, lambda: other_closed.append("db"))
    _write_config(tmp_path, {"notes": {"command": "notes-server"}})
    new = _bridge_with("mcp__notes__search")

    await reload_mcp(
        _settings(tmp_path), loop, _FakeState(), connect=_connect_returning(new)
    )

    bridge_closers = [
        c for c in loop._closers if getattr(c, "__self__", None) is not None
    ]
    assert [c.__self__ for c in bridge_closers] == [new]

    # What src.spine.main._close_loop does at shutdown.
    for closer in loop._closers:
        result = closer()
        if hasattr(result, "__await__"):
            await result

    assert new.closes == [True]
    assert old.closes == [False]  # closed once, by the reload, and not again
    assert other_closed == ["db"]  # unrelated closers survive the swap


async def test_a_failed_reload_leaves_the_closers_alone(tmp_path) -> None:
    old = _bridge_with("mcp__files__read_file")
    old.register_worker_tools()
    loop = _loop(old)
    _write_config(tmp_path, {"files": {"command": "npx"}})

    await reload_mcp(
        _settings(tmp_path),
        loop,
        _FakeState(),
        connect=_connect_raising(RuntimeError("nope")),
    )

    assert [getattr(c, "__self__", None) for c in loop._closers] == [old]


# --- the State key ----------------------------------------------------------


async def test_the_state_key_says_what_is_connected(tmp_path) -> None:
    state = _FakeState()
    loop = _loop()
    _write_config(tmp_path, {"notes": {"command": "notes-server"}})

    await reload_mcp(
        _settings(tmp_path),
        loop,
        state,
        connect=_connect_returning(
            _bridge_with("mcp__notes__search", "mcp__notes__append")
        ),
    )

    status = json.loads(state.cache["mcp_status"])
    assert set(status) == {"servers", "tools", "ok", "error", "ts"}
    assert status["servers"] == ["notes"]
    assert status["tools"] == 2
    assert status["ok"] is True
    assert status["error"] == ""
    assert isinstance(status["ts"], float)


async def test_the_state_key_reports_the_surviving_set_after_a_failure(
    tmp_path,
) -> None:
    state = _FakeState()
    old = _bridge_with("mcp__files__read_file")
    old.register_worker_tools()
    loop = _loop(old)
    _write_config(tmp_path, {"files": {"command": "npx"}})

    await reload_mcp(
        _settings(tmp_path),
        loop,
        state,
        connect=_connect_raising(RuntimeError("boom")),
    )

    status = json.loads(state.cache["mcp_status"])
    assert status["ok"] is False
    assert status["servers"] == ["files"]  # still running, still callable
    assert status["tools"] == 1
    assert "boom" in status["error"]


# --- the capability index ---------------------------------------------------


async def test_reload_rebuilds_the_capability_index_when_there_is_one(
    tmp_path, monkeypatch
) -> None:
    from src.agent.tools import direct as direct_tools

    rebuilt: list[int] = []
    fake_index = types.SimpleNamespace(rebuild=lambda: rebuilt.append(1))
    monkeypatch.setattr(
        direct_tools, "_capability_index_singleton", fake_index, raising=False
    )
    _write_config(tmp_path, {"notes": {"command": "notes-server"}})

    await reload_mcp(
        _settings(tmp_path),
        _loop(),
        _FakeState(),
        connect=_connect_returning(_bridge_with("mcp__notes__search")),
    )

    assert rebuilt == [1]


async def test_no_capability_index_is_not_an_error(tmp_path, monkeypatch) -> None:
    from src.agent.tools import direct as direct_tools

    monkeypatch.setattr(
        direct_tools, "_capability_index_singleton", None, raising=False
    )
    _write_config(tmp_path, {"notes": {"command": "notes-server"}})

    status = await reload_mcp(
        _settings(tmp_path),
        _loop(),
        _FakeState(),
        connect=_connect_returning(_bridge_with("mcp__notes__search")),
    )

    assert status["ok"] is True


# --- who owns the sessions --------------------------------------------------
#
# An MCP stdio session lives inside an anyio cancel scope, and anyio only
# lets a scope be left by the task that entered it, innermost first. A
# reload breaks both rules by nature: the bridge was connected by the boot
# task and is closed by the poller, and the replacement is connected
# before the old one is closed. Reproduced against real servers as
# "Attempted to exit cancel scope in a different task" and as a spurious
# CancelledError at the next unrelated await — with the sessions left
# running either way. These two use a bare anyio task group, which fails
# in exactly the same place and costs no subprocess.


@contextlib.asynccontextmanager
async def _scope(record: list[str], name: str):
    async with anyio.create_task_group():
        record.append(f"enter {name}")
        yield
    record.append(f"exit {name}")


def _scoped_bridge(record: list[str], name: str) -> McpBridge:
    """A bridge whose "server" is one anyio cancel scope."""
    bridge = McpBridge()

    async def _connect_all(_settings: Any) -> None:
        await bridge._stack.enter_async_context(_scope(record, name))
        bridge._connected_servers.append(name)
        bridge._tools.append((f"mcp__{name}__x", "x", {}, None))

    bridge._connect_all = _connect_all  # type: ignore[method-assign]
    return bridge


async def test_a_bridge_connected_in_one_task_closes_from_another() -> None:
    """The daemon shape: the boot task connects, the poller closes."""
    record: list[str] = []
    bridge = _scoped_bridge(record, "a")

    await asyncio.create_task(bridge.connect(None), name="boot")
    assert record == ["enter a"]
    await asyncio.create_task(bridge.aclose(unregister=False), name="poller")

    assert record == ["enter a", "exit a"]  # the servers really stopped


async def test_closing_the_replaced_bridge_leaves_the_new_one_alone() -> None:
    """Connect-then-close is the whole point: the working set never
    disappears. That order is also what unwinds the scopes backwards."""
    record: list[str] = []
    old = _scoped_bridge(record, "old")
    new = _scoped_bridge(record, "new")
    await old.connect(None)
    await new.connect(None)

    await old.aclose(unregister=False)
    await asyncio.sleep(0)  # where the stray cancellation used to land

    assert record == ["enter old", "enter new", "exit old"]
    await new.aclose(unregister=False)
    assert record[-1] == "exit new"


async def test_a_reload_stops_the_servers_it_replaced(tmp_path) -> None:
    """End to end, in the daemon's own task layout."""
    record: list[str] = []
    old = _scoped_bridge(record, "old")
    await asyncio.create_task(old.connect(None), name="boot")
    old.register_worker_tools()
    loop = _loop(old)
    _write_config(tmp_path, {"new": {"command": "x"}})
    new = _scoped_bridge(record, "new")

    async def _connect(_settings: Any) -> Any:
        await new.connect(None)
        return new

    async def _reload() -> Any:
        return await reload_mcp(
            _settings(tmp_path), loop, _FakeState(), connect=_connect
        )

    status = await asyncio.create_task(_reload(), name="poller")

    assert status["ok"] is True
    assert record == ["enter old", "enter new", "exit old"]
    assert _mcp_names() == ["mcp__new__x"]
    await new.aclose()


# --- the watcher ------------------------------------------------------------


def test_a_quiet_config_never_fires(tmp_path) -> None:
    _write_config(tmp_path, {"files": {"command": "npx"}})
    watcher = _McpConfigWatcher(tmp_path / ".mcp.json", clock=_Clock())
    assert watcher.poll() is False
    assert watcher.poll() is False


def test_debounce_collapses_two_rapid_changes(tmp_path) -> None:
    """An editor writes in two syscalls; the installer writes right after
    a hand edit. Either way that is one reload, not two."""
    path = tmp_path / ".mcp.json"
    _write_config(tmp_path, {"files": {"command": "npx"}})
    clock = _Clock()
    watcher = _McpConfigWatcher(path, settle_s=0.5, clock=clock)

    path.write_text("", encoding="utf-8")  # truncated: half a write
    assert watcher.poll() is False

    clock.tick(0.1)
    _write_config(tmp_path, {"files": {"command": "npx"}, "notes": {"command": "n"}})
    assert watcher.poll() is False  # changed again — the timer restarts

    clock.tick(0.6)
    assert watcher.poll() is True  # one fire, for the settled content
    assert watcher.poll() is False  # and not a second one


def test_a_change_fires_once_it_holds_still(tmp_path) -> None:
    path = tmp_path / ".mcp.json"
    _write_config(tmp_path, {"files": {"command": "npx"}})
    clock = _Clock()
    watcher = _McpConfigWatcher(path, settle_s=0.5, clock=clock)

    _write_config(tmp_path, {"files": {"command": "npx"}, "notes": {"command": "n"}})
    assert watcher.poll() is False
    clock.tick(0.2)
    assert watcher.poll() is False  # still inside the settle window
    clock.tick(0.4)
    assert watcher.poll() is True


def test_a_deleted_config_is_a_change_like_any_other(tmp_path) -> None:
    path = tmp_path / ".mcp.json"
    _write_config(tmp_path, {"files": {"command": "npx"}})
    clock = _Clock()
    watcher = _McpConfigWatcher(path, settle_s=0.5, clock=clock)

    path.unlink()
    assert watcher.poll() is False
    clock.tick(0.6)
    assert watcher.poll() is True
    # ...and reload_mcp is what refuses to act on it — see
    # test_a_config_that_vanished_keeps_the_old_tools.


def test_a_missing_config_has_no_fingerprint(tmp_path) -> None:
    assert _mcp_fingerprint(tmp_path / "nothing-here.json") is None


def test_accept_swallows_the_pending_change(tmp_path) -> None:
    """After a forced reload the file has already been read."""
    path = tmp_path / ".mcp.json"
    _write_config(tmp_path, {"files": {"command": "npx"}})
    clock = _Clock()
    watcher = _McpConfigWatcher(path, settle_s=0.5, clock=clock)

    _write_config(tmp_path, {"files": {"command": "npx"}, "notes": {"command": "n"}})
    watcher.accept()
    clock.tick(1.0)
    assert watcher.poll() is False
