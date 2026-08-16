"""Tests for src/spine/tools.py — no network, fakes and a temp DB only."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from src.agent.jobs import DONE, FAILED, INTERRUPTED, RUNNING
from src.memory.base import MemoryEntry, MemoryType
from src.spine.tools import (
    McpHands,
    SpineActionLog,
    VoiceToolbox,
    make_hands_factory,
    open_spine_records,
)


class FakeHands:
    """Records start/set_delivery/cancel_all calls. No real work happens."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.started: list[str] = []
        self.delivery = None
        self.cancel_calls = 0
        self.cancel_return = 2

    def set_delivery(self, deliver) -> None:
        self.delivery = deliver

    def start(self, task: str) -> None:
        self.started.append(task)

    def cancel_all(self) -> int:
        self.cancel_calls += 1
        return self.cancel_return


class BrokenHands(FakeHands):
    """A Hands whose start() blows up, to prove execute() never raises."""

    def start(self, task: str) -> None:
        raise RuntimeError("boom")


class FakeMemory:
    """Fake SQLiteBackend-compatible memory: store/search only."""

    def __init__(self, search_results: list[MemoryEntry] | None = None) -> None:
        self.stored: list[MemoryEntry] = []
        self.search_calls: list[tuple[str, int]] = []
        self._search_results = search_results or []

    async def store(self, entry: MemoryEntry) -> str:
        self.stored.append(entry)
        return entry.id or "fake-id"

    async def search(
        self, query: str, *, limit: int = 5, types: list | None = None
    ) -> list[MemoryEntry]:
        self.search_calls.append((query, limit))
        return self._search_results[:limit]


class BrokenMemory(FakeMemory):
    async def store(self, entry: MemoryEntry) -> str:
        raise RuntimeError("db is on fire")

    async def search(
        self, query: str, *, limit: int = 5, types: list | None = None
    ) -> list[MemoryEntry]:
        raise RuntimeError("db is on fire")


def _entry(content: str, id_: str = "id1") -> MemoryEntry:
    return MemoryEntry(id=id_, type=MemoryType.FACT, content=content)


async def _deliver(text: str) -> None:
    pass


def _make_toolbox(
    memory: Any = None, deliver=None, hands_cls: type = FakeHands
) -> tuple[VoiceToolbox, FakeHands]:
    holder: dict[str, FakeHands] = {}

    def factory(settings: Any) -> FakeHands:
        h = hands_cls(settings)
        holder["hands"] = h
        return h

    toolbox = VoiceToolbox(
        settings=object(),
        memory=memory if memory is not None else FakeMemory(),
        deliver=deliver or _deliver,
        hands_factory=factory,
    )
    return toolbox, holder["hands"]


# -- schemas -------------------------------------------------------------


def test_schemas_are_exactly_three_named_tools():
    toolbox, _ = _make_toolbox()
    schemas = toolbox.schemas
    assert len(schemas) == 3
    names = {s["function"]["name"] for s in schemas}
    assert names == {"delegate", "remember", "recall"}


def test_schemas_are_valid_json_schema_shape():
    toolbox, _ = _make_toolbox()
    for schema in toolbox.schemas:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert isinstance(fn["name"], str) and fn["name"]
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict) and params["properties"]
        assert isinstance(params["required"], list)
        for prop_name in params["required"]:
            assert prop_name in params["properties"]


# -- delegate --------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_starts_hands_and_returns_ukrainian_ack():
    toolbox, hands = _make_toolbox()
    reply = await toolbox.execute("delegate", {"task": "прибери робочий стіл"})
    assert hands.started == ["прибери робочий стіл"]
    assert reply == "Прийнято, роблю."


@pytest.mark.asyncio
async def test_delegate_does_not_await_job_completion():
    """start() on the fake is synchronous and returns at once; execute()
    must not block on anything beyond it."""
    toolbox, hands = _make_toolbox()
    reply = await toolbox.execute("delegate", {"task": "довга робота"})
    # If execute() were waiting on job completion, hands.delivery would
    # already have been invoked with a result. It was only wired, never
    # called, by delegate itself.
    assert hands.started == ["довга робота"]
    assert reply == "Прийнято, роблю."


