"""The role platform — sessions, quiet channels, artifacts.

These moved out of the conductor's tests when the policy moved out of
the conductor: RoleFlow owns the triggers, the log and hints channels,
the finishing state and the artifact. The conductor only asks it whether
a turn belongs to a session.
"""
from __future__ import annotations

import asyncio

from src.spine.role_flow import RoleFlow

from tests.test_spine_loop import FakeAudio, FakeAssembler, FakeWake, _make_loop

# -- the role platform -------------------------------------------------


class FakeRole:
    def __init__(self, name="мітинг", channel="log", artifact="підсумок",
                 prompt="поводься як секретар"):
        self.name = name
        self.channel = channel
        self.artifact = artifact
        self.prompt = prompt
        self.deny_tools = ("bash",)


class FakeRoleManager:
    def __init__(self):
        self.active = None
        self.turns: list = []
        self.finished_with: list | None = None
        self.cancelled = False

    def start(self, role):
        self.active = role
        return f"Роль «{role.name}» активна."

    def note_turn(self, turn_id):
        self.turns.append(turn_id)

    async def finish(self, *, exchanges, complete):
        self.finished_with = exchanges
        await complete([{"role": "user", "content": "збери підсумок"}])
        self.active = None

        class _A:
            full_md = "# Підсумок\nвсе добре"
            spoken = "Підсумував. Два рішення."

        return _A()

    def cancel(self):
        """RoleManager.cancel — how a session is dropped when it could not
        be ended properly. The flow falls back to it so a failed finish
        cannot leave a session running with nobody recording it."""
        role, self.active = self.active, None
        self.cancelled = True
        return f'Роль «{role.name}» скасована.' if role else None


def _wire_roles(loop, role):
    """Build the flow the way the composition root does, then let the
    conductor lend it its mouth and hands."""
    saved = {}
    flow = RoleFlow(
        role_manager=FakeRoleManager(),
        roles={role.name: role},
        trigger_match=lambda text, roles: (
            role if "почни мітинг" in text.lower() else None
        ),
        end_match=lambda text, r: "закінчили" in text.lower(),
        save_artifact=lambda name, md: saved.setdefault("path", f"/tmp/{name}.md"),
    )
    loop.adopt_role_flow(flow)
    return saved


async def test_role_session_records_everything_and_answers_nothing() -> None:
    audio = FakeAudio()
    assembler = FakeAssembler()
    loop = _make_loop(audio, assembler=assembler)
    role = FakeRole()
    saved = _wire_roles(loop, role)

    chat_calls = []
    real_chat = loop.stream_chat

    def spying_chat(m):
        chat_calls.append(m)
        return real_chat(m)

    loop.stream_chat = spying_chat

    run = asyncio.create_task(loop.run())
    assembler.transcript("Дока, почни мітинг")       # start
    assembler.transcript("обговорюємо бюджет на рік")  # logged, no reply
    assembler.transcript("рішення: беремо перший варіант")
    assembler.transcript("ну все, закінчили")          # finish -> artifact

    async def _done() -> None:
        while saved.get("path") is None:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_done(), timeout=3.0)
    run.cancel()

    mgr = loop.role_flow.role_manager
    assert mgr.active is None, "session must be closed"
    assert [e["user"] for e in mgr.finished_with] == [
        "обговорюємо бюджет на рік",
        "рішення: беремо перший варіант",
    ]
    # The only LLM traffic is the artifact build inside finish() — the
    # logged turns themselves never reached the model.
    assert len(chat_calls) == 1
    spoken = b"".join(audio.played).decode()
    assert "активна" in spoken and "Підсумував" in spoken
    assert audio.mute_output is False, "voice restored after the session"


async def test_log_role_turns_bypass_wake_gate() -> None:
    audio = FakeAudio()
    assembler = FakeAssembler()
    loop = _make_loop(audio, assembler=assembler)
    role = FakeRole()
    _wire_roles(loop, role)
    loop.wake = FakeWake(accept=False)  # gate would reject everything
    loop.role_flow.role_manager.start(role)       # session already active

    run = asyncio.create_task(loop.run())
    assembler.transcript("чужа розмова в кімнаті")

    async def _logged() -> None:
        while not loop.role_flow.log:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_logged(), timeout=2.0)
    run.cancel()

    assert loop.role_flow.log == [{"user": "чужа розмова в кімнаті", "agent": None}]
    assert loop.wake.asked == [], "wake gate must not judge in-session turns"


