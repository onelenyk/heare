"""The registry of parts, asked directly: no door, no HTTP."""

from __future__ import annotations

import asyncio
import json

from heare.api import registry as registry_mod
from heare.api.registry import Registry


def _registry(tmp_path):
    told: list[tuple[str, dict]] = []
    executors: list[tuple[str, str, str, str]] = []
    registry = Registry(tmp_path / "parts.json",
                        lambda kind, **data: told.append((kind, data)),
                        lambda pid, channel, about, host: executors.append(
                            (pid, channel, about, host)))
    return registry, told, executors


async def test_a_yes_is_remembered_and_a_part_at_work_is_an_executor(tmp_path):
    registry, told, executors = _registry(tmp_path)
    registry.attach("opencode@mac", "opencode", ["executor"], "руки", "mac", "pending")
    waiting = asyncio.create_task(registry.answer("opencode@mac"))
    await asyncio.sleep(0)
    assert registry.decide("opencode@mac", "approved")
    assert await waiting == "approved"
    registry.attach("opencode@mac", "opencode", ["executor"], "руки", "mac", "approved")

    assert "opencode@mac" in registry.trusted()
    assert json.loads((tmp_path / "parts.json").read_text()) == ["opencode@mac"]
    assert executors == [("opencode@mac", "opencode", "руки", "mac")]
    assert not registry.decide("opencode@mac", "approved")  # nothing waits now


async def test_nobody_answering_is_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_mod, "APPROVAL_WAIT_S", 0.01)
    registry, _, _ = _registry(tmp_path)
    registry.attach("eyes@mac", "eyes", ["input"], "", "mac", "pending")
    assert await registry.answer("eyes@mac") == "expired"


async def test_presence_follows_the_streams(tmp_path):
    registry, told, _ = _registry(tmp_path)
    registry.attach("eyes@mac", "eyes", ["input"], "", "mac", "approved")
    registry.listens("eyes@mac")
    registry.listens("eyes@mac")
    registry.left("eyes@mac")
    assert registry.known["eyes@mac"]["state"] == "approved"
    registry.left("eyes@mac")
    assert registry.known["eyes@mac"]["state"] == "away"
    registry.listens("eyes@mac")
    assert registry.known["eyes@mac"]["state"] == "approved"
    assert [data["state"] for kind, data in told][-2:] == ["away", "approved"]


async def test_presence_follows_the_knock_and_the_stream(tmp_path):
    seen: list[tuple[str, bool]] = []
    registry = Registry(
        tmp_path / "parts.json", lambda kind, **data: None,
        on_present=lambda pid, on: seen.append((pid, on)))

    registry.attach("opencode@mac", "opencode", ["executor"], "руки", "mac", "approved")
    assert seen[-1] == ("opencode@mac", True)
    registry.listens("opencode@mac")
    assert seen[-1] == ("opencode@mac", True)
    registry.left("opencode@mac")
    assert seen[-1] == ("opencode@mac", False)


async def test_a_window_away_long_enough_leaves_but_a_manifest_part_stays(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_mod, "WINDOW_GONE_S", 0.01)
    registry, _, _ = _registry(tmp_path)
    for pid, channel in (("window@laptop-1a2b", "web"), ("eyes@mac", "eyes")):
        registry.attach(pid, channel, [], "", "mac", "approved")
        registry.listens(pid)
        registry.left(pid)
    await asyncio.sleep(0.05)
    assert "window@laptop-1a2b" not in registry.known
    assert registry.known["eyes@mac"]["state"] == "away"


async def test_a_part_hears_its_own_the_person_hears_all(tmp_path):
    registry, _, _ = _registry(tmp_path)
    registry.attach("eyes@mac", "eyes", [], "", "mac", "approved")
    registry.attach("text@mac", "text", [], "", "mac", "approved", hears="all")
    said = {"channel": "voice", "text": "мій пароль"}

    assert registry.hears(None, said)
    assert registry.hears("text@mac", said)
    assert not registry.hears("eyes@mac", said)
    assert registry.hears("eyes@mac", {"channel": "eyes"})
    assert registry.hears("eyes@mac", {"part": "eyes@mac"})
    assert not registry.hears("stranger@mac", {"channel": "eyes"})


