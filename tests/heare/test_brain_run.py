"""The brain's own loops: a bad setting must not take the brain down."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time

import pytest

from heare import brain
from heare.channels.voice.wake_phrases import wake_phrases
from heare.config import Settings
from heare.core import Message, i18n
from heare.prompt import persona
from tests.heare.fakes import Page, Script


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(
        path=tmp_path / "config.toml",
        identity_file=tmp_path / "settings" / "identity.json",
        db_path=tmp_path / "heare.db",
        memory_dir=tmp_path / "memory",
        events_dir=tmp_path / "events",
        intents_path=tmp_path / "reminders.json",
        parts_dir=tmp_path / "parts",
        **overrides,
    )


async def _settle(core) -> None:
    for _ in range(500):
        await asyncio.sleep(0.01)
        if core.settled:
            return
    raise AssertionError("the core never settled")


async def test_a_language_switch_changes_words_and_judge_without_a_restart(tmp_path):
    settings = _settings(tmp_path)
    settings.set("language", "en")
    subject = brain.Brain(settings)
    model = Script()
    subject.core.model = model
    page = Page()
    subject.core.add_output(page)
    running = asyncio.create_task(subject.core.run())
    try:
        assert subject.core.words.is_halt("стоп") is False
        assert subject.core.words.consent("так") is None
        assert "not addressing you by name" in subject.core.judge

        settings.set("language", "uk")

        assert subject.core.words.is_halt("стоп") is True
        assert subject.core.words.consent("так") is True
        assert "Котра зараз година" in subject.core.judge

        subject.core.hear(Message(text="стоп", channel="page"))
        await _settle(subject.core)
        assert page.said[-1].startswith("Добре")
        assert model.asked == []

        model.will_say("SILENT")
        subject.core.hear(Message(
            text="Котра зараз година?", channel="page", addressed=False))
        await _settle(subject.core)
        assert "Котра зараз година" in model.asked[-1][0]["content"]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        subject.memory.close()
        i18n.choose(lambda: "en")


def test_a_fresh_install_has_a_name_and_its_wake_phrases(tmp_path):
    settings = _settings(tmp_path)

    subject = brain.Brain(settings)
    try:
        assert persona.name(settings.identity_file) == "Дока"
        assert "дока" in subject.core.names
        phrases = wake_phrases(settings, name=persona.name(settings.identity_file))
        assert "дока" in phrases
        assert "heare" in phrases
    finally:
        subject.memory.close()


async def test_a_bad_review_hour_does_not_kill_the_brain(tmp_path, monkeypatch, caplog):
    path = tmp_path / "config.toml"
    settings = Settings(path=path)
    with pytest.raises(ValueError):
        settings.set("review_hour", 25)
    assert settings.review_hour == 4

    path.write_text("review_hour = 25\n", encoding="utf-8")
    settings = Settings(path=path, memory_dir=tmp_path / "memory")
    noon = datetime.combine(date.today(), time(hour=12)).timestamp()
    monkeypatch.setattr(brain.clock, "time", lambda: noon)

    class Loop:
        def __init__(self) -> None:
            self.settings = settings
            self.calls = 0

        async def review_once(self):
            self.calls += 1
            return brain.ReviewRun(already=True)

    loop = Loop()
    with caplog.at_level(logging.WARNING, logger="heare.brain"):
        task = asyncio.create_task(brain.Brain._review(loop))
        await asyncio.sleep(0.05)
        assert not task.done()
        assert loop.calls >= 1
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert any("review_hour" in record.message for record in caplog.records)
