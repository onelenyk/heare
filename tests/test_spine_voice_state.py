"""The voice-state file the dashboard and menubar read.

src/spine/voice_state.py is the writer half of what used to be a pipecat
frame processor in the old engine's stages. The spine calls it directly, so
it must keep the one property the readers depend on: a reader polling the
file never sees a half-written document.
"""

from __future__ import annotations

import json

from src.spine.voice_state import write_voice_state


def test_it_writes_a_readable_document(tmp_path):
    path = tmp_path / "voice_state.json"
    write_voice_state(path, "listening")

    payload = json.loads(path.read_text("utf-8"))
    assert payload["state"] == "listening"
    assert payload["last_partial"] is None
    assert payload["last_final"] is None
    assert payload["since_ts"] > 0


def test_the_last_transcript_survives_in_the_document(tmp_path):
    path = tmp_path / "voice_state.json"
    write_voice_state(path, "result", last_final="привіт", last_partial="при")

    payload = json.loads(path.read_text("utf-8"))
    assert payload["state"] == "result"
    assert payload["last_final"] == "привіт"
    assert payload["last_partial"] == "при"


def test_the_directory_is_created_when_missing(tmp_path):
    """First boot writes into ~/.heare/ before anything else has made it."""
    path = tmp_path / "nested" / "deeper" / "voice_state.json"
    write_voice_state(path, "idle")
    assert json.loads(path.read_text("utf-8"))["state"] == "idle"


def test_writes_are_atomic_so_a_reader_never_sees_half_a_document(tmp_path):
    """The replace is what makes polling safe: every read is either the
    old document or the new one, never a truncated one."""
    path = tmp_path / "voice_state.json"
    write_voice_state(path, "listening")

    for state in ("stt", "result", "listening", "idle"):
        write_voice_state(path, state)
        payload = json.loads(path.read_text("utf-8"))  # never a parse error
        assert payload["state"] == state

    # The tmpfile is not left lying around next to it.
    assert [p.name for p in tmp_path.iterdir()] == ["voice_state.json"]


def test_an_unwritable_path_is_logged_not_raised(tmp_path, caplog):
    """A dashboard file is never worth killing a conversation over."""
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")
    path = blocker / "voice_state.json"

    write_voice_state(path, "listening")  # must not raise
