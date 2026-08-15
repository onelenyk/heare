"""The junk filter, tested against this deployment's own corpus.

Every phrase in the "must drop" table is a real row from heare.db, with
its real frequency in the comment — these are the ghosts that filled
meeting transcripts. Every phrase in the "must keep" table is something
a person actually said to the assistant.
"""
from __future__ import annotations

import pytest

from src.spine.hallucinations import is_junk


@pytest.mark.parametrize(
    "text",
    [
        "*тиша*",              # 77 rows
        "Дякую за перегляд!",  # 40 rows
        "Музика",              # 8 rows
        "Будьласка,бро.",      # 45 rows — glued, no spaces
        "Явsilentрежимі,такийчайтисобіобговорюєте.",  # 4 rows
        "[музика]",
        "(applause)",
        "Спасибо за просмотр!",
        "Редактор субтитров А.Семкин",
        "Продовження далі...",
        "...",
        "   ",
        "",
        "***",
    ],
)
def test_never_speech_is_dropped(text: str) -> None:
    assert is_junk(text) is True
    # Even right after the assistant spoke, these are not answers.
    assert is_junk(text, agent_spoke_recently=True) is True


@pytest.mark.parametrize(
    "text",
    [
        "Дока, привіт!",
        "Скільки вільного місця на диску?",
        "Дока. Подивись, будь ласка.",
        "почни мітинг",
        "Що ти вмієш робити?",
        "домовились: релізимо в понеділок",
        "музика грає надто гучно, зроби тихіше",  # the word, not the marker
        "дякую, а тепер перевір диск",           # courtesy plus a request
    ],
)
def test_real_speech_survives(text: str) -> None:
    assert is_junk(text) is False
    assert is_junk(text, agent_spoke_recently=True) is False


@pytest.mark.parametrize("text", ["Дякую.", "Дякую!", "Спасибі", "Ага", "Угу", "Хм..."])
def test_courtesy_depends_on_whether_anyone_spoke(text: str) -> None:
    """«Дякую.» is 398 rows — mostly Whisper answering silence, but a
    real thank-you when it follows a real answer."""
    assert is_junk(text, agent_spoke_recently=False) is True
    assert is_junk(text, agent_spoke_recently=True) is False


def test_short_glued_words_are_kept() -> None:
    """The no-space rule must not eat ordinary single words."""
    for word in ("привіт", "стоп", "продовжуй", "розкажи"):
        assert is_junk(word, agent_spoke_recently=True) is False


@pytest.mark.parametrize("text", ["287,000.", "12:30", "3,14", "о 9:45 зустріч"])
def test_numbers_and_times_are_not_glued_junk(text: str) -> None:
    """The welded-words rule looks for letters on both sides of the
    punctuation — «287,000.» is a real thing to say."""
    assert is_junk(text, agent_spoke_recently=True) is False


@pytest.mark.parametrize(
    "text",
    [
        "Будьласка,бро.Завждиназв'язку.",
        "Зрозумів.ВідкриваюLofiGirlубраузері.",
        "Явsilentрежимі—тількислухаю.",
    ],
)
def test_echo_of_the_assistants_own_glued_speech_is_dropped(text: str) -> None:
    """These rows are the assistant's own voice heard back by the mic,
    welded by the chunk-scrubbing bug that used to eat inter-word spaces.
    They must never re-enter the conversation as user turns."""
    assert is_junk(text, agent_spoke_recently=True) is True
