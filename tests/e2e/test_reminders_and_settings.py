"""Reminders speak when due, and settings live-reload without restart.

A person schedules a reminder via voice ("Дока, нагадай за 10 хвилин випити
води"). The engine holds it and speaks the text when the due time arrives
(if the person is present and quiet). Settings like model and provider are
read fresh on each turn, so a change to the dashboard takes effect at once.

This suite watches the reminders path (due time, presence/quiet checks,
model-prompted speech) and the settings path (model switch, provider
fallback without key).
"""

from __future__ import annotations

import time

import pytest

from tests.e2e.room import Says, close_room, open_room

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.e2e_text]


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


# ── reminders: due time and voice ─────────────────────────────────────


async def test_reminder_is_scheduled_and_spoken_at_due_time(room) -> None:
    """Set a reminder "за 10 хвилин"; 5 minutes later it is silent; at
    11 minutes it is spoken (person present, quiet).

    What a person notices: The assistant acks the request; later, it
    speaks the reminder text when the time comes. The reminder is not
    forgotten mid-test.
    """
    now = time.time()
    room.will_say(
        Says(calls=(("remind", {"minutes": 10, "text": "випити води"}),)),
        "Добре, нагадаю за 10 хвилин.",
    )

    # Set the reminder
    reply = await room.told("Дока, нагадай за 10 хвилин випити води")
    assert reply == "Добре, нагадаю за 10 хвилин."

    # 5 minutes later: nothing yet
    verdict_at_5 = await room.tick(now=now + 5 * 60)
    assert verdict_at_5.speak is False, "reminder not yet due"

    # 11 minutes later: spoken to the person if quiet
    room.will_say("випити води")
    await room.tick(now=now + 11 * 60)
    # The tick queues the reminder through loop.inject, which starts
    # a turn. Wait for the turn to complete and be written to the database.
    await room.drained()
    spoken = room.said()
    assert "води" in spoken


async def test_reminder_is_not_spoken_while_person_is_talking(room) -> None:
    """When the due time arrives and the person is talking, the reminder
    waits. Once they stop, it is spoken.

    What a person notices: The assistant does not interrupt their sentence
    with a reminder — it waits for a breath, then says it.
    """
    now = time.time()
    room.will_say(
        Says(calls=(("remind", {"minutes": 5, "text": "тест"}),)),
        "Добре, нагадаю за 5 хвилин.",
    )

    reply = await room.told("Дока, нагадай за 5 хвилин тест")
    assert reply == "Добре, нагадаю за 5 хвилин."

    # Due time arrives, but the person is talking
    room.is_talking(True)
    verdict = await room.tick(now=now + 5 * 60 + 10)
    assert verdict.speak is False, (
        "guard should block speaking mid-conversation"
    )

    # Person stops talking
    room.is_talking(False)
    room.will_say("тест")
    verdict = await room.tick(now=now + 5 * 60 + 11)
    # The engine should speak now
    await room.drained()
    spoken = room.said()
    assert "тест" in spoken, "reminder should be spoken once person stops"


# ── settings live-reload ───────────────────────────────────────────────


async def test_model_switch_takes_effect_next_turn(room) -> None:
    """Change the model on settings mid-run: the next turn's config
    carries the new model.

    What a person notices: They can switch the model in the dashboard and
    the assistant will use it on the next thing they say — no restart
    needed.
    """
    # First turn with the default model
    room.will_say("Привіт.")
    await room.told("привіт")
    first_config = room.configs[-1]
    assert first_config.model is not None

    # Change the model in settings
    new_model = "deepseek-reasoner"
    room.settings.deepseek_model = new_model

    # Second turn: the config should use the new model
    room.will_say("Як справи.")
    await room.told("як справи?")
    second_config = room.configs[-1]
    assert second_config.model == new_model, (
        f"model should have changed from {first_config.model} to {new_model}"
    )


async def test_missing_provider_key_falls_back_without_crash(room) -> None:
    """Set llm_provider to a provider that has no key: the turn still
    completes, and resolve_llm logs a warning and uses a fallback provider.

    What a person notices: They accidentally set an unconfigured provider,
    but the assistant still answers. They see a warning in the logs.
    """
    # The room gives deepseek a key. Set the provider to one that has no key.
    room.settings.llm_provider = "groq"  # No groq key in the test room
    room.settings.groq_api_key = None
    room.will_say("Привіт, я змінився.")
    await room.told("привіт")

    # The turn should complete
    spoken = room.said()
    assert spoken, "turn should complete with fallback"
    # The config should use a fallback (deepseek, which has a key)
    config_used = room.configs[-1]
    assert config_used.provider != "groq", (
        "should have fallen back from groq"
    )
    assert config_used.provider == "deepseek", "deepseek should be the fallback"
