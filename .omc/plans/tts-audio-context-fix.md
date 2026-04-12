# TTS Audio Not Playing — Root Cause & Fix

## Problem

Гава produces decisions with `type=speak` and valid Ukrainian text replies,
but no audio comes out of the speaker. The dashboard shows decisions being
made, but the user hears silence.

## Root Cause

`DeciderProcessor` pushes `TextFrame(reply)` into the pipeline, but
Pipecat 0.0.108's `TTSService` base class only processes `TTSSpeakFrame`
instances. When a `TextFrame` arrives, TTSService either ignores it or
processes it without creating a proper audio context. The result is that
`run_tts()` produces valid PCM audio frames, but the base class rejects
them with:

```
EdgeTTSService#0 unable to append audio to context <uuid>
```

This message appears dozens of times per speak attempt because each PCM
chunk is rejected individually.

## Fix

In `src/decider.py`, change every `push_frame(TextFrame(...))` to
`push_frame(TTSSpeakFrame(...))`:

```python
# Before (broken):
from pipecat.frames.frames import TextFrame
await self.push_frame(TextFrame(reply))

# After (fixed):
from pipecat.frames.frames import TTSSpeakFrame
await self.push_frame(TTSSpeakFrame(reply))
```

Locations in `_build_decider_processor_class` that push text to TTS:
1. `_handle_listening` — when decision type is "speak" (the reply)
2. `_handle_listening` — when decision type is "act" (the confirmation question)
3. `_handle_confirmation` — "Скажи: так чи ні?" re-prompt
4. `_cancel_pending` — "nevermind, cancelled" / "okay"
5. `_execute_pending` — action result summary / error message
6. `on_heartbeat_tick` — heartbeat-triggered speech

Also update `_load_pipecat_base()` to import `TTSSpeakFrame` instead of
(or in addition to) `TextFrame`.

## Secondary Fixes Applied in This Session

1. **MP3 → PCM transcode** (`src/tts_edge.py`): edge-tts returns MP3 bytes
   but `LocalAudioOutputTransport` expects raw PCM (s16le). Added
   `_mp3_to_pcm_s16le()` that pipes through ffmpeg. Requires ffmpeg on PATH.

2. **TTSSettings initialization**: Pipecat 0.0.108 validates that `model`,
   `voice`, `language` are set in `TTSSettings`. Pass them explicitly:
   `TTSSettings(model=None, voice=voice, language=Language.UK)`.

3. **`run_tts` signature**: Pipecat 0.0.108 calls `run_tts(text, context_id)`
   not `run_tts(text)`. Add `context_id: str | None = None` parameter.

## Verification

After the TTSSpeakFrame fix:
1. `uv run pytest tests/` should still pass (tests mock push_frame)
2. Restart daemon: `HEARE_HEARTBEAT_MIN=2 uv run python -m src.main start`
3. Say "Гава, привіт" — should see `edge-tts produced N MP3 bytes -> M PCM bytes`
   in daemon.log AND hear Ukrainian audio from the speaker
4. No more "unable to append audio to context" messages in the log
