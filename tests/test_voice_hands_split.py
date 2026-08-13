"""The voice/hands split, and the ways it was observed to fail.

Same model, same key. The split is temporal: one call must answer within
a second and sees three verbs; the other has no deadline and sees all of
them.

Measured on this machine through ``src/pipeline/harness.py``:

    echo (fast tool)   single 3784 ms to first audio, split 5157 ms
    sleep 12 (slow)    split speaks at 2927 ms and answers at 18120 ms

So the split costs about a second when the work is quick and saves the
whole wait when it is not.
"""

from __future__ import annotations

import asyncio

from src.agent.tools import system
from src.config import Settings


# ── what the conversational model can see ─────────────────────────────


def test_the_voice_agent_sees_three_verbs() -> None:
    assert {t.name for t in system._visible_tools()} == system.VOICE_TOOLS


def test_delegate_exists_and_is_one_of_the_verbs() -> None:
    names = {t.name for t in system.TOOLS if t.enabled}
    assert "delegate" in names
    assert system.VOICE_TOOLS == {"delegate", "remember", "recall"}


def test_the_schema_is_the_three_verbs() -> None:
    schema = system.build_tools_schema()
    assert {s.name for s in schema.standard_tools} == system.VOICE_TOOLS


def test_only_the_verbs_are_registered() -> None:
    class FakeLLM:
        def __init__(self):
            self.registered: list[str] = []

        def register_function(self, name, handler, **kw):
            self.registered.append(name)

    llm = FakeLLM()
    names = system.register_all_tools(llm, settings=Settings())

    assert set(names) == system.VOICE_TOOLS
    assert set(llm.registered) == system.VOICE_TOOLS


# ── the prompt must not describe tools that are not there ─────────────


def test_the_catalog_lists_only_the_verbs() -> None:
    from src.agent.llm import prompt_sections

    catalog = prompt_sections._render_tool_catalog()

    assert "delegate" in catalog
    assert "bash" not in catalog
    assert "web_search" not in catalog


def test_the_prompt_drops_machinery_the_voice_agent_cannot_reach() -> None:
    """Skills it cannot run, agents it cannot spawn, a four-call budget it
    cannot spend: 58% of the prompt, all of it instructions about someone
    else's job. The observed cost was not tokens but confusion — the
    model announced work it never delegated.
    """
    from src.agent.llm.context_injector import render_native_system_prompt

    trimmed = render_native_system_prompt(
        persona="You are an assistant.", context=None, language="uk"
    )

    assert len(trimmed) < 4000, (
        f"the prompt used to be 7453 characters; now {len(trimmed)}"
    )
    for gone in ("### Capabilities", "run_skill", "Background agents"):
        assert gone not in trimmed

    # What survives is what still applies.
    for kept in ("HARD CONSTRAINTS", "delegate", "Response length"):
        assert kept in trimmed


def test_hard_constraints_make_delegation_an_obligation() -> None:
    """The observed failure was linguistic, not technical.

    "Say one short sentence that you are on it, then stop" is satisfied
    perfectly without calling anything — and stated near the end of a
    4000-token prompt, that is what the model did.
    """
    from src.agent.llm import prompt_sections

    rules = prompt_sections._render_hard_constraints("uk")

    assert "delegate" in rules
    first_line = rules.splitlines()[1]
    assert "delegate" in first_line, "the rule has to come first, not last"


# ── delegate must not block, and must not lie ─────────────────────────


def test_delegate_returns_before_the_work_finishes() -> None:
    """The entire point: the speaking path never waits."""
    from src.agent.hands import execute_delegate, set_hands

    started: list[str] = []
    release = asyncio.Event()

    class SlowHands:
        def start(self, task: str) -> None:
            started.append(task)

            async def job():
                await release.wait()

            asyncio.get_event_loop().create_task(job())

    async def drive():
        set_hands(SlowHands())
        try:
            result = await asyncio.wait_for(
                execute_delegate("count to a million"), timeout=0.5
            )
        finally:
            release.set()
            set_hands(None)
        return result

    result = asyncio.run(drive())
    assert result["success"] is True
    assert started == ["count to a million"]