@pytest.mark.asyncio
async def test_delegate_empty_task_is_refused_without_starting_hands():
    toolbox, hands = _make_toolbox()
    reply = await toolbox.execute("delegate", {"task": "   "})
    assert hands.started == []
    assert reply
    assert reply != "Прийнято, роблю."


@pytest.mark.asyncio
async def test_delegate_missing_task_key_is_refused():
    toolbox, hands = _make_toolbox()
    reply = await toolbox.execute("delegate", {})
    assert hands.started == []
    assert isinstance(reply, str) and reply


# -- remember --------------------------------------------------------------


@pytest.mark.asyncio
async def test_remember_stores_content_and_confirms():
    memory = FakeMemory()
    toolbox, _ = _make_toolbox(memory=memory)
    reply = await toolbox.execute(
        "remember", {"type": "fact", "content": "любить каву без цукру"}
    )
    assert len(memory.stored) == 1
    stored = memory.stored[0]
    assert stored.content == "любить каву без цукру"
    assert stored.type == MemoryType.FACT
    assert isinstance(reply, str) and reply


@pytest.mark.asyncio
async def test_remember_empty_content_is_refused_without_storing():
    memory = FakeMemory()
    toolbox, _ = _make_toolbox(memory=memory)
    reply = await toolbox.execute("remember", {"type": "fact", "content": ""})
    assert memory.stored == []
    assert isinstance(reply, str) and reply


@pytest.mark.asyncio
async def test_remember_falls_back_to_fact_type_on_bad_type():
    memory = FakeMemory()
    toolbox, _ = _make_toolbox(memory=memory)
    await toolbox.execute("remember", {"type": "nonsense", "content": "щось"})
    assert memory.stored[0].type == MemoryType.FACT


# -- recall ------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_formats_top_results_as_prose():
    memory = FakeMemory(
        search_results=[
            _entry("живе у Львові", "id1"),
            _entry("любить каву", "id2"),
            _entry("програміст", "id3"),
            _entry("не має значення, зайвий", "id4"),
        ]
    )
    toolbox, _ = _make_toolbox(memory=memory)
    reply = await toolbox.execute("recall", {"query": "де живе"})
    assert memory.search_calls == [("де живе", 3)]
    assert "живе у Львові" in reply
    assert "любить каву" in reply
    assert "програміст" in reply
    assert "не має значення, зайвий" not in reply


@pytest.mark.asyncio
async def test_recall_zero_results():
    memory = FakeMemory(search_results=[])
    toolbox, _ = _make_toolbox(memory=memory)
    reply = await toolbox.execute("recall", {"query": "щось невідоме"})
    assert reply == "Нічого не знайшов."


@pytest.mark.asyncio
async def test_recall_empty_query_is_refused():
    memory = FakeMemory()
    toolbox, _ = _make_toolbox(memory=memory)
    reply = await toolbox.execute("recall", {"query": ""})
    assert memory.search_calls == []
    assert isinstance(reply, str) and reply


# -- unknown tool / error handling -----------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_returns_refusal_not_exception():
    toolbox, _ = _make_toolbox()
    reply = await toolbox.execute("launch_missiles", {})
    assert isinstance(reply, str) and reply


@pytest.mark.asyncio
async def test_execute_never_raises_when_hands_start_throws():
    toolbox, _ = _make_toolbox(hands_cls=BrokenHands)
    reply = await toolbox.execute("delegate", {"task": "будь-що"})
    assert isinstance(reply, str) and reply


@pytest.mark.asyncio
async def test_execute_never_raises_when_memory_store_throws():
    toolbox, _ = _make_toolbox(memory=BrokenMemory())
    reply = await toolbox.execute("remember", {"type": "fact", "content": "щось"})
    assert isinstance(reply, str) and reply


@pytest.mark.asyncio
async def test_execute_never_raises_when_memory_search_throws():
    toolbox, _ = _make_toolbox(memory=BrokenMemory())
    reply = await toolbox.execute("recall", {"query": "щось"})
    assert isinstance(reply, str) and reply


