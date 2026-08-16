"""The daemon's two switches, and the truth it publishes about them.

`src/spine/features.py` promises that `off` means *not wired at all*.
Seven subsystems keep that promise in the composition root; the other
two — the MCP bridge and telemetry — are built by `run_spine_daemon`,
and for a long time they were built unconditionally: the log said
`mcp=OFF`, the dashboard's FeaturesCard said off, and `npx` servers
spawned anyway while turns.jsonl kept growing.

The other half of the same defect: the State key the card renders was
copied from the resolved config, so a subsystem that failed to build
still reported "on". These tests boot the runner with fakes — no audio,
no network, no real conductor — and ask what it actually wired.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.daemon.spine_engine import _NoTelemetry, run_spine_daemon, wired_features


class _FakeAudio:
    """Everything the runner touches on AudioIO, and nothing else."""

    def __init__(self, **kwargs) -> None:
        self.input_gain = 1.0
        self.output_volume = 1.0
        self.mute_input_user = False
        self.mute_output_user = False
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    def stop_playback(self) -> int:
        return 0


class _FakeState:
    """The daemon's State, minus SQLite."""

    def __init__(self) -> None:
        self.cache: dict[str, str] = {}

    async def init(self) -> None:
        return None

    def get(self, key: str, default: str = "") -> str:
        return self.cache.get(key, default)

    def get_bool(self, key: str) -> bool:
        return self.cache.get(key) == "1"

    async def set(self, key: str, value: str) -> None:
        self.cache[key] = value

    def set_cache_only(self, key: str, value: str) -> None:
        self.cache[key] = value

    def snapshot(self) -> dict:
        return dict(self.cache)


class _FakeLoop:
    """A conductor-shaped object that answers one turn and stops.

    ``run()`` deliberately goes through ``self.transcribe`` and
    ``self.respond`` — the attributes the runner replaces with its own
    wrappers — so a turn exercises whatever telemetry was (or was not)
    installed, and then the daemon shuts down on its own.
    """

    def __init__(self, features: dict[str, bool], *, empty_stt: bool = False) -> None:
        self.features = dict(features)
        self.mcp = None
        self.memory = object()
        self.aec = object()
        self.wake = object()
        self.toolbox = object()
        self.role_flow = object()
        self.persist = None
        self.usage = object()
        self.roles: dict = {}
        self.role_manager = None
        self.stream_chat = None
        self.stream_events = None
        self.hint_sink = None
        self._closers: list = []
        self._role_log: list = []
        self.role_finishing = False
        self._interrupted = False
        self._duplex = True
        self.barge_in_enabled = True
        self._empty_stt = empty_stt
        self.turns: list[str] = []

    async def transcribe(self, pcm: bytes):
        return SimpleNamespace(
            text="" if self._empty_stt else "привіт", language="uk"
        )

    async def respond(self, user_text: str, *, speak: bool = True) -> str:
        self.turns.append(user_text)
        return "ok"

    async def _speak(self, sentence: str) -> None:
        return None

    async def inject(self, text: str) -> None:
        return None

    async def run(self) -> None:
        # One tick before the turn: the MCP connect is a background task,
        # and a runner that finished without ever yielding would end the
        # daemon before the bridge had a chance to land.
        await asyncio.sleep(0.05)
        await self.transcribe(b"\x00\x00")
        await self.respond("привіт")


