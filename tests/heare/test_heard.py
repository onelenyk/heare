"""What the ear heard is kept beside what it was taken for."""

from __future__ import annotations

import json
import struct
import wave

from heare.channels.voice import channel
from heare.channels.voice.channel import Voice
from heare.channels.voice.heard import Heard
from heare.channels.voice.stt import Transcript

# Half a second loud enough to count as speech.
LOUD = struct.pack("<h", 8000) * 8000


def test_a_phrase_is_a_sound_and_what_it_was_taken_for(tmp_path):
    heard = Heard(tmp_path)
    path = heard.add(LOUD, {"text": "та можна"})

    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, 8000)
    meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["text"] == "та можна"
    assert meta["ms"] == 500


def test_the_oldest_go_as_new_ones_come(tmp_path):
    heard = Heard(tmp_path, keep=2)
    for word in ("раз", "два", "три"):
        heard.add(LOUD, {"text": word})
    kept = sorted(tmp_path.glob("*.json"))
    assert [json.loads(p.read_text(encoding="utf-8"))["text"] for p in kept] == ["два", "три"]
    assert len(list(tmp_path.glob("*.wav"))) == 2


class _Settings:
    def __init__(self, **values):
        self.values = values

    def __getattr__(self, name):
        return self.__dict__["values"].get(name, "")

    def set(self, name, value):
        self.values[name] = value


async def _recognised(monkeypatch, text):
    async def transcribe(pcm, **kw):
        return Transcript(text=text, language="uk")

    monkeypatch.setattr(channel, "transcribe", transcribe)


async def test_what_is_recognised_is_kept_and_a_switch_stops_it(tmp_path, monkeypatch):
    await _recognised(monkeypatch, "Та можна, можна.")
    settings = _Settings()
    voice = Voice(object(), settings, heard=Heard(tmp_path))

    assert await voice._text_of(LOUD) == "Та можна, можна."
    (meta,) = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.glob("*.json")]
    assert meta["text"] == "Та можна, можна." and meta["dropped"] is None

    voice.set_control("keep_heard", False)
    assert voice.controls["keep_heard"] is False
    await voice._text_of(LOUD)
    assert len(list(tmp_path.glob("*.wav"))) == 1


async def test_an_invented_phrase_is_kept_too_and_marked(tmp_path, monkeypatch):
    await _recognised(monkeypatch, "Дякую за перегляд!")
    voice = Voice(object(), _Settings(), heard=Heard(tmp_path))

    assert await voice._text_of(LOUD) == ""
    (meta,) = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.glob("*.json")]
    assert meta["dropped"] == "invented"


async def test_too_short_to_be_a_word_is_neither_sent_nor_kept(tmp_path, monkeypatch):
    await _recognised(monkeypatch, "щось")
    voice = Voice(object(), _Settings(), heard=Heard(tmp_path))
    assert await voice._text_of(LOUD[:3200]) == ""
    assert not list(tmp_path.glob("*"))