# -- wiring ------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_delivery_wired_with_deliver_fn():
    async def deliver(text: str) -> None:
        pass

    toolbox, hands = _make_toolbox(deliver=deliver)
    assert hands.delivery is deliver


def test_cancel_all_delegates_to_hands():
    toolbox, hands = _make_toolbox()
    result = toolbox.cancel_all()
    assert hands.cancel_calls == 1
    assert result == hands.cancel_return


# -- delegated work that leaves a trace ------------------------------------
#
# The worker the spine builds used to be given nothing to write to: no
# jobs store, no action log, no way to say "still working". A restart
# mid-job left the user waiting for an answer that could never arrive,
# and the activity feed could show what was said but never what was done.


class StubWorker(McpHands):
    """A real McpHands with the model replaced by a stub ``_loop``.

    Everything under test — the jobs rows, the action rows, the beacon —
    lives in ``Hands._run`` / ``_execute`` around that one call, so this
    exercises the real code path without a network.
    """

    def __init__(self, settings: Any, **kwargs: Any) -> None:
        self._answer = kwargs.pop("answer", "готово")
        self._explode = kwargs.pop("explode", None)
        self._entered: asyncio.Event = kwargs.pop("entered", None) or asyncio.Event()
        self._hold = kwargs.pop("hold", 0.0)
        super().__init__(settings, **kwargs)

    async def _loop(self, task: str, job_id: int | None = None) -> str:
        self._entered.set()
        if self._hold:
            await asyncio.sleep(self._hold)
        if self._explode is not None:
            raise self._explode
        return self._answer


def _settings() -> Any:
    class _S:
        capability_install_enabled = False

    return _S()


def _worker(**kwargs: Any) -> StubWorker:
    """Built through the factory, so the factory's wiring is what is tested."""
    factory_kwargs = {
        k: kwargs.pop(k)
        for k in ("jobs", "conversation_manager", "on_long_running", "mcp_provider")
        if k in kwargs
    }
    factory = make_hands_factory(**factory_kwargs)
    worker = factory(_settings())
    # Same collaborators, stub model.
    return StubWorker(
        _settings(),
        jobs=worker._jobs,
        conversation_manager=worker._conversation_manager,
        mcp_provider=worker._mcp_provider,
        on_long_running=worker._on_long_running,
        **kwargs,
    )


async def _actions(db) -> list[dict]:
    cursor = await db.execute(
        "SELECT intent_id, tool, args, status, result_json FROM actions "
        "WHERE intent_id IS NOT NULL ORDER BY id"
    )
    rows = await cursor.fetchall()
    return [
        {
            "intent_id": r[0],
            "tool": r[1],
            "args": r[2],
            "status": r[3],
            "result": json.loads(r[4]) if r[4] else None,
        }
        for r in rows
    ]


# -- the jobs table ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_job_started_and_finished_writes_through_the_injected_store(tmp_path):
    records = await open_spine_records(tmp_path / "heare.db")
    delivered: list[str] = []

    async def deliver(text: str) -> None:
        delivered.append(text)

    worker = _worker(jobs=records.jobs, answer="на диску 40 гігабайт")
    worker.set_delivery(deliver)
    await worker._run("подивись скільки місця на диску", "місця диску")

    jobs = await records.jobs.recent()
    assert len(jobs) == 1
    assert jobs[0].task == "подивись скільки місця на диску"
    assert jobs[0].label == "місця диску"
    assert jobs[0].state == DONE
    assert jobs[0].result == "на диску 40 гігабайт"
    assert delivered and "на диску 40 гігабайт" in delivered[0]
    await records.close()


@pytest.mark.asyncio
async def test_a_job_that_fails_still_leaves_a_row_saying_why(tmp_path):
    records = await open_spine_records(tmp_path / "heare.db")
    worker = _worker(jobs=records.jobs, explode=RuntimeError("диск не відповідає"))
    worker.set_delivery(_deliver)
    await worker._run("подивись диск", "диск")

    jobs = await records.jobs.recent()
    assert jobs[0].state == FAILED
    assert "диск не відповідає" in (jobs[0].error or "")
    await records.close()