def _settings(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(
        log_dir=tmp_path / "logs",
        voice_state_file=tmp_path / "voice_state.json",
        inject_dir=tmp_path / "inject",
        workspace_dir=tmp_path / "ws",
        db_path=tmp_path / "h.db",
        mcp_dir=tmp_path / "mcp",
        spine_turn_hold_seconds=1.3,
    )


async def _boot(monkeypatch, tmp_path, features: dict[str, bool], **loop_kwargs):
    """Run the daemon once, with fakes, and hand back what it wired."""
    import src.spine.audio_io as audio_io
    import src.spine.main as spine_main
    import src.agent.mcp_bridge as mcp_bridge

    loop = _FakeLoop(features, **loop_kwargs)
    connects: list[str] = []

    async def _fake_build_loop(settings, **kwargs):
        return loop

    async def _fake_connect(settings):
        connects.append("connect")
        return SimpleNamespace(
            connected_servers=["fs"],
            tool_names=["mcp__fs__read"],
            register_worker_tools=lambda: ["mcp__fs__read"],
            aclose=lambda **kw: asyncio.sleep(0),
            prompt_block=lambda: "",
        )

    monkeypatch.setattr(audio_io, "AudioIO", _FakeAudio)
    monkeypatch.setattr(spine_main, "_build_loop", _fake_build_loop)
    monkeypatch.setattr(mcp_bridge, "connect_mcp_servers", _fake_connect)

    state = _FakeState()
    api = SimpleNamespace(state=None, _memory_backend=None)
    settings = _settings(tmp_path)
    await asyncio.wait_for(
        run_spine_daemon(settings, state, api, handle_signals=False), timeout=10
    )
    return loop, state, connects, settings


async def test_mcp_off_connects_nothing(monkeypatch, tmp_path) -> None:
    """`--without mcp` used to log "off" while npx servers spawned."""
    loop, state, connects, _ = await _boot(
        monkeypatch, tmp_path, {"mcp": False, "telemetry": True}
    )
    assert connects == [], "the bridge was connected despite mcp=off"
    assert loop.mcp is None
    status = json.loads(state.cache["mcp_status"])
    assert status["servers"] == [] and status["error"] == "off"


async def test_mcp_on_still_connects(monkeypatch, tmp_path) -> None:
    """The switch must gate the subsystem, not remove it."""
    loop, state, connects, _ = await _boot(
        monkeypatch, tmp_path, {"mcp": True, "telemetry": True}
    )
    assert connects == ["connect"]
    assert loop.mcp is not None


async def test_telemetry_off_writes_no_turns_file(monkeypatch, tmp_path) -> None:
    """No Telemetry object, no file — not "built but told to be quiet"."""
    loop, state, _, settings = await _boot(
        monkeypatch,
        tmp_path,
        {"mcp": False, "telemetry": False},
        empty_stt=True,
    )
    assert not (settings.log_dir / "turns.jsonl").exists()
    published = {e["name"]: e for e in json.loads(state.cache["spine_features"])}
    assert published["telemetry"]["on"] is False


async def test_telemetry_on_writes_the_line(monkeypatch, tmp_path) -> None:
    """The control: the same turn, the instrument switched on."""
    loop, state, _, settings = await _boot(
        monkeypatch,
        tmp_path,
        {"mcp": False, "telemetry": True},
        empty_stt=True,
    )
    path = settings.log_dir / "turns.jsonl"
    assert path.exists(), "telemetry was on and wrote nothing"
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]
    assert rows and rows[0]["dropped_junk"] is True


async def test_published_state_is_what_was_wired(monkeypatch, tmp_path) -> None:
    """The card renders this key as fact, so it must be a fact."""
    loop, state, _, _ = await _boot(
        monkeypatch, tmp_path, {"mcp": False, "telemetry": False}
    )
    published = {e["name"]: e for e in json.loads(state.cache["spine_features"])}
    assert set(published) == {
        "aec", "wake", "tools", "roles", "mcp", "memory", "persist",
        "usage", "telemetry",
    }
    # persist was never built on this fake loop — the config said nothing
    # about it, so the old code would have reported the default (on).
    assert published["persist"]["on"] is False
    assert published["persist"]["observed"] is True
    assert published["mcp"]["on"] is False
    assert published["wake"]["on"] is True
    assert all("cost" in e for e in published.values())


def test_a_subsystem_that_failed_to_build_reports_off() -> None:
    """The second defect, in one line: asked-for is not the same as wired."""
    loop = SimpleNamespace(
        features={name: True for name in
                  ("aec", "wake", "tools", "roles", "mcp", "memory",
                   "persist", "usage", "telemetry")},
        aec=None,  # SpineAEC raised; the loop came up without it
        wake=object(),
        toolbox=object(),
        role_flow=object(),
        mcp=None,
        memory=object(),
        persist=object(),
        usage=object(),
    )
    entries = {e["name"]: e for e in wired_features(loop, telemetry=None)}
    assert entries["aec"]["on"] is False, "config said on; nothing was built"
    assert entries["telemetry"]["on"] is False
    assert entries["wake"]["on"] is True
    assert all(e["observed"] for e in entries.values())


def test_the_null_telemetry_answers_every_call_site() -> None:
    """It stands in for Telemetry at eight call sites; a missing method
    would raise inside a turn, which is the one thing telemetry must
    never do."""
    from src.spine.telemetry import Telemetry

    null = _NoTelemetry()
    for name in ("stt", "turn_closed", "first_delta", "first_audio", "finish"):
        assert hasattr(Telemetry, name) and hasattr(null, name)
    null.stt(5, dropped=False)
    null.turn_closed()
    null.first_delta()
    null.first_audio()
    null.finish(chars=3, interrupted=False, role="")
    assert null.wired is False


@pytest.mark.parametrize("env", ["HEARE_WITHOUT", "HEARE_SAFE_MODE"])
def test_the_env_switches_reach_the_daemon(env, monkeypatch) -> None:
    """The runner reads the table the composition root resolved, and that
    resolution honours the env — a bad boot is driven from the shell."""
    from src.spine.features import resolve

    monkeypatch.setenv(env, "mcp,telemetry" if env == "HEARE_WITHOUT" else "1")
    state = resolve(SimpleNamespace())
    assert state["mcp"] is False and state["telemetry"] is False