async def test_voice_role_conversation_lands_in_session_log() -> None:
    loop = _make_loop(FakeAudio(), reply_deltas=["Поясни", " мені чому."])
    role = FakeRole(name="вчитель", channel="voice")
    _wire_roles(loop, role)
    loop.role_flow.role_manager.start(role)

    await loop.respond("бо так швидше")

    assert loop.role_flow.log == [
        {"user": "бо так швидше", "agent": "Поясни мені чому."}
    ]


async def test_hints_channel_generates_a_silent_hint() -> None:
    """The interview prompter: a heard question becomes a dashboard hint
    via hint_sink, nothing is spoken, the turn is logged for the recap."""
    audio = FakeAudio()
    loop = _make_loop(audio, reply_deltas=["- пункт один\n- пункт два"])
    role = FakeRole(name="суфлер", channel="hints",
                    prompt="готуй план відповіді")
    _wire_roles(loop, role)
    loop.role_flow.role_manager.start(role)

    hints: list[str] = []

    async def sink(text: str, question: str = "") -> None:
        hints.append((text, question))

    loop.role_flow.hint_sink = sink

    consumed = await loop.role_flow.handle("Розкажіть про ваш досвід з Kafka")
    assert consumed is True

    async def _hinted() -> None:
        while not hints:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_hinted(), timeout=2.0)

    assert hints == [
        ("- пункт один\n- пункт два", "Розкажіть про ваш досвід з Kafka")
    ]
    assert not audio.played, "hints must never sound"
    assert loop.role_flow.log == [
        {"user": "Розкажіть про ваш досвід з Kafka", "agent": None}
    ]


async def test_repeated_end_presses_do_not_become_chat() -> None:
    """The summary takes seconds of LLM time; the button looks dead and
    the user presses again. Those extra «закінчили» used to reach the
    model and be answered as conversation."""
    audio = FakeAudio()
    loop = _make_loop(audio)
    role = FakeRole()
    _wire_roles(loop, role)
    loop.role_flow.role_manager.start(role)

    gate = asyncio.Event()
    real_finish = loop.role_flow.role_manager.finish

    async def slow_finish(*, exchanges, complete):
        await gate.wait()
        return await real_finish(exchanges=exchanges, complete=complete)

    loop.role_flow.role_manager.finish = slow_finish

    assert await loop.role_flow.handle("закінчили") is True      # first press
    await asyncio.sleep(0.01)
    assert loop.role_flow.finishing is True

    # Everything said while the summary is being written is held.
    assert await loop.role_flow.handle("закінчили") is True      # second press
    assert await loop.role_flow.handle("а що там з диском?") is True

    gate.set()

    async def _done() -> None:
        while loop.role_flow.finishing:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_done(), timeout=2.0)

    # And a stray press just after the session closed is still not chat.
    assert await loop.role_flow.handle("закінчили") is True
    user_turns = [m for m in loop.history if m["role"] == "user"]
    assert user_turns == [], "no user turn reached the model"
    # The spoken summary itself is part of the conversation, and stays.
    assert any(m["role"] == "assistant" for m in loop.history)


async def test_end_phrase_is_conversation_again_after_the_grace_window() -> None:
    loop = _make_loop(FakeAudio())
    role = FakeRole()
    _wire_roles(loop, role)
    loop.role_flow.ended_ts = 1.0  # long ago
    assert await loop.role_flow.handle("закінчили") is False


async def test_finishing_is_announced_out_loud() -> None:
    """Seven to thirty seconds of silence while the artifact is built
    reads as a dead assistant unless it says what it is doing."""
    audio = FakeAudio()
    loop = _make_loop(audio)
    role = FakeRole()
    _wire_roles(loop, role)
    loop.role_flow.role_manager.start(role)
    await loop.role_flow.handle("щось було сказано на сесії")
    audio.played.clear()

    await loop.role_flow.handle("закінчили")

    async def _done() -> None:
        while loop.role_flow.finishing:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_done(), timeout=2.0)
    spoken = b"".join(audio.played).decode()
    assert "збираю підсумок" in spoken.lower(), spoken
    assert "Підсумував" in spoken, "the summary itself still follows"


