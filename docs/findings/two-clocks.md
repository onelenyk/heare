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

## What is true today

The debounce is 1.2 s: long enough to join the fragments that arrive
close together, short enough to keep winning the race. Sentences said in
one breath with a long clause in the middle still get answered twice.

The scenarios allow up to two replies to one question for exactly this
reason, and that tolerance is a record of an unfixed problem, not a
judgement that two replies are fine.

The attempt above is not in the tree. It is written down here so the next
person to have the same good idea can start from the measurement instead
of the idea.