@pytest.mark.asyncio
async def test_a_job_cut_off_mid_flight_is_found_by_a_later_sweep(tmp_path):
    """The restart case: a row exists while the work is still running, so
    the next process can say what was interrupted instead of nothing."""
    records = await open_spine_records(tmp_path / "heare.db")
    entered = asyncio.Event()
    worker = _worker(jobs=records.jobs, entered=entered, hold=30.0)
    worker.set_delivery(_deliver)

    task = asyncio.create_task(worker._run("довга робота", "довга робота"))
    await asyncio.wait_for(entered.wait(), 2.0)
    # Give _job_start's write a chance to land before we look.
    for _ in range(50):
        if await records.jobs.recent():
            break
        await asyncio.sleep(0.01)

    running = await records.jobs.recent()
    assert running and running[0].state == RUNNING

    stranded = await records.jobs.sweep_interrupted()
    assert [j.task for j in stranded] == ["довга робота"]
    assert (await records.jobs.recent())[0].state == INTERRUPTED

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await records.close()


@pytest.mark.asyncio
async def test_a_reopened_store_sweeps_what_the_last_run_left_running(tmp_path):
    db_path = tmp_path / "heare.db"
    first = await open_spine_records(db_path)
    await first.jobs.start("перевір пошту", "перевір пошту")
    await first.close()

    second = await open_spine_records(db_path)
    assert [j.task for j in second.stranded] == ["перевір пошту"]
    assert (await second.jobs.recent())[0].state == INTERRUPTED
    await second.close()


# -- the actions table ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tool_call_records_pending_then_result(tmp_path, monkeypatch):
    records = await open_spine_records(tmp_path / "heare.db")

    async def _fake_direct(name, args, settings):
        # Mid-call the row must already exist, and say "pending".
        await records.actions.flush()
        rows = await _actions(records.db)
        assert [r["status"] for r in rows] == ["pending"]
        assert rows[0]["tool"] == "bash"
        return {"success": True, "output": "40G вільно"}

    monkeypatch.setattr("src.agent.tools.direct.execute_direct", _fake_direct)

    worker = _worker(conversation_manager=records.actions)
    out = await worker._execute("bash", {"command": "df -h"})
    assert out == "40G вільно"

    await records.actions.flush()
    rows = await _actions(records.db)
    assert len(rows) == 1, "one intent, one row"
    assert rows[0]["status"] == "done"
    assert rows[0]["tool"] == "bash"
    assert "df -h" in rows[0]["args"]
    assert rows[0]["result"] == {"summary": "40G вільно"}
    await records.close()


@pytest.mark.asyncio
async def test_a_failing_tool_records_the_error_on_the_same_row(tmp_path, monkeypatch):
    records = await open_spine_records(tmp_path / "heare.db")

    async def _fake_direct(name, args, settings):
        raise RuntimeError("не вийшло")

    monkeypatch.setattr("src.agent.tools.direct.execute_direct", _fake_direct)

    worker = _worker(conversation_manager=records.actions)
    out = await worker._execute("bash", {"command": "df -h"})
    assert "failed" in out

    await records.actions.flush()
    rows = await _actions(records.db)
    assert len(rows) == 1
    assert rows[0]["status"] == "error"
    assert rows[0]["tool"] == "bash"
    assert "не вийшло" in rows[0]["result"]["error"]
    await records.close()


@pytest.mark.asyncio
async def test_an_mcp_call_is_recorded_the_same_way(tmp_path):
    records = await open_spine_records(tmp_path / "heare.db")

    class _Bridge:
        async def call(self, name: str, arguments: dict) -> dict:
            return {"success": True, "output": "прочитав"}

    worker = _worker(
        conversation_manager=records.actions, mcp_provider=lambda: _Bridge()
    )
    out = await worker._execute("mcp__files__read_file", {"path": "/tmp/a"})
    assert out == "прочитав"

    await records.actions.flush()
    rows = await _actions(records.db)
    assert len(rows) == 1
    assert rows[0]["tool"] == "mcp__files__read_file"
    assert rows[0]["status"] == "done"
    await records.close()