async def test_empty_session_does_not_announce_a_summary_it_will_not_build() -> None:
    audio = FakeAudio()
    loop = _make_loop(audio)
    role = FakeRole()
    _wire_roles(loop, role)
    loop.role_flow.role_manager.start(role)
    audio.played.clear()

    await loop.role_flow.handle("закінчили")   # nothing was said in the session

    async def _done() -> None:
        while loop.role_flow.finishing:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_done(), timeout=2.0)
    assert "збираю" not in b"".join(audio.played).decode().lower()


# -- what was said while the artifact was being built ------------------


def _gate_finish(flow):
    """Freeze the artifact build so the finishing window can be walked
    through a turn at a time. Returns the event that releases it."""
    gate = asyncio.Event()
    real_finish = flow.role_manager.finish

    async def slow_finish(*, exchanges, complete):
        await gate.wait()
        return await real_finish(exchanges=exchanges, complete=complete)

    flow.role_manager.finish = slow_finish
    return gate


async def test_turns_said_during_the_wait_are_answered_after_it_in_order() -> None:
    """Building an artifact is 7 to 30 seconds of LLM time. Everything
    said in that window used to be heard, transcribed, paid for and
    dropped — the conversation simply had a hole in it."""
    audio = FakeAudio()
    loop = _make_loop(audio)
    role = FakeRole()
    _wire_roles(loop, role)
    flow = loop.role_flow
    flow.role_manager.start(role)
    gate = _gate_finish(flow)

    run = asyncio.create_task(loop.run())
    assert await flow.handle("закінчили") is True
    await asyncio.sleep(0.02)
    assert flow.finishing is True

    assert await flow.handle("а що там з диском?") is True
    assert await flow.handle("і купи молока") is True
    assert flow.pending == ["а що там з диском?", "і купи молока"]

    gate.set()

    async def _answered() -> None:
        while len([m for m in loop.history if m["role"] == "user"]) < 2:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_answered(), timeout=3.0)
    run.cancel()

    assert [m["content"] for m in loop.history if m["role"] == "user"] == [
        "а що там з диском?",
        "і купи молока",
    ], "held turns come back, oldest first"
    assert flow.pending == [], "and are not asked twice"


async def test_held_turns_come_back_even_when_the_artifact_failed() -> None:
    """The replay must not hang off the happy path: a refusing model ends
    the session with no artifact, and the words said during the wait are
    still owed an answer."""
    loop = _make_loop(FakeAudio())
    role = FakeRole()
    _wire_roles(loop, role)
    flow = loop.role_flow
    flow.role_manager.start(role)
    gate = _gate_finish(flow)

    replayed: list[str] = []

    async def spy(text: str) -> None:
        replayed.append(text)

    loop.inject = spy   # the conductor's front door, watched

    async def refusing_chat(messages):
        raise RuntimeError("insufficient balance")
        yield ""  # pragma: no cover

    loop.stream_chat = refusing_chat

    await flow.handle("щось було сказано")
    assert await flow.handle("закінчили") is True
    await asyncio.sleep(0.02)
    assert await flow.handle("а що там з диском?") is True   # heard mid-build
    gate.set()

    async def _replayed() -> None:
        while not replayed:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_replayed(), timeout=3.0)
    assert replayed == ["а що там з диском?"]
    assert flow.pending == []


async def test_an_injected_result_is_never_held(caplog) -> None:
    """Injected turns are how a delegated job's answer comes back. The
    job has already finished; there is no second copy. Holding one is
    losing it."""
    loop = _make_loop(FakeAudio())
    role = FakeRole()
    _wire_roles(loop, role)
    flow = loop.role_flow
    flow.role_manager.start(role)
    flow.finishing = True
    result = "[результат роботи] диск на 82%"

    assert await flow.handle(result, injected=True) is False, (
        "an injected turn goes straight to the conversation"
    )
    assert flow.pending == []

    # Without the flag — a conductor that was never taught to pass it —
    # behaviour is exactly what it is today: held, not dropped.
    assert await flow.handle(result) is True
    assert flow.pending == [result]


