"""English understanding, and Ukrainian left as it was.

The words differ per language; the decisions must not. These tests read
``heare.language.en`` directly (and once through ``for_language``) so they
never depend on the default setting, which stays Ukrainian.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from heare.core import Task
from heare.core import i18n
from heare.language import en, for_language, uk
from heare.language import promises_en
from heare.model.judge import Judge
from heare.prompt import persona


@pytest.mark.parametrize(
    "text",
    ["yeah", "Yeah!", "uh-huh", "uh huh", "ok", "Okay.", "mhm", "right",
     "Right!", "got it", "Got it!", "yep", "mm"],
)
def test_english_backchannels_mean_go_on(text: str) -> None:
    assert en.is_backchannel(text) is True


@pytest.mark.parametrize(
    "text",
    ["no", "Nope!", "not now", "stop", "enough", "quiet", "what?",
     "tell me more about that", "", "   ", "***"],
)
def test_english_backchannels_reject_commands_and_negations(text: str) -> None:
    assert en.is_backchannel(text) is False


@pytest.mark.parametrize(
    "text", ["stop", "Stop!", "enough", "Enough.", "quiet", "Quiet!", "no", "No!"],
)
def test_english_stop_words_cancel(text: str) -> None:
    assert en.is_stop_word(text) is True


@pytest.mark.parametrize("text", ["yeah", "ok", "hello", "", "   "])
def test_english_non_stops_are_not_stop_words(text: str) -> None:
    assert en.is_stop_word(text) is False


@pytest.mark.parametrize(
    ("turn", "halt"),
    [
        ("Stop.", True),
        ("Doka, stop", True),
        ("Stop, stop.", True),
        ("stop everything now", True),
        ("Stop the music", False),
        ("Cancel the flight", False),
        ("no", False),
        ("", False),
    ],
)
def test_english_a_stop_is_the_whole_turn(turn: str, halt: bool) -> None:
    assert en.is_halt(turn, frozenset({"doka"})) is halt


@pytest.mark.parametrize(
    ("said", "answer"),
    [
        ("Yeah, go ahead", True),
        ("yes please", True),
        ("Sure.", True),
        ("Of course.", True),
        ("Doka, yes", True),
        ("No thanks", False),
        ("No.", False),
        ("maybe later", None),
        ("Yes, and also check the mail", None),
    ],
)
def test_english_consent_is_yes_no_or_neither(said: str, answer: bool | None) -> None:
    assert en.consent(said, frozenset({"doka"})) is answer


@pytest.mark.parametrize(
    "text",
    ["What did I say about the weather?", "Do you remember the file?",
     "Remind me tomorrow", "Yesterday it rained"],
)
def test_english_recall_cues(text: str) -> None:
    assert en.recall_cue(text) is True


@pytest.mark.parametrize("text", ["hello", "What time is it?", "open the panel"])
def test_english_ordinary_speech_is_not_a_recall_cue(text: str) -> None:
    assert en.recall_cue(text) is False


@pytest.mark.parametrize(
    "reply",
    ["I'll check that now.", "Looking into it.", "On it.", "Let me see.",
     "One second.", "I'll find it."],
)
def test_english_promises_of_work(reply: str) -> None:
    assert en.promises_work(reply) is True


@pytest.mark.parametrize(
    "reply",
    ["Today is Friday.", "I checked: Chrome eats the most.",
     "I'll check this tomorrow morning, and until then the likeliest cause "
     "is a setting in the network, because everything else works as it "
     "should and nothing here is waiting on me."],
)
def test_english_answers_are_not_promises(reply: str) -> None:
    assert en.promises_work(reply) is False


@pytest.mark.parametrize(
    "reply",
    ["Done.", "Updated the panel.", "Added a note.", "Here it is.",
     "Created the file.", "Fixed the tests."],
)
def test_english_claims_of_done_work(reply: str) -> None:
    assert en.claims_done(reply) is True


def test_english_ordinary_answers_are_not_claims() -> None:
    assert en.claims_done("The weather is nice.") is False


def test_english_promises_are_their_own_tables() -> None:
    assert en.promises_work is promises_en.promises_work
    assert en.claims_done is promises_en.claims_done


def test_the_english_decide_table() -> None:
    words = for_language("en")
    assert words.decide("yeah", speech_ms=0) == "resume"
    assert words.decide("stop", speech_ms=0) == "cancel"
    assert words.decide("hello", speech_ms=0) == "interrupt"
    assert words.decide("yeah", speech_ms=words.INTERRUPT_HOLD_MS) == "interrupt"
    assert words.decide("", speech_ms=0) == "resume"
    assert words.tokens("I'll check the panel now") == [
        "i'll", "check", "the", "panel", "now"]


def test_english_exposes_every_name_the_core_uses() -> None:
    for name in uk.__all__:
        assert hasattr(en, name), name


def test_ukrainian_still_understands_its_own_words() -> None:
    assert uk.is_backchannel("Ага, ага")
    assert uk.is_stop_word("Досить!")
    assert uk.is_halt("Дока, стоп", frozenset({"дока"}))
    assert uk.consent("Та можна, можна.") is True
    assert uk.recall_cue("Що я казав про це?")
    assert uk.promises_work("Гляну, секунду.")
    assert uk.claims_done("Оновив погоду — тепер там і вологість")


def test_the_room_judge_follows_the_chosen_language(monkeypatch) -> None:
    i18n.choose(lambda: "uk")
    assert "Чутно мене чи ні?" in persona.JUDGE

    i18n.choose(lambda: "en")
    try:
        assert "Can you hear me?" in persona.JUDGE
        assert "Чутно мене чи ні?" not in persona.JUDGE
    finally:
        i18n.choose(lambda: "uk")


async def test_the_prompt_example_is_the_persons_word(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(persona, "IDENTITY", tmp_path / "none.json")

    i18n.choose(lambda: "uk")
    ukrainian = await persona.prompt()
    assert "«екран»" in ukrainian

    i18n.choose(lambda: "en")
    try:
        english = await persona.prompt()
        assert "«screen»" in english
        assert "«екран»" not in english
    finally:
        i18n.choose(lambda: "uk")


async def test_the_gate_shows_the_owners_words_in_their_language() -> None:
    systems: list[str] = []

    async def ask(system: str, user: str) -> str:
        systems.append(system)
        return '{"verdict": "allow"}'

    task = Task(1, "x", "opencode")
    await Judge(SimpleNamespace(language="en"), ask=ask).decide(task, [], "a", "a")
    assert "tidy up the duplicate resumes" in systems[-1]
    assert "прибери" not in systems[-1]

    await Judge(SimpleNamespace(language="uk"), ask=ask).decide(task, [], "a", "a")
    assert "прибери дублікати резюме" in systems[-1]
