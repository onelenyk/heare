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

from src.daemon.spine_engine import (
    _NoTelemetry,
    greeting_text,
    missing_keys,
    run_spine_daemon,
    wired_features,
)

# Every _FakeAudio built during a test, newest last — a boot that failed
# has no other way to hand back the streams it opened.
_AUDIOS: list["_FakeAudio"] = []


class _FakeAudio:
    """Everything the runner touches on AudioIO, and nothing else."""

    def __init__(self, **kwargs) -> None:
        self.input_gain = 1.0
        self.output_volume = 1.0
        self.mute_input_user = False
        self.mute_output_user = False
        self.started = False
        self.stops = 0
        _AUDIOS.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False
        self.stops += 1

    def stop_playback(self) -> int:
        return 0


class _FakeState:
    """The daemon's State, minus SQLite."""

    def __init__(self) -> None:
        self.cache: dict[str, str] = {}
        # Every write in order: a boot that waits and then succeeds
        # overwrites its own status, so the last value cannot answer
        # "did it ever say it was waiting?".
        self.history: list[tuple[str, str]] = []

    async def init(self) -> None:
        return None

    def get(self, key: str, default: str = "") -> str:
        return self.cache.get(key, default)

    def get_bool(self, key: str) -> bool:
        return self.cache.get(key) == "1"

    async def set(self, key: str, value: str) -> None:
        self.cache[key] = value
        self.history.append((key, value))

    def set_cache_only(self, key: str, value: str) -> None:
        self.cache[key] = value
        self.history.append((key, value))

    def written(self, key: str) -> list[dict | str]:
        """Every JSON value (or raw string) ever written to `key`, in order."""
        result = []
        for k, v in self.history:
            if k == key:
                try:
                    result.append(json.loads(v))
                except (json.JSONDecodeError, ValueError):
                    # Not JSON, return the raw string
                    result.append(v)
        return result

    def snapshot(self) -> dict:
        return dict(self.cache)


class _FakeUtterance:
    """A simple utterance object for testing."""

    def __init__(self):
        self.stopped = False


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
        # wake with awake and required properties for the awareness poller.
        self.wake = SimpleNamespace(awake=True, required=True)
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
        self._interrupted_state = False
        self._duplex = True
        self.barge_in_enabled = True
        self._empty_stt = empty_stt
        self.turns: list[str] = []
        self.sources: list[str] = []
        self.said: list[str] = []

    @property
    def interrupted(self) -> bool:
        """Read-only property: whether the latest utterance was cut short."""
        return self._interrupted_state

    def stop_speaking(self, outcome: str = "cancelled") -> int:
        """Stop every live utterance (drops queued audio, returns bytes dropped)."""
        self._interrupted_state = True
        return 0  # fake implementation

    async def transcribe(self, pcm: bytes):
        return SimpleNamespace(
            text="" if self._empty_stt else "привіт", language="uk"
        )

    async def respond(
        self, user_text: str, *, speak: bool = True, source: str = "mic"
    ) -> str:
        # Same signature as SpineLoop.respond, so a wrapper that drops a
        # keyword fails here the way it would in the daemon.
        self.turns.append(user_text)
        self.sources.append(source)
        return "ok"

    async def _speak(self, utt, sentence: str) -> None:
        return None

    async def _say_now(self, text: str) -> None:
        # The real one speaks through self._speak, which the runner has
        # wrapped — going through it keeps the wrapper on the path.
        self.said.append(text)
        utt = _FakeUtterance()
        await self._speak(utt, text)

    async def inject(self, text: str) -> None:
        return None

    async def run(self) -> None:
        # One tick before the turn: the MCP connect is a background task,
        # and a runner that finished without ever yielding would end the
        # daemon before the bridge had a chance to land.
        await asyncio.sleep(0.05)
        await self.transcribe(b"\x00\x00")
        # As SpineLoop._answer does: the source travels with the turn.
        await self.respond("привіт", source="mic")


