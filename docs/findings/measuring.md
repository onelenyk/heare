# Instruments

Until 2026-08-10 the only instrument in this project was a pair of ears,
one session at a time. That is why a scale-overflow bug that destroyed
every audio sample survived for months: it presented as normal behaviour.

Three instruments now exist. They matter more than the code they were
built to debug — the code may be rewritten; the inability to tell whether
something works is the actual problem.

## 1. The text harness — everything except acoustics

Two of them, the same instrument pointed at either pipeline:

```
uv run python -m src.core.harness "скажи котра година"           # the small core
uv run python -m src.core.harness --single "..."                 # one-agent control
uv run python -m src.core.harness --interject "..." --interject-at 5
uv run python -m src.core.harness --window 30 "..."

uv run python -m src.pipeline.harness "..."                      # the full daemon
uv run python -m src.pipeline.harness --window 30 --interject "стоп"
```

`src/pipeline/harness.py` builds the daemon exactly as `src/main.py`
does — every stage, all 63 tools, modes, persistence — with `audio=False`
so no device opens. It was the first end-to-end measurement of the
daemon that has ever been taken:

```
                 core      full daemon
first token      808 ms      1488 ms
first audio     1351 ms      3204 ms
```

Builds the real pipeline with the audio devices disabled, injects a user
message with `run_llm=True`, and watches what comes out with two probe
stages — one before TTS for text, one after for audio.

Reports per turn: first token, first audio, the start of each distinct
utterance, total audio bytes, which tools ran, and what was said.

```
  “Виконай команду sleep 15 і скажи мені коли вона завершиться.”
    first token   2415 ms
    first audio   3523 ms
    utterances    3  at 3523 ms, 7418 ms, 21854 ms
    tools         delegate, bash
```

What it proves: the model answers, tools run, TTS produces audio, and how
long each takes. What it cannot prove: anything acoustic.

## 2. Audio level taps — where the sound is lost

```
uv run python -m src.core.main --probe-audio
```

`src/core/audio_probe.py` logs the peak level at named points once a
second, split by whether the bot was speaking:

```
raw       frames= 51  peak(bot silent)=-120.0 dB  peak(bot talking)= -28.4 dB
post-aec  frames= 51  peak(bot silent)=-120.0 dB  peak(bot talking)=-120.0 dB
post-gate frames= 51  peak(bot silent)=-120.0 dB  peak(bot talking)=-120.0 dB
```

Three lines like that name the guilty stage in one second of reading. A
stage that outputs −120 dB while the input is −28 dB is not filtering,
it is deleting.

The `bot silent` / `bot talking` split is what makes it useful: a mute
that only engages during playback is invisible to any measurement that
does not separate the two.

## 3. Delay and suppression — is the canceller converging

Also under `--probe-audio`, from `DelayEstimator` in `src/core/aec.py`:

```
aec: delay measured 128 ms (conf 0.31) vs configured 120 ms
     — suppression 44.8 dB, reference queue 140 ms
```

Three numbers, each answering a specific failure:

| number | healthy | what it means when it is not |
|---|---|---|
| reference queue | near zero, steady | the reference is not tracking playback |
| measured vs configured delay | within ~20 ms | AEC is looking for the echo in the wrong place |
| suppression | 40 dB or better | the adaptive filter is not converging |

The delay is measured by cross-correlating what was sent against what the
microphone heard — no guessing, no sweeping through values by ear.

## 4. A simulated room — the acoustic half, without a room

```
uv run python -m src.pipeline.room                  # the default scenario
uv run python -m src.pipeline.room --echo -6        # a louder room
uv run python -m src.pipeline.room --delay-ms 250   # other hardware
```

`src/pipeline/room.py` puts a microphone made of arithmetic where the
device would be:

    mic = scripted speech
        + echo_gain × (what the daemon played, delayed)
        + a noise floor

Speech is synthesized with edge-tts in a different voice from the
assistant's — identical voices would make "did it hear itself"
unanswerable. Frames are generated from inside the chain, one 20 ms
frame every 20 ms, and an utterance can be scheduled `at="mid_speech"`:
wait until the assistant is speaking, then cut in. Interrupting at a
repeatable moment is the one thing a person in a room cannot do twice.

```
── room: echo -10 dB, delay 120 ms, noise -60 dBFS ──
   21.65 s  ♪ бот         заговорив
   22.05 s  ▶ сказано     Стоп, зачекай.
   22.41 s  ♪ бот         замовк
   22.55 s  ⚡ переривання                                503 ms
   24.00 s  ◀ почуто      Стоп, зачекай.
──────────────────────────────────────────────────────
  почув себе          0
  перебивання         503 ms
  кадрів: подано 1403, пройшло STT 1401, VAD спрацював 2×
PASS
```

Those three counters matter more than they look. "Nothing was heard" has
three causes — frames never fed, VAD never fired, STT never returned —
and they are indistinguishable from the outcome. Both bugs found while
building this were found by reading them:

- **`mute_mic` survives restarts.** A session that ended muted silently
  mutes every later run: `input_mute_gate` drops the audio before STT
  and the only symptom is silence. Almost certainly why the microphone
  appeared dead for a whole day of live testing.
- **Audio queued at the task source never enters the chain** when the
  transport is disabled — the switched-off input transport drops it.

## What is still missing

Reverberation, speaker distortion, the microphone's own gain control.
A passing simulation does not promise the hardware works; a failing one
promises it does not. The last mile still wants ten minutes of talking
to it.

Metrics from the pipeline are collected and never read. `enable_metrics`
is on; nothing consumes the frames.
