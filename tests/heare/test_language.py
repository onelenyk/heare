"""What the person meant, kept out of the core: recall cues."""

from __future__ import annotations

from heare.language import uk


def test_a_request_to_remember_is_a_recall_cue():
    assert uk.recall_cue("Нагадай, що я казав про це")
    assert uk.recall_cue("Ти пам'ятаєш той файл?")
    assert uk.recall_cue("Що я говорив учора?")


def test_ordinary_speech_is_not_a_recall_cue():
    assert not uk.recall_cue("Привіт")
    assert not uk.recall_cue("Котра година?")


def test_a_past_time_word_is_a_deliberate_broad_cue():
    # «Вчора був дощ» is a statement, but the time word is a cue by design:
    # it is cheap and gives the model what it remembers about yesterday.
    assert uk.recall_cue("Вчора був дощ")
