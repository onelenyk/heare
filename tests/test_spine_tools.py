"""Tests for src/spine/tools.py — no network, no real DB, fakes only."""

from __future__ import annotations

from typing import Any

import pytest

from src.memory.base import MemoryEntry, MemoryType
from src.spine.tools import VoiceToolbox


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