async def test_the_hold_is_bounded_and_says_what_it_dropped(caplog) -> None:
    """`finishing` has latched before. If it ever does again, the held
    turns must not grow until the process dies — and what is thrown away
    must be named, because those are words a person actually said."""
    import logging

    from src.spine.role_flow import PENDING_LIMIT

    caplog.set_level(logging.WARNING, logger="spine.role_flow")
    flow = RoleFlow(role_manager=FakeRoleManager())
    flow.finishing = True   # stuck, and nothing is going to clear it

    for i in range(PENDING_LIMIT + 3):
        assert await flow.handle(f"репліка {i}") is True

    assert len(flow.pending) == PENDING_LIMIT
    assert flow.pending[0] == "репліка 3", "the oldest go first"
    assert flow.pending[-1] == f"репліка {PENDING_LIMIT + 2}"
    text = "\n".join(
        r.getMessage() for r in caplog.records if r.name == "spine.role_flow"
    )
    assert "репліка 0" in text and "репліка 2" in text, text


async def test_nothing_is_held_when_no_artifact_is_being_built() -> None:
    loop = _make_loop(FakeAudio())
    role = FakeRole()
    _wire_roles(loop, role)
    flow = loop.role_flow

    assert await flow.handle("звичайне питання") is False
    flow.role_manager.start(role)
    assert await flow.handle("сказане на сесії") is True   # logged, not held

    assert flow.pending == []
    assert flow.log == [{"user": "сказане на сесії", "agent": None}]


async def test_a_flow_with_no_conductor_keeps_its_held_turns_for_draining() -> None:
    """A flow built by hand — no loop behind its callbacks — has nowhere
    to hand turns back to. It keeps them rather than dropping them, and
    whoever owns it can take them with drain_pending()."""
    flow = RoleFlow(role_manager=FakeRoleManager())
    flow.finishing = True
    await flow.handle("перше")
    await flow.handle("друге")
    flow.finishing = False

    await flow._replay_pending()
    assert flow.pending == ["перше", "друге"], "kept, not dropped"
    assert flow.drain_pending() == ["перше", "друге"]
    assert flow.pending == []


# -- the end of a session must never latch -----------------------------


async def _finished(flow, timeout: float = 2.0) -> None:
    async def _wait() -> None:
        while flow.finishing:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait(), timeout=timeout)


async def test_a_dead_speaker_does_not_leave_the_assistant_deaf() -> None:
    """The worst latch in the spine: `finishing` holds every turn, and the
    two spoken lines of finish() sat outside the try that cleared it. One
    ffmpeg that is not installed and the assistant listened, transcribed,
    paid Groq and answered nothing until a restart."""
    audio = FakeAudio()
    loop = _make_loop(audio)
    role = FakeRole()
    _wire_roles(loop, role)
    flow = loop.role_flow

    await flow.handle("Дока, почни мітинг")     # log channel: speaker muted
    await flow.handle("обговорюємо бюджет")     # something to summarise

    async def dead_mouth(text: str):
        raise RuntimeError("ffmpeg not found")
        yield b""  # pragma: no cover

    loop.synthesise = dead_mouth

    await flow.handle("ну все, закінчили")
    await _finished(flow)

    assert flow.finishing is False, "the latch must be cleared on every path"
    assert flow.role_manager.active is None, "the session must be closed"
    assert audio.mute_output is False, "the speaker must be un-muted again"

    # And the very next turn is ordinary conversation again, not swallowed.
    loop.synthesise = _make_loop(audio).synthesise  # the mouth is back
    assert await flow.handle("а що там з диском?") is False
    assert await loop.respond("а що там з диском?") == "Вітаю!"


async def test_a_refusing_model_does_not_leave_the_assistant_deaf() -> None:
    """Same latch through the other door: the artifact build is one whole
    LLM call, and a provider that raises must still end the session."""
    audio = FakeAudio()
    loop = _make_loop(audio)
    role = FakeRole()
    _wire_roles(loop, role)
    flow = loop.role_flow

    await flow.handle("Дока, почни мітинг")
    await flow.handle("обговорюємо бюджет")

    async def refusing_chat(messages):
        raise RuntimeError("insufficient balance")
        yield ""  # pragma: no cover

    loop.stream_chat = refusing_chat

    await flow.handle("ну все, закінчили")
    await _finished(flow)

    assert flow.finishing is False
    assert flow.role_manager.active is None
    assert flow.role_manager.cancelled is True, (
        "a finish that never deactivated must fall back to cancel()"
    )
    assert audio.mute_output is False
    spoken = b"".join(audio.played).decode()
    assert "Роль завершено" in spoken, "the failure is still said out loud"
    assert await flow.handle("а що там з диском?") is False


