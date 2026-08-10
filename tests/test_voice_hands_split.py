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

import pytest

from src.agent.tools import system
from src.config import Settings


@pytest.fixture(autouse=True)
def _restore_flag():
    """The switch is module-level; never leak it into another test."""
    before = system.is_voice_only()
    yield
    system.set_voice_only(before)


# ── what the conversational model can see ─────────────────────────────


def test_off_by_default() -> None:
    """Measured failure: the model announced work it never delegated, so
    nothing ran and no answer could arrive. Opt in, do not opt out."""
    assert Settings().voice_agent_enabled is False


def test_all_tools_visible_when_the_split_is_off() -> None:
    system.set_voice_only(False)
    visible = {t.name for t in system._visible_tools()}
    assert len(visible) > 50
    assert "bash" in visible and "delegate" in visible


def test_three_verbs_when_the_split_is_on() -> None:
    system.set_voice_only(True)
    assert {t.name for t in system._visible_tools()} == system.VOICE_TOOLS


def test_delegate_exists_and_is_one_of_the_verbs() -> None:
    names = {t.name for t in system.TOOLS if t.enabled}
    assert "delegate" in names
    assert system.VOICE_TOOLS == {"delegate", "remember", "recall"}


def test_schema_follows_the_switch() -> None:
    system.set_voice_only(True)
    schema = system.build_tools_schema()
    assert {s.name for s in schema.standard_tools} == system.VOICE_TOOLS


def test_registration_follows_the_switch() -> None:
    class FakeLLM:
        def __init__(self):
            self.registered: list[str] = []

        def register_function(self, name, handler, **kw):
            self.registered.append(name)

    system.set_voice_only(True)
    llm = FakeLLM()
    names = system.register_all_tools(llm, settings=Settings())

    assert set(names) == system.VOICE_TOOLS
    assert set(llm.registered) == system.VOICE_TOOLS


# ── the prompt must not describe tools that are not there ─────────────


def test_catalog_lists_three_verbs_under_the_split() -> None:
    from src.agent.llm import prompt_sections

    system.set_voice_only(True)
    catalog = prompt_sections._render_tool_catalog()

    assert "delegate" in catalog
    assert "bash" not in catalog
    assert "web_search" not in catalog


def test_hard_constraints_make_delegation_an_obligation() -> None:
    """The observed failure was linguistic, not technical.

    "Say one short sentence that you are on it, then stop" is satisfied
    perfectly without calling anything — and stated near the end of a
    4000-token prompt, that is what the model did.
    """
    from src.agent.llm import prompt_sections

    system.set_voice_only(True)
    rules = prompt_sections._render_hard_constraints("uk")

    assert "delegate" in rules
    first_line = rules.splitlines()[1]
    assert "delegate" in first_line, "the rule has to come first, not last"

    system.set_voice_only(False)
    assert "delegate" not in prompt_sections._render_hard_constraints("uk")


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

        async def fake_loop(task):
            return "команда завершилась"

        hands._loop = fake_loop  # type: ignore[method-assign]
        await hands._run("do it")

    asyncio.run(drive())
    assert delivered == [f"{RESULT_PREFIX} команда завершилась"]


def test_a_crashing_job_still_answers() -> None:
    """Silence is the one outcome that must be impossible."""
    from src.agent.hands import Hands

    delivered: list[str] = []

    async def deliver(text: str) -> None:
        delivered.append(text)

    async def drive():
        hands = Hands(Settings())
        hands.set_delivery(deliver)

        async def boom(task):
            raise RuntimeError("provider exploded")

        hands._loop = boom  # type: ignore[method-assign]
        await hands._run("do it")

    asyncio.run(drive())
    assert len(delivered) == 1
    assert "provider exploded" in delivered[0]