def test_delegate_says_so_when_there_is_no_worker() -> None:
    from src.agent.hands import execute_delegate, set_hands

    set_hands(None)
    result = asyncio.run(execute_delegate("do something"))
    assert result["success"] is False
    assert "worker" in result["error"]


def test_delegate_rejects_an_empty_task() -> None:
    from src.agent.hands import execute_delegate

    result = asyncio.run(execute_delegate("   "))
    assert result["success"] is False


# ── hands ─────────────────────────────────────────────────────────────


def test_hands_runs_tools_through_the_pipeline_path(monkeypatch) -> None:
    """Delegating changes who calls a tool, never what the tool does."""
    from src.agent import hands as hands_mod

    calls: list[tuple[str, str]] = []

    async def fake_execute_direct(tool, args, settings=None):
        calls.append((tool, args))
        return {"success": True, "output": "привіт"}

    monkeypatch.setattr(
        "src.agent.tools.direct.execute_direct", fake_execute_direct
    )

    hands = hands_mod.Hands(Settings())
    out = asyncio.run(hands._execute("bash", {"command": "echo привіт"}))

    assert out == "привіт"
    # The bash serializer passes the bare command, not JSON — the same
    # transformation the pipeline's own handler applies.
    assert calls == [("bash", "echo привіт")]


def test_hands_reports_a_failed_tool_instead_of_raising(monkeypatch) -> None:
    from src.agent import hands as hands_mod

    async def failing(tool, args, settings=None):
        return {"success": False, "error": "no such file"}

    monkeypatch.setattr("src.agent.tools.direct.execute_direct", failing)

    out = asyncio.run(hands_mod.Hands(Settings())._execute("read", {"path": "/nope"}))
    assert "no such file" in out


def test_hands_never_offers_delegate_to_itself() -> None:
    """Otherwise a worker can hand its own job back and spin."""
    from src.agent.hands import Hands

    names = {s["function"]["name"] for s in Hands(Settings())._tool_schemas()}
    assert "delegate" not in names
    assert "bash" in names


def test_hands_hides_install_tools_while_the_gate_is_closed() -> None:
    """With capability_install_enabled off the installer refuses anyway;
    keeping the schemas would make the worker offer what it must refuse."""
    from src.agent.hands import Hands
    from src.agent.tools.capability_index import INSTALL_TOOLS

    closed = Settings()
    assert not closed.capability_install_enabled
    names = {s["function"]["name"] for s in Hands(closed)._tool_schemas()}
    assert not (INSTALL_TOOLS & names), INSTALL_TOOLS & names

    opened = Settings(capability_install_enabled=True)
    open_names = {s["function"]["name"] for s in Hands(opened)._tool_schemas()}
    assert INSTALL_TOOLS & open_names == INSTALL_TOOLS


def test_hands_still_honours_the_mode_profile() -> None:
    """Modes used to gate the conversational model's tools. With the work
    moved to the worker, skipping the check here would turn every mode
    into "allow everything" — a security control lost to a refactor."""
    from src.agent.hands import Hands

    from src.agent.modes import resolve

    class SessionState:
        profile = resolve("meeting")

    names = {
        s["function"]["name"]
        for s in Hands(Settings(), session_state=SessionState())._tool_schemas()
    }
    from src.agent.modes import is_tool_allowed

    denied = {
        t.name
        for t in system.TOOLS
        if t.enabled and not is_tool_allowed(SessionState.profile, t.name)
    }
    assert denied, "the silent profile is expected to deny something"
    assert not (denied & names), f"worker offered denied tools: {denied & names}"


def test_result_is_delivered_as_a_marked_user_turn() -> None:
    """It re-enters the conversation so the voice agent phrases it — in
    the right language, in its own voice, through the existing TTS."""
    from src.agent.hands import RESULT_PREFIX, Hands

    delivered: list[str] = []

    async def deliver(text: str) -> None:
        delivered.append(text)

    async def drive():
        hands = Hands(Settings())
        hands.set_delivery(deliver)

        async def fake_loop(task, job_id=None):
            return "команда завершилась"

        hands._loop = fake_loop  # type: ignore[method-assign]
        await hands._run("do it", "робота")

    asyncio.run(drive())
    assert delivered == [f"{RESULT_PREFIX} команда завершилась"]
    # An instruction, not a label. As a bare tag a small model read it as
    # more input and said "зараз гляну" a second time while holding the
    # answer it had just been given.
    assert "перекажи" in delivered[0]
    assert "команда завершилась" in delivered[0]