def _settings(tmp_path, **overrides) -> SimpleNamespace:
    settings = SimpleNamespace(
        log_dir=tmp_path / "logs",
        voice_state_file=tmp_path / "voice_state.json",
        inject_dir=tmp_path / "inject",
        workspace_dir=tmp_path / "ws",
        db_path=tmp_path / "h.db",
        mcp_dir=tmp_path / "mcp",
        spine_turn_hold_seconds=1.3,
        # Boot now refuses to start without these, so the fixture is a
        # configured machine unless a test says otherwise.
        deepseek_api_key="sk-test",
        groq_api_key="gsk-test",
        wake_word="гава",
        # Pointing at nothing on purpose: the name is resolved from
        # identity.json, and without this field the resolver would have
        # to be trusted not to read the developer's own ~/.heare.
        identity_file=tmp_path / "identity.json",
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


async def _boot(
    monkeypatch,
    tmp_path,
    features: dict[str, bool],
    *,
    settings_overrides: dict | None = None,
    build_error: Exception | None = None,
    **loop_kwargs,
):
    """Run the daemon once, with fakes, and hand back what it wired."""
    import src.spine.audio_io as audio_io
    import src.spine.main as spine_main
    import src.agent.mcp_bridge as mcp_bridge

    loop = _FakeLoop(features, **loop_kwargs)
    connects: list[str] = []

    async def _fake_build_loop(settings, **kwargs):
        if build_error is not None:
            raise build_error
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
    settings = _settings(tmp_path, **(settings_overrides or {}))
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


async def test_the_telemetry_wrapper_passes_the_source_through(
    monkeypatch, tmp_path
) -> None:
    """Commit 8f27476 taught the loop to call respond(turn, source=...);
    the daemon's telemetry wrapper still took only `speak`, so every
    spoken turn died with a TypeError before a word was said. The tests
    were blind: they called the loop's respond, never the daemon's."""
    loop, state, _, _ = await _boot(
        monkeypatch, tmp_path, {"mcp": False, "telemetry": True}
    )
    assert loop.turns == ["привіт"], "the boot turn never reached respond"
    assert loop.sources == ["mic"]
    # The attribute the loop's _answer calls is now the wrapper.
    await loop.respond("з дашборду", speak=False, source="dashboard")
    assert loop.sources[-1] == "dashboard", "the wrapper flattened the source"


async def test_published_state_is_what_was_wired(monkeypatch, tmp_path) -> None:
    """The card renders this key as fact, so it must be a fact."""
    loop, state, _, _ = await _boot(
        monkeypatch, tmp_path, {"mcp": False, "telemetry": False}
    )
    published = {e["name"]: e for e in json.loads(state.cache["spine_features"])}
    # Read from the registry rather than restated here: a new subsystem
    # must appear on the card automatically, and a list copied into the
    # test would have said the card was complete while it was not.
    from src.spine.features import FEATURES

    assert set(published) == {f.name for f in FEATURES}
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


# ── cold start: a machine with no keys, no permission, no voice ──────
#
# `heare start` on a fresh machine used to end in a traceback out of
# resolve_llm, and the dashboard's ▶ did the same every time it was
# pressed. Worse, the microphone and speaker streams were opened before
# the build and never closed, so the process died holding the mic.


async def test_missing_key_waits_instead_of_raising(monkeypatch, tmp_path) -> None:
    """No DeepSeek key: the daemon waits, says why, and starts when it
    appears — it does not raise."""
    import src.daemon.spine_engine as engine

    monkeypatch.setattr(engine, "BOOT_POLL_SECS", 0.01)

    reloads: list[int] = []

    def _fake_reload(settings):
        reloads.append(1)
        if len(reloads) >= 2:  # the key shows up on the second poll
            settings.deepseek_api_key = "sk-added-later"
        return settings

    monkeypatch.setattr(engine, "_reload_settings", _fake_reload)

    loop, state, _, _ = await _boot(
        monkeypatch,
        tmp_path,
        {"mcp": False, "telemetry": False},
        settings_overrides={"deepseek_api_key": ""},
    )

    statuses = state.written("boot_status")
    waiting = [s for s in statuses if s["phase"] == "waiting_for_keys"]
    assert waiting, "a boot blocked on a key published nothing"
    assert waiting[0]["ok"] is False
    assert waiting[0]["missing"] == ["deepseek_api_key"]
    assert waiting[0]["hint"], "the user is told the key is missing, not how"
    assert waiting[0]["costs"], "what the missing key costs is not published"
    # And it did come up once the key was there (and then shut down
    # normally, which is why `running` is false again by now).
    assert ("running", "true") in state.history
    assert statuses[-1]["phase"] == "listening" and statuses[-1]["ok"] is True


async def test_missing_key_opens_no_audio_while_waiting(
    monkeypatch, tmp_path
) -> None:
    """Waiting must not hold the microphone: nothing is opened until the
    key is there."""
    import src.daemon.spine_engine as engine

    monkeypatch.setattr(engine, "BOOT_POLL_SECS", 0.01)
    _AUDIOS.clear()
    opened_while_waiting: list[int] = []

    def _fake_reload(settings):
        opened_while_waiting.append(len(_AUDIOS))
        settings.groq_api_key = "gsk-added-later"
        return settings

    monkeypatch.setattr(engine, "_reload_settings", _fake_reload)

    await _boot(
        monkeypatch,
        tmp_path,
        {"mcp": False, "telemetry": False},
        settings_overrides={"groq_api_key": ""},
    )
    assert opened_while_waiting == [0], "audio was opened before the key arrived"


async def test_failed_build_releases_the_audio_it_opened(
    monkeypatch, tmp_path
) -> None:
    """The streams are opened before the build; a build that raises used
    to leave them open in a dying process."""
    _AUDIOS.clear()
    boom = RuntimeError("no ffmpeg on PATH")
    with pytest.raises(RuntimeError):
        await _boot(
            monkeypatch,
            tmp_path,
            {"mcp": False, "telemetry": False},
            build_error=boom,
        )
    assert _AUDIOS, "no audio was built at all"
    audio = _AUDIOS[-1]
    assert audio.started is False, "the microphone stayed open after a failed boot"
    assert audio.stops == 1


async def test_failed_build_publishes_the_failure(monkeypatch, tmp_path) -> None:
    """A crash the user cannot fix by adding a key still gets a line the
    dashboard can render."""
    state = _FakeState()
    with pytest.raises(RuntimeError):
        await _boot_with_state(
            monkeypatch,
            tmp_path,
            state,
            build_error=RuntimeError("portaudio is not installed"),
        )
    failed = [s for s in state.written("boot_status") if s["phase"] == "failed"]
    assert failed and failed[-1]["ok"] is False
    assert "portaudio" in failed[-1]["error"]


async def _boot_with_state(monkeypatch, tmp_path, state, **kwargs):
    """_boot, but with a state object the caller keeps after the raise."""
    import src.spine.audio_io as audio_io
    import src.spine.main as spine_main

    build_error = kwargs.pop("build_error", None)
    loop = _FakeLoop({"mcp": False, "telemetry": False})

    async def _fake_build_loop(settings, **kw):
        if build_error is not None:
            raise build_error
        return loop

    monkeypatch.setattr(audio_io, "AudioIO", _FakeAudio)
    monkeypatch.setattr(spine_main, "_build_loop", _fake_build_loop)
    api = SimpleNamespace(state=None, _memory_backend=None)
    await asyncio.wait_for(
        run_spine_daemon(_settings(tmp_path), state, api, handle_signals=False),
        timeout=10,
    )


async def test_greeting_is_spoken_once(monkeypatch, tmp_path) -> None:
    """The only 'I am alive' signal used to be a green dot on a page
    nobody had open."""
    import src.daemon.spine_engine as engine

    monkeypatch.delenv("HEARE_NO_GREETING", raising=False)
    monkeypatch.setattr(engine, "GREETING_DELAY_SECS", 0.0)

    loop, state, _, _ = await _boot(
        monkeypatch, tmp_path, {"mcp": False, "telemetry": False}
    )
    assert len(loop.said) == 1, f"greeting spoken {len(loop.said)} times"
    assert "зв'язку" in loop.said[0]
    assert loop.said[0].startswith("Гава")
    assert state.written("boot_status")[-1]["greeting"] == loop.said[0]


async def test_greeting_can_be_switched_off(monkeypatch, tmp_path) -> None:
    import src.daemon.spine_engine as engine

    monkeypatch.setenv("HEARE_NO_GREETING", "1")
    monkeypatch.setattr(engine, "GREETING_DELAY_SECS", 0.0)

    loop, state, _, _ = await _boot(
        monkeypatch, tmp_path, {"mcp": False, "telemetry": False}
    )
    assert loop.said == []
    assert state.written("boot_status")[-1]["greeting"] == "off"


def test_missing_keys_reads_both_of_them() -> None:
    assert missing_keys(SimpleNamespace()) == ["deepseek_api_key", "groq_api_key"]
    assert missing_keys(
        SimpleNamespace(deepseek_api_key="x", groq_api_key="   ")
    ) == ["groq_api_key"]
    assert missing_keys(SimpleNamespace(deepseek_api_key="x", groq_api_key="y")) == []


def test_missing_keys_accepts_any_provider_the_engine_would() -> None:
    """config.toml chose OpenCode, the OpenCode key is there, DeepSeek's is
    not: the engine can answer, so nothing is missing. Until 13 September
    this waited at boot for DEEPSEEK_API_KEY forever."""
    assert missing_keys(
        SimpleNamespace(llm_provider="opencode", opencode_api_key="x", groq_api_key="y")
    ) == []
    # Chosen OpenCode, but only DeepSeek has a key: resolve_llm falls back,
    # loudly — still not a reason to block the boot.
    assert missing_keys(
        SimpleNamespace(llm_provider="opencode", deepseek_api_key="x", groq_api_key="y")
    ) == []
    # No key anywhere: named after the provider the user chose, because
    # that is the one they mean to add.
    assert missing_keys(
        SimpleNamespace(llm_provider="openrouter", groq_api_key="y")
    ) == ["openrouter_api_key"]


async def test_api_shares_the_settings_the_engine_resolves_against(
    monkeypatch, tmp_path
) -> None:
    """After a key-wait boot the engine runs on a *reloaded* Settings. The
    API must hold that same object, or every key/provider/model saved in
    the dashboard lands on one the engine never reads."""
    import src.daemon.spine_engine as engine
    import src.spine.audio_io as audio_io
    import src.spine.main as spine_main
    import src.agent.mcp_bridge as mcp_bridge

    monkeypatch.setattr(engine, "BOOT_POLL_SECS", 0.01)
    seen: dict[str, object] = {}
    loop = _FakeLoop({"mcp": False, "telemetry": False})

    async def _fake_build_loop(settings, **kwargs):
        seen["engine_settings"] = settings
        return loop

    def _fake_reload(settings):
        fresh = _settings(tmp_path, deepseek_api_key="sk-fresh")
        return fresh  # a NEW object, as the real reload returns

    async def _fake_connect(settings):
        return SimpleNamespace(
            connected_servers=[], tool_names=[], register_worker_tools=lambda: [],
            aclose=lambda **kw: asyncio.sleep(0), prompt_block=lambda: "",
        )

    monkeypatch.setattr(audio_io, "AudioIO", _FakeAudio)
    monkeypatch.setattr(spine_main, "_build_loop", _fake_build_loop)
    monkeypatch.setattr(mcp_bridge, "connect_mcp_servers", _fake_connect)
    monkeypatch.setattr(engine, "_reload_settings", _fake_reload)

    boot_settings = _settings(tmp_path, deepseek_api_key="")
    api = SimpleNamespace(state=None, _memory_backend=None, config=boot_settings)
    await asyncio.wait_for(
        run_spine_daemon(boot_settings, _FakeState(), api, handle_signals=False),
        timeout=10,
    )

    assert seen["engine_settings"] is not boot_settings
    assert api.config is seen["engine_settings"]


def test_greeting_is_short_and_ukrainian(tmp_path) -> None:
    absent = SimpleNamespace(wake_word="гава", identity_file=tmp_path / "none.json")
    assert greeting_text(absent) == "Гава на зв'язку."
    assert len(greeting_text(SimpleNamespace()).split()) <= 4


def test_the_greeting_says_the_name_the_model_believes_it_has(tmp_path) -> None:
    """These were two different strings for months. identity.json said
    «Doka», `wake_word` still held its default «гава», and the assistant
    introduced itself as one and answered to the other."""
    import json

    identity = tmp_path / "identity.json"
    identity.write_text(json.dumps({"name": "Дока"}), encoding="utf-8")

    said = greeting_text(SimpleNamespace(wake_word="гава", identity_file=identity))

    assert said == "Дока на зв'язку."


# ── the browser bridge follows the dashboard without a restart ────────


class _FakeBridge:
    """A BrowserBridge-shaped object: remembers what it was built from."""

    instances: list["_FakeBridge"] = []

    def __init__(self, settings) -> None:
        self.token = getattr(settings, "browser_bridge_token", "")
        self.stopped = False
        _FakeBridge.instances.append(self)

    async def start(self) -> None:
        await asyncio.sleep(3600)  # serves forever, like the real one

    async def stop(self) -> None:
        self.stopped = True


async def _boot_with_bridge(
    monkeypatch, tmp_path, during_run, *, before_boot=None, **settings_overrides
):
    import src.agent.browser_bridge as browser_bridge
    import src.agent.mcp_bridge as mcp_bridge
    import src.daemon.spine_engine as engine
    import src.spine.audio_io as audio_io
    import src.spine.main as spine_main

    monkeypatch.setattr(engine, "BRIDGE_POLL_SECS", 0.01)
    monkeypatch.setattr(browser_bridge, "BrowserBridge", _FakeBridge)
    _FakeBridge.instances.clear()
    state = _FakeState()
    if before_boot is not None:
        await before_boot(state)
    settings = _settings(
        tmp_path, browser_bridge_enabled=True, browser_bridge_token="old", **settings_overrides
    )

    class _Loop(_FakeLoop):
        async def run(self) -> None:
            await asyncio.sleep(0.05)
            await during_run(settings, state)
            await asyncio.sleep(0.1)

    loop = _Loop({"mcp": False, "telemetry": False})

    async def _fake_build_loop(settings, **kwargs):
        return loop

    async def _fake_connect(settings):
        return SimpleNamespace(
            connected_servers=[], tool_names=[], register_worker_tools=lambda: [],
            aclose=lambda **kw: asyncio.sleep(0), prompt_block=lambda: "",
        )

    monkeypatch.setattr(audio_io, "AudioIO", _FakeAudio)
    monkeypatch.setattr(spine_main, "_build_loop", _fake_build_loop)
    monkeypatch.setattr(mcp_bridge, "connect_mcp_servers", _fake_connect)
    api = SimpleNamespace(state=None, _memory_backend=None)
    await asyncio.wait_for(
        run_spine_daemon(settings, state, api, handle_signals=False), timeout=10
    )
    return _FakeBridge.instances, browser_bridge._get_bridge()


async def test_a_rotated_token_rebuilds_the_bridge(monkeypatch, tmp_path) -> None:
    """/api/bridge/rotate-token writes the token onto the live Settings
    and sets bridge_reload; the daemon builds a new bridge from it."""

    async def rotate(settings, state):
        settings.browser_bridge_token = "new"
        await state.set("bridge_reload", "1")

    instances, live = await _boot_with_bridge(monkeypatch, tmp_path, rotate)
    assert [b.token for b in instances] == ["old", "new"]
    assert instances[0].stopped, "the old bridge kept its socket"
    assert live is None, "shutdown left a bridge registered"
    assert instances[1].stopped


async def test_switching_the_bridge_off_stops_it(monkeypatch, tmp_path) -> None:
    import src.agent.browser_bridge as browser_bridge

    seen: dict[str, object] = {}

    async def switch_off(settings, state):
        settings.browser_bridge_enabled = False
        await state.set("bridge_reload", "1")
        await asyncio.sleep(0.05)
        seen["after"] = browser_bridge._get_bridge()

    instances, _ = await _boot_with_bridge(monkeypatch, tmp_path, switch_off)
    assert len(instances) == 1 and instances[0].stopped
    assert seen["after"] is None, "off, but the verbs still had a bridge to reach"


async def test_a_stale_reload_flag_does_not_rebuild_at_boot(monkeypatch, tmp_path) -> None:
    """bridge_reload is persisted; a request made just before the last
    daemon died must not rebuild a bridge this boot has only just built."""

    async def nothing(settings, state):
        return None

    async def stale(state):
        await state.set("bridge_reload", "1")

    instances, _ = await _boot_with_bridge(monkeypatch, tmp_path, nothing, before_boot=stale)
    assert len(instances) == 1


# -- awareness poller: ears and job state ----------------------------------


async def test_awareness_poller_writes_ears(monkeypatch, tmp_path) -> None:
    """The awareness poller writes ears (awake, gate, duplex) to state."""
    import src.daemon.spine_engine as spine_engine

    # Speed up polling so it completes before boot finishes
    monkeypatch.setattr(spine_engine, "AWARE_POLL_SECS", 0.01)

    loop, state, _, _ = await _boot(
        monkeypatch,
        tmp_path,
        {"mcp": False, "telemetry": False},
        empty_stt=True,
    )

    # Check that ears was written at least once with correct structure
    ears_written = state.written("ears")
    assert len(ears_written) > 0, "ears was never written"
    ears = ears_written[-1]
    assert isinstance(ears, dict)
    assert "awake" in ears
    assert "gate" in ears
    assert "duplex" in ears
    assert isinstance(ears["awake"], bool)
    assert isinstance(ears["gate"], bool)
    assert isinstance(ears["duplex"], bool)


async def test_awareness_poller_writes_job_empty_without_toolbox(monkeypatch, tmp_path) -> None:
    """When there is no toolbox, the awareness poller writes job as empty string."""
    import src.daemon.spine_engine as spine_engine

    monkeypatch.setattr(spine_engine, "AWARE_POLL_SECS", 0.01)

    loop, state, _, _ = await _boot(
        monkeypatch,
        tmp_path,
        {"mcp": False, "telemetry": False},
        empty_stt=True,
    )

    # Check that job was written (should be empty string when no job)
    job_written = state.written("job")
    assert len(job_written) > 0, "job was never written"
    # The last written job should be an empty string (no running job)
    job = job_written[-1]
    assert job == ""
