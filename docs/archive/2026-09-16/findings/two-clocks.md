# Two clocks, racing

The assistant answers half a sentence. You say

> Дока, подивись будь ласка, скільки вільного місця на диску

in one breath, and it answers twice: once to being called, once to the
question. Measured over twelve scenario runs, it happened in two of
them — and in the ones where it did not, the reason was luck.

## What actually happens

Recognition does not return words as they are spoken. It returns them
per segment, and a long segment comes back long after it started. That
one sentence arrives at the gate as:

```
 3.28 s   "Дока."
 3.87 s   "Привіт."
 6.19 s   "Скажи однім реченням,"
 7.83 s   "Як ти себе почуваєш?"
```

Four transcripts, one breath. The gap between the first and the third is
two and a half seconds — none of it a pause in the room. It is the
recogniser thinking.

`transcript_debounce_seconds` holds a transcript waiting for more of it,
and it measures the gap between transcripts. Two and a half seconds is
longer than any debounce anyone would set, so the address goes through
alone, the model answers it, and the question arrives afterwards as a
second turn.

## Why the obvious fix makes it mute

Hold the transcript while the person is still speaking — VAD knows, and
the frames pass through the gate on their way. It reads correctly and it
was measured: the fragments joined, four turns became two.

And the assistant went silent. Not slower — silent. Three runs out of
three, the model call fired at the moment the pipeline was shutting
down:

```
12:09:39  transcription_passed (text=Скажи однім реченням, Як ти себе почуваєш?)
12:10:07  PipelineTask#0: Closing.
12:10:07  POST https://api.deepseek.com/v1/chat/completions
```

Because there is a second clock. The context aggregator runs the model
when the *user's turn* ends, and the turn ends on VAD silence —
`turn_silence_seconds`, one second after the last sound. A transcript
released after that lands in a turn that has already closed, and waits
for a turn end that never comes.

So the two clocks are:

- **the debounce**, started by a transcript arriving, 1.2 s
- **the turn**, started by silence in the room, 1.0 s

and the transcript has to win a race it starts late — recognition alone
takes about a second. It mostly wins because the aggregator forgives
late arrivals; push it any later and it stops winning at all.

## What this rules out

Any fix that lives only in the gate. The gate can decide *what* to
release and it can delay it, but the deadline it has to meet is set by a
timer it cannot see and does not control. Holding for a better transcript
and answering promptly are, at this layer, the same dial turned opposite
ways.

Two things could actually resolve it, neither small:

- **Let the release end the turn.** If the gate's flush were what closed
  the user's turn — rather than a silence timer running in parallel —
  there would be one clock and no race. That means owning the turn
  strategy instead of configuring it.
- **Stop segmenting late.** Streaming recognition returns partial words
  as they are spoken, so "still talking" and "more words coming" become
  the same signal. Whisper-over-HTTP cannot do this; it is a segmented
  service by construction.

## What was done — the first route

`turn_end = "sentence"` (src/pipeline/turns.py). It extends the
`SpeechTimeoutUserTurnStopStrategy` with one change: every transcript
pushes the deadline back, instead of only the ones that arrive before
voice activity ends. The turn is over when the person has stopped
speaking *and* the recogniser has stopped producing. One clock, and it
is the words.

That alone took the greeting from three replies to two. The rest came
from `vad_stop_secs`, raised 0.2 → 0.6: at 0.2 the pause after a comma
counted as the room going quiet, so the sentence was split before the
strategy ever saw it. With both, the same sentence now arrives as a
single transcript:

```
before   2.86 s  "Дока, привіт!"          →  greeted
         6.74 s  "Скажи одним реченням…"  →  answered separately
after    8.51 s  "Дока, перелічи будь ласка всі вісім планет…"   one turn
```

Measured over runs: hello and addressed went from three replies to two,
and each run got 3–5 seconds shorter. Two remain because they fall either
side of a full stop — a pause a person makes on purpose, and arguably
one an assistant should answer.

The cost is 0.4 s added to the end of every turn, paid for by removing
the debounce's contribution to the same wait.

The second route — streaming recognition — is still open and still the
only way to get below this.