def test_a_crashing_job_still_answers() -> None:
    """Silence is the one outcome that must be impossible."""
    from src.agent.hands import Hands

    delivered: list[str] = []

    async def deliver(text: str) -> None:
        delivered.append(text)

    async def drive():
        hands = Hands(Settings())
        hands.set_delivery(deliver)

        async def boom(task, job_id=None):
            raise RuntimeError("provider exploded")

        hands._loop = boom  # type: ignore[method-assign]
        await hands._run("do it", "робота")

    asyncio.run(drive())
    assert len(delivered) == 1
    assert "provider exploded" in delivered[0]


def test_delegated_work_lands_in_the_action_log(monkeypatch) -> None:
    """The action log lives in the pipeline's handler wrapper, which the
    worker does not go through. Without this the table goes back to being
    empty — the state that made the assistant's own work invisible."""
    from src.agent import hands as hands_mod

    recorded: list[tuple] = []

    class Manager:
        def record_action_pending(self, intent_id, tool, args):
            recorded.append(("pending", tool, args))

        def record_action_result(self, intent_id, summary, items=None):
            recorded.append(("result", summary))

        def record_action_error(self, intent_id, error):
            recorded.append(("error", error))

    async def ok(tool, args, settings=None):
        return {"success": True, "output": "привіт"}

    monkeypatch.setattr("src.agent.tools.direct.execute_direct", ok)

    hands = hands_mod.Hands(Settings(), conversation_manager=Manager())
    asyncio.run(hands._execute("bash", {"command": "echo привіт"}))

    assert recorded == [("pending", "bash", "echo привіт"), ("result", "привіт")]


def test_a_failed_tool_is_recorded_as_an_error(monkeypatch) -> None:
    from src.agent import hands as hands_mod

    recorded: list[tuple] = []

    class Manager:
        def record_action_pending(self, intent_id, tool, args):
            recorded.append(("pending", tool))

        def record_action_result(self, intent_id, summary, items=None):
            recorded.append(("result", summary))

        def record_action_error(self, intent_id, error):
            recorded.append(("error", error))

    async def boom(tool, args, settings=None):
        return {"success": False, "error": "no such file"}

    monkeypatch.setattr("src.agent.tools.direct.execute_direct", boom)

    hands = hands_mod.Hands(Settings(), conversation_manager=Manager())
    out = asyncio.run(hands._execute("read", {"path": "/nope"}))

    assert "no such file" in out
    assert ("error", "no such file") in recorded


# ── the interaction ───────────────────────────────────────────────────


def test_the_worker_is_given_the_conversation() -> None:
    """Without it the worker sees one sentence, and the voice agent has to
    restate everything it knows — which it does badly, inventing
    requirements as it goes."""
    from src.agent.hands import Hands

    turns = [
        {"role": "user", "content": "подивись файл notes.txt"},
        {"role": "assistant", "content": "гляну"},
    ]
    hands = Hands(Settings(), recent_turns=lambda: turns)
    block = hands._conversation()

    assert "notes.txt" in block
    assert "User:" in block and "Assistant:" in block


def test_a_missing_conversation_is_not_fatal() -> None:
    from src.agent.hands import Hands

    def boom() -> list[dict]:
        raise RuntimeError("context gone")

    assert Hands(Settings(), recent_turns=boom)._conversation() == ""
    assert Hands(Settings())._conversation() == ""


def test_stop_cancels_work_in_flight() -> None:
    """Otherwise the assistant falls silent when interrupted and then
    announces the result of work the user just called off."""
    from src.agent.hands import Hands

    async def drive():
        hands = Hands(Settings())
        started = asyncio.Event()

        async def forever(task, job_id=None):
            started.set()
            await asyncio.sleep(60)

        hands._loop = forever  # type: ignore[method-assign]
        hands.start("щось довге")
        await started.wait()
        assert hands.busy == 1

        cancelled = hands.cancel_all()
        await asyncio.sleep(0)
        return cancelled

    assert asyncio.run(drive()) == 1