@pytest.mark.asyncio
async def test_a_broken_action_log_never_reaches_the_tool_call(tmp_path, monkeypatch):
    class _BrokenDB:
        async def execute(self, *args):
            raise RuntimeError("db is on fire")

        async def commit(self):
            raise RuntimeError("db is on fire")

    async def _fake_direct(name, args, settings):
        return {"success": True, "output": "ok"}

    monkeypatch.setattr("src.agent.tools.direct.execute_direct", _fake_direct)
    log = SpineActionLog(_BrokenDB())
    worker = _worker(conversation_manager=log)
    assert await worker._execute("bash", {"command": "true"}) == "ok"
    await log.flush()


# -- nothing injected: today's behaviour ------------------------------------


@pytest.mark.asyncio
async def test_with_no_collaborators_the_worker_behaves_exactly_as_before(
    tmp_path, monkeypatch
):
    async def _fake_direct(name, args, settings):
        return {"success": True, "output": "ok"}

    monkeypatch.setattr("src.agent.tools.direct.execute_direct", _fake_direct)

    delivered: list[str] = []

    async def deliver(text: str) -> None:
        delivered.append(text)

    worker = _worker(answer="зробив")
    worker.set_delivery(deliver)

    assert worker._jobs is None
    assert worker._conversation_manager is None
    assert worker._on_long_running is None
    # The recorders no-op rather than raising, exactly as before.
    assert worker._record_pending("bash", "{}") is None
    worker._record_result(None, "ok")
    worker._record_error(None, "boom")

    assert await worker._execute("bash", {"command": "true"}) == "ok"
    await worker._run("щось зроби", "щось зроби")
    assert delivered and "зробив" in delivered[0]


def test_the_factory_still_builds_a_gated_mcp_capable_worker():
    state = object()
    bridge = object()
    worker = make_hands_factory(
        session_state=state, mcp_provider=lambda: bridge
    )(_settings())
    assert isinstance(worker, McpHands)
    assert worker._session_state is state
    assert worker._mcp_provider() is bridge
    assert worker._jobs is None and worker._conversation_manager is None


# -- the progress beacon ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_long_job_calls_the_progress_seam_with_its_label(monkeypatch):
    monkeypatch.setattr("src.spine.tools.BEACON_INTERVAL_S", 0.01)
    seen: list[str] = []

    worker = _worker(on_long_running=seen.append, hold=0.1, answer="все")
    worker.set_delivery(_deliver)
    await worker._run("довга робота", "довга робота")

    assert seen and set(seen) == {"довга робота"}


@pytest.mark.asyncio
async def test_the_progress_seam_may_be_async(monkeypatch):
    monkeypatch.setattr("src.spine.tools.BEACON_INTERVAL_S", 0.01)
    seen: list[str] = []

    async def on_long_running(label: str) -> None:
        seen.append(label)

    worker = _worker(on_long_running=on_long_running, hold=0.1)
    worker.set_delivery(_deliver)
    await worker._run("довга робота", "довга робота")
    assert seen == ["довга робота"] or len(seen) > 1


@pytest.mark.asyncio
async def test_a_broken_progress_seam_does_not_stop_the_job(monkeypatch):
    monkeypatch.setattr("src.spine.tools.BEACON_INTERVAL_S", 0.01)
    delivered: list[str] = []

    async def deliver(text: str) -> None:
        delivered.append(text)

    def boom(label: str) -> None:
        raise RuntimeError("no channel")

    worker = _worker(on_long_running=boom, hold=0.05, answer="все одно зробив")
    worker.set_delivery(deliver)
    await worker._run("довга робота", "довга робота")
    assert delivered and "все одно зробив" in delivered[0]


@pytest.mark.asyncio
async def test_without_a_seam_the_beacon_is_the_daemons_own(monkeypatch):
    """No callback = today's behaviour: Hands._beacon, which finds no
    indication facade on the spine and stays silent."""
    monkeypatch.setattr("src.spine.tools.BEACON_INTERVAL_S", 0.01)
    worker = _worker(hold=0.05, answer="тихо")
    worker.set_delivery(_deliver)
    await worker._run("робота", "робота")
