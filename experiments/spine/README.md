# spine — can the audio front end stand without pipecat?

## The hypothesis

> On built-in speakers, no headphones, we can hold a turn without hearing
> ourselves and be interrupted mid-sentence, using only
> `pywebrtc_audio.AudioProcessor` — no silero, no onnxruntime, no pipecat.

Everything else in the spine is HTTP calls and `if` statements. This is the
only part where a rewrite can actually fail, so it goes first.

## Why this is worth testing

`AudioProcessor` runs echo cancellation, noise suppression, AGC and the
high-pass filter in one pass and leaves the VAD estimate in
`speech_probability`. It is 780 KB and already installed — heare uses it
today for AEC only.

If the hypothesis holds, one call per 10 ms frame replaces four pipeline
stages and 94 MB of ML runtime (`onnxruntime` + `sympy`, pulled for silero
VAD alone).

## Running it

```
uv run python experiments/spine/echo_probe.py --check     # no mic opened
uv run python experiments/spine/echo_probe.py             # AEC on
uv run python experiments/spine/echo_probe.py --no-aec    # control
```

Speakers, not headphones — headphones make it pass for the wrong reason.
It repeats back whatever you say. Ctrl-C prints the verdict.

**Run the control.** A clean run with AEC proves nothing on its own: it may
only mean the microphone is quiet or the room is dead. The control run
*should* show self-hearing. If both runs are clean, the test is not
measuring what it claims to.

Things worth doing during a run: talk over it while it is speaking; let it
speak a long sentence and stay silent; say "стоп" mid-utterance.

## The three numbers

| | pass |
|---|---|
| `heard itself` | 0 with AEC on, non-zero in the control |
| `barge-in stop` | under ~200 ms |
| `speech end → audio` | no worse than the current pipeline |

Every frame decision lands in `probe.jsonl`, so the verdict can be
re-derived rather than remembered.

## How it fails

- **It transcribes its own voice.** The reference (`far`) signal is wrong —
  what AEC is told we are playing does not match what the speaker emits.
  Try `--delay-ms` (default 30); the room and the device both add latency.
  This is the trap the whole probe exists to catch.
- **Barge-in is slow.** Cancellation is a `deque.clear()`, so slowness here
  means frames are queued somewhere else — most likely the device buffer.
- **Round trip is worse than today.** Then this direction is not worth
  taking, whatever it saves in dependencies.

## What it deliberately does not do

No LLM, no tools, no context, no journal beyond the raw event log. Adding
them would make a failure ambiguous, and the point is to get an unambiguous
answer for 300 lines.

Notably, it does **not** gate the microphone while speaking. Gating would
hide self-hearing behind a mute, which is what the current pipeline does
with `interrupt_toggle_gate`. Here the microphone stays open precisely so
the echo canceller has to earn it.