def test_results_are_labelled_only_when_several_jobs_are_in_flight() -> None:
    """Naming a single result is noise; two unlabelled results arriving
    out of order are indistinguishable."""
    from src.agent.hands import RESULT_PREFIX, Hands, _label

    delivered: list[str] = []

    async def deliver(text: str) -> None:
        delivered.append(text)

    async def drive():
        hands = Hands(Settings())
        hands.set_delivery(deliver)

        async def quick(task, job_id=None):
            return "готово"

        hands._loop = quick  # type: ignore[method-assign]
        await hands._run("подивись диск", _label("подивись диск"))

    asyncio.run(drive())
    assert delivered == [f"{RESULT_PREFIX} готово"]


def test_a_label_is_a_few_words_of_the_task() -> None:
    from src.agent.hands import _label

    assert _label("подивись скільки місця на диску").startswith("подивись")
    assert len(_label("x" * 200)) <= 40
    assert _label("") == "робота"


def test_a_job_is_recorded_from_start_to_finish() -> None:
    """A job that leaves no trace cannot be reported after a restart, and
    the assistant cannot say what it did an hour ago."""
    from src.agent import jobs as jobs_mod
    from src.agent.hands import Hands

    events: list[tuple] = []

    class Store:
        async def start(self, label, task):
            events.append(("start", label, task))
            return 7

        async def step(self, job_id, text):
            events.append(("step", job_id, text))

        async def finish(self, job_id, state, *, result=None, error=None):
            events.append(("finish", job_id, state, result or error))

    async def drive():
        hands = Hands(Settings(), jobs=Store())
        hands.set_delivery(lambda text: asyncio.sleep(0))

        async def quick(task, job_id=None):
            await hands._job_step(job_id, "bash(df -h)")
            return "240 ГБ вільно"

        hands._loop = quick  # type: ignore[method-assign]
        await hands._run("подивись диск", "подивись диск")

    asyncio.run(drive())

    assert events[0] == ("start", "подивись диск", "подивись диск")
    assert ("step", 7, "bash(df -h)") in events
    assert events[-1] == ("finish", 7, jobs_mod.DONE, "240 ГБ вільно")


def test_a_cancelled_job_is_recorded_as_cancelled() -> None:
    from src.agent import jobs as jobs_mod
    from src.agent.hands import Hands

    states: list[str] = []

    class Store:
        async def start(self, label, task):
            return 1

        async def step(self, job_id, text):
            pass

        async def finish(self, job_id, state, *, result=None, error=None):
            states.append(state)

    async def drive():
        hands = Hands(Settings(), jobs=Store())
        started = asyncio.Event()

        async def forever(task, job_id=None):
            started.set()
            await asyncio.sleep(60)

        hands._loop = forever  # type: ignore[method-assign]
        hands.start("довге завдання")
        await started.wait()
        hands.cancel_all()
        await asyncio.sleep(0.05)

    asyncio.run(drive())
    assert states == [jobs_mod.CANCELLED]


def test_the_progress_beacon_is_a_sound_not_a_sentence() -> None:
    """Speaking costs a model turn and can cut across the user. The point
    is only to tell "still working" from "stuck" — what a spinner does."""
    import inspect

    from src.agent.hands import PROGRESS_AFTER, Hands

    source = inspect.getsource(Hands._beacon)
    assert "ACTION_LONG_RUNNING" in source
    assert "_deliver" not in source, "a beacon must not speak"
    assert PROGRESS_AFTER >= 5, "too eager a beacon is worse than none"


def test_the_worker_answers_in_the_language_being_spoken() -> None:
    """Observed: the worker replied in English, and the voice agent — told
    to speak only Ukrainian — ignored the result entirely and repeated its
    acknowledgement while holding the answer."""
    from src.agent.hands import SYSTEM, Hands

    class LanguageState:
        language = "uk"

    class SessionState:
        language_state = LanguageState()

    assert Hands(Settings(), session_state=SessionState())._language() == "Ukrainian"
    assert Hands(Settings())._language() == "Ukrainian", "default, not a crash"

    class English:
        language = "en"

    class EnglishSession:
        language_state = English()

    hands = Hands(Settings(), session_state=EnglishSession())
    assert hands._language() == "English"
    assert "English" in SYSTEM.format(language=hands._language())
