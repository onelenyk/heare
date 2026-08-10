# Echo cancellation never worked

Diagnosed and fixed 2026-08-10. Working implementation: `src/core/aec.py`.

## The symptom

Barge-in was impossible. Speaking over the assistant did nothing at all —
not "sometimes", not "unreliably": nothing. Alongside it, the assistant
appeared never to hear itself, which was read for months as evidence that
echo suppression worked.

Both observations had one cause. It was not discriminating between the
user and its own voice; it was **deaf while speaking**.

## How it was found

Two instruments, both worth keeping:

- **`src/core/audio_probe.py`** — taps the microphone path at named
  points and logs the peak level once a second, split by whether the bot
  was speaking. Insert two and the difference names the guilty stage.
- **`DelayEstimator` in `src/core/aec.py`** — cross-correlates what was
  sent to the speaker against what the microphone heard, giving the real
  acoustic delay instead of a guess.

Both run under `uv run python -m src.core.main --probe-audio`.

The first measurement ended the guessing:

```
raw       peak(bot talking) =  -28.4 dB
post-aec  peak(bot talking) = -120.0 dB
```

The microphone had signal. After the echo canceller it was digital zero.
Not attenuation — annihilation.

## Four bugs, each fatal on its own

### 1. Scale — the one that mattered

`pywebrtc_audio.AudioProcessor.process()` takes floats in `[-1, 1]` and
clips anything beyond. It was handed int16-scaled values (±32768):

```python
mic = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float32)
cleaned = ap.process(mic, ref)      # every sample clipped to ±1.0
```

Verified directly with a 300 Hz tone:

```
float [-1,1]    in=0.1000   out=0.0995   ratio 0.995
int16-scaled    in=3276.7   out=1.0000   ratio 0.000
```

The output is a signal of amplitude 1 out of 32768 — about −90 dB. That
is the "mute". Fix: divide by 32768 going in, multiply back coming out.

### 2. The reference was a second stale

```python
ref = self._echo_state.get_buffer()   # one second, oldest first
cleaned = ap.process(mic[:n], ref[:n])   # ...so this is the oldest 10 ms
```

`get_buffer()` returns the ring in chronological order. Slicing from the
front hands AEC3 audio from a second ago as "what the speaker is playing
now". Fix: `ref[-n:]`.

### 3. It only ran while the bot spoke

```python
if isinstance(frame, InputAudioRawFrame) and self._is_active():
```

AEC3 is adaptive: it needs an uninterrupted stream at a fixed block size
to converge its filter and estimate the delay. Started from scratch on
every utterance, fed variable-length frames, it never gets there and
falls back to suppressing everything.

Fix: process **every** microphone frame, always, in exact 160-sample
(10 ms) blocks, with zeros as the reference when nothing is playing.

### 4. The reference was tapped at the wrong end of the pipeline

The collector sat *before* `transport.output()`, so it received audio at
the rate TTS generated it, not the rate the speaker played it. Measured
queue depth:

```
reference queue 2796 ms
reference queue 4620 ms     <- at the cap; oldest samples silently dropped
reference queue    0 ms     <- empty, while the speaker was still playing
```

Mid-utterance the queue ran seconds deep and overflowed (a `deque` with
`maxlen` drops from the left, which is exactly what is about to be
played). In the gaps between sentences it drained to empty, so AEC was
handed silence precisely while the echo was arriving.

Fix: tap *after* `transport.output()`, where frames arrive at the rate
they are written to the device. Also raised the queue cap so nothing is
dropped.

### And the delay setting

`stream_delay_ms` was 30. Measured on this machine: **~125 ms**.

## Result

```
                  before            after
reference queue   0 ↔ 4600 ms       0–160 ms, stable
measured delay    29–352 ms         124–134 ms, confidence 0.25–0.44
suppression       6–40 dB (~20)     40–50 dB
```

Barge-in works. No bot phrases return through the microphone, and the
Whisper silence-hallucinations that fed the loop ("Дякую.", "Thank you.")
are gone with the residual that caused them.

## The correlation gate

`src/pipeline/stages/echo_gate.py` drops any microphone frame whose
correlation with recent bot output exceeds 0.15. On built-in speakers
correlation runs 0.55–0.78, so it logged:

```
[ECHO GATE] passed=0 dropped=100 rate=100.0%
```

Every frame, dropped whole. A second mute in series with the first. It is
off by default in `src/core/` — with cancellation actually working it has
nothing left to do.

## Rolled into the main pipeline

`build_pipeline` now uses this implementation. The changes:

```
build.py       aec_filter moved BEFORE echo_gate — the gate must judge
               the residual, not raw microphone audio
build.py       far_collector appended AFTER transport.output()
build.py       VADUserTurnStartStrategy(enable_interruptions=True)
config.py      echo_gate_enabled       True  -> False
config.py      echo_classifier_enabled True  -> False
config.py      aec_stream_delay_ms      30   -> 120  (measured)
```

`src/pipeline/stages/webrtc_aec_filter.py` was deleted rather than fixed:
once nothing imported it, keeping a module that destroys audio — with a
green test suite certifying its behaviour — is an invitation to wire it
back in.

`experiments/spine/echo_probe.py` still has the scale bug, which is one
more reason that probe's results meant nothing.

Regression cover: `tests/test_core_aec.py` (the scale bug has its own
test, asserting that unit-scaled audio survives and int16-scaled audio
does not) and `tests/test_audio_path_order.py` (every position in the
chain that decides whether interruption is possible).

## How to diagnose this again

Run with `--probe-audio` and read three numbers:

1. **reference queue** — should sit near zero, steady. Seconds deep or
   oscillating to zero means the reference is not tracking playback.
2. **measured vs configured delay** — should agree within ~20 ms.
3. **suppression** — 40 dB or better. Under 20 dB, or wildly varying,
   means the filter is not converging.