def test_a_manifest_makes_a_part_known_and_trusted(tmp_path):
    folder = tmp_path / "parts" / "eyes"
    folder.mkdir(parents=True)
    (folder / "heare.json").write_text(json.dumps(
        {"channel": "eyes", "forms": ["input"], "key": "eyes@mac"}), encoding="utf-8")
    (tmp_path / "parts" / "broken").mkdir()
    (tmp_path / "parts" / "broken" / "heare.json").write_text("{", encoding="utf-8")
    registry, _, _ = _registry(tmp_path)
    registry.load(tmp_path / "parts")

    assert registry.known["eyes@mac"]["state"] == "away"
    assert "eyes@mac" in registry.trusted()


def test_a_version_is_kept_until_the_part_says_another(tmp_path):
    registry, _, _ = _registry(tmp_path)
    registry.attach("eyes@mac", "eyes", [], "", "mac", "approved", version="1.2.0")
    registry.attach("eyes@mac", "eyes", [], "", "mac", "away")
    assert registry.known["eyes@mac"]["version"] == "1.2.0"
    registry.attach("eyes@mac", "eyes", [], "", "mac", "approved", version="1.3.0")
    assert registry.known["eyes@mac"]["version"] == "1.3.0"


def test_a_part_that_began_again_is_counted(tmp_path):
    registry, _, _ = _registry(tmp_path)
    attach = lambda started: registry.attach(  # noqa: E731
        "eyes@mac", "eyes", ["input"], "", "mac", "approved", started=started)
    attach(100.0)
    attach(100.0)  # the same process knocking again after the stream broke
    assert registry.known["eyes@mac"]["restarts"] == 0
    attach(200.0)
    attach(300.0)
    assert registry.known["eyes@mac"]["restarts"] == 2
    assert registry.known["eyes@mac"]["started"] == 300.0
    registry.attach("eyes@mac", "eyes", ["input"], "", "mac", "approved")
    assert registry.known["eyes@mac"]["started"] == 300.0  # a manifest says nothing


def test_two_executors_of_one_channel_do_not_hear_each_other(tmp_path):
    registry, _, _ = _registry(tmp_path)
    registry.attach("native-hands@server", "native-hands", ["executor"],
                    "швидкі", "server", "approved")
    registry.attach("native-hands@mac", "native-hands", ["executor"],
                    "швидкі", "mac", "approved")

    assert registry.hears("native-hands@server", {"channel": "native-hands"}) is False
    assert registry.hears("native-hands@server",
                          {"part": "native-hands@server"}) is True
    assert registry.hears("native-hands@server",
                          {"part": "native-hands@mac"}) is False


def test_two_pages_still_share_the_web_channel(tmp_path):
    registry, _, _ = _registry(tmp_path)
    pages = ("window@laptop-1a2b", "window@desk-3c4d")
    for pid in pages:
        registry.attach(pid, "web", ["input", "output"], "", "mac", "approved")

    assert all(registry.hears(pid, {"channel": "web"}) for pid in pages)


def test_two_executors_of_one_channel_are_called_back_by_pid(tmp_path):
    calls: list[tuple[str, str, str, str]] = []
    registry = Registry(
        tmp_path / "parts.json", lambda kind, **data: None,
        lambda pid, channel, about, host: calls.append((pid, channel, about, host)))

    registry.attach("native-hands@server", "native-hands", ["executor"],
                    "швидкі", "server", "approved")
    registry.attach("native-hands@mac", "native-hands", ["executor"],
                    "швидкі", "mac", "approved")

    assert calls == [
        ("native-hands@server", "native-hands", "швидкі", "server"),
        ("native-hands@mac", "native-hands", "швидкі", "mac"),
    ]
    assert registry.executor_on("native-hands") is True