async def test_a_failing_finish_task_is_logged_not_swallowed(caplog) -> None:
    """finish() runs off the turn loop. A bare create_task() is garbage
    collectable mid-flight and never has its exception retrieved — the
    session would die in silence with nothing in the journal."""
    import logging

    caplog.set_level(logging.ERROR, logger="spine.role_flow")
    loop = _make_loop(FakeAudio())
    role = FakeRole()
    _wire_roles(loop, role)
    flow = loop.role_flow
    flow.role_manager.start(role)

    async def exploding_finish() -> None:
        raise RuntimeError("щось геть зламалось")

    flow.finish = exploding_finish

    assert await flow.handle("закінчили") is True

    def _ours() -> list:
        return [r for r in caplog.records if r.name == "spine.role_flow"]

    async def _logged() -> None:
        while not _ours():
            await asyncio.sleep(0.01)

    # The flow itself must report it — not asyncio's "Task exception was
    # never retrieved", which only fires whenever the task is collected.
    await asyncio.wait_for(_logged(), timeout=2.0)
    text = "\n".join(r.getMessage() for r in _ours())
    assert "role finish" in text and "щось геть зламалось" in text


# -- the seam between the conductor and the policy ---------------------


async def test_a_loop_without_a_role_flow_still_converses() -> None:
    """No roles wired is the plain-assistant case: every turn is chat,
    including the words that would have ended a session."""
    audio = FakeAudio()
    assembler = FakeAssembler()
    loop = _make_loop(audio, assembler=assembler)
    assert loop.role_flow is None

    run = asyncio.create_task(loop.run())
    assembler.transcript("ну все, закінчили")

    async def _answered() -> None:
        while not audio.played:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_answered(), timeout=2.0)
    run.cancel()
    assert loop.history[0] == {"role": "user", "content": "ну все, закінчили"}


async def test_the_old_flat_names_still_reach_the_flow() -> None:
    """main.py and spine_engine.py set and read these names on the loop —
    before the flow is adopted (the CLI) and after it (the daemon adds
    hint_sink later). The policy moved out from under them; they must
    not notice."""
    loop = _make_loop(FakeAudio())
    role = FakeRole()

    loop.roles = {role.name: role}
    loop.role_manager = FakeRoleManager()
    flow = RoleFlow()
    loop.adopt_role_flow(flow)
    loop.hint_sink = "sink"
    loop.trigger_match = "match"
    loop.end_match = "end"
    loop.save_artifact = "save"

    assert flow.roles == {role.name: role}
    assert flow.role_manager is loop.role_manager
    assert (flow.trigger_match, flow.end_match, flow.hint_sink) == (
        "match", "end", "sink"
    )
    assert flow.save_artifact == "save"

    # …and the two the dashboard poller reads every second.
    flow.log.append({"user": "щось", "agent": None})
    flow.finishing = True
    assert loop._role_log == [{"user": "щось", "agent": None}]
    assert loop.role_finishing is True
    assert loop._role_ended_ts == flow.ended_ts


async def test_the_built_engine_actually_has_a_role_flow(tmp_path, monkeypatch) -> None:
    """A refactor once left every role inert in production: the policy
    object existed, the wiring set its fields, and nobody adopted it —
    so the conductor never asked. Only a check on the assembled engine
    catches that; the unit tests all passed."""
    from types import SimpleNamespace

    import src.spine.main as spine_main

    settings = SimpleNamespace(
        db_path=tmp_path / "h.db",
        workspace_dir=tmp_path / "ws",
        groq_api_key="x",
        groq_language="uk",
        deepseek_api_key="sk-test",
        deepseek_base_url="",
        deepseek_model="",
        identity_file=tmp_path / "identity.json",
        wake_word="гава",
        wake_window_seconds=45.0,
        wake_required=True,
        spine_vad_stop_ms=800,
        spine_turn_continuation_hold_seconds=2.6,
    )

    loop = await spine_main._build_loop(
        settings, audio=None, voice="", hold_s=1.3, full=True
    )
    try:
        assert loop.role_flow is not None, "the engine was built without a role flow"
        assert loop.roles, "roles must be loaded from the repo's roles/ folder"
        # And the conductor can reach a session through it.
        assert loop.role_flow.in_session is False
    finally:
        await spine_main._close_loop(loop)
