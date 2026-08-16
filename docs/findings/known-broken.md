# Known broken

Each verified by reading the code and running it, not inferred. Line
numbers as of 2026-08-10. Items marked **fixed** were closed the same
day; they are kept because the shape of each is worth recognising again.

## Memory search returned the worst matches first — fixed

`src/memory/sqlite_backend.py`. Two independent sign errors in one
`ORDER BY`.

```sql
ORDER BY rank * (
    1.0
    - 0.3 * MAX(0.0, 1.0 - (strftime('%%s','now') - m.last_accessed) / 2592000.0)
    - 0.1 * CAST(MIN(m.access_count, 5) AS REAL) / 5.0
) ASC
```

**The `%%s`.** The query is built with an f-string, so `%%s` reached
SQLite as the literal text `%s`, not the epoch seconds. SQLite coerces
that to `0`, so `(0 - last_accessed) / 2592000` is about −689 and the
multiplier came out near **−206** instead of ~1. FTS5's `rank` is
negative for better matches, so a negative multiplier inverts the order
and `ASC` puts the worst matches on top.

**The subtraction.** Even with `%s`, both terms *subtract*. A boost has
to make the product more negative, so every "boost" demoted the memory it
was meant to promote.

The detail that let it live for months: `last_accessed` is 0 until a
memory has actually been recalled, and at 0 the broken multiplier stays
positive and behaves almost sensibly. It inverted only for memories that
had been used — and worse, scores of opposite sign sorted into two
blocks, so **every used memory ranked below every unused one**, however
well it matched. Tests that stored rows and searched immediately never
saw any of it.

Covered now by `tests/test_memory_ranking.py`, whose four assertions all
fail against the shipped clause.
## It answers one sentence two or three times — mostly fixed

Three replies became two: `turn_end = "sentence"` and `vad_stop_secs`
0.2 → 0.6. The two that remain fall either side of a full stop. See
docs/findings/two-clocks.md.

## Interrupting works about half the time, and the canceller is involved

Measured in the simulated room, four runs each, everything else held
still:

| | barge-in | landed |
|---|---|---|
| echo cancellation on | 1040–1300 ms | 2 of 4 |
| echo cancellation off | 557–1036 ms | 4 of 4 |

In every failed run voice activity detection fired exactly one time
fewer than in the successful ones. The interrupting voice is not being
lost downstream — it is not being recognised as a voice at all.

Two explanations were tested and are wrong. Switching off noise
suppression made it worse (0 of 4). Lowering the detection thresholds
made it worse (1 of 4). And the canceller does not destroy speech: fed a
voice speaking over an echo it returns it intact — 2000 in, 5548 out,
now asserted in tests/test_core_aec.py. (Fed a *tone* it returns 29, and
a first version of that test used a tone and would have had me report
the opposite. Speech is not stationary.)

What the numbers do say: in the room the canceller achieves 9–21 dB of
suppression, not the 40–50 dB measured against a tone, and its delay
estimator reports 1–177 ms at confidence 0.01–0.08 — which is to say it
cannot find the echo it is supposed to be removing. Alignment between
the reference and the echo is the next thing to measure, and it wants a
session of its own rather than more four-run samples of a coin flip.


## Barge-in waited on a five-second network call — fixed

`src/pipeline/stages/echo_classifier.py:164`

```python
result = await asyncio.wait_for(self._classify_echo(prompt), timeout=timeout)
```

An inline DeepSeek call inside `process_frame`, guarded by
`self._bot_speaking` — so it fired *only when the user interrupts*, with
`deepseek_timeout_seconds` at 5.0. The fastest action in the system —
"stop, I'm talking" — was the only one with a network round trip in its
path.

`echo_classifier_enabled` now defaults to False. The stage is left in
place: it is a reasonable idea in the wrong position, and deleting it
would lose that.

## The local API has no authentication at all — fixed 2026-08-17

`src/api.py` — 48 handlers, zero checks for `Origin`, `Host` or
`Authorization`. One of them runs `bash`.

Bound to `127.0.0.1`, so today any web page the user visits can post a
form at it and run a shell command. **On a mini-server on a network this
becomes an opening from outside**, which is exactly the deployment being
planned.

`src/core/` exposes no HTTP at all yet. Whatever it grows must not repeat
this.

**Fixed.** `src/api.py` now runs one `_auth_middleware` (`api.py:411`)
ahead of every route, not 48 per-handler checks: a request whose `Origin`
header is set and is not 127.0.0.1/localhost is refused with 403 before
anything else runs, and every non-exempt route additionally requires the
bearer token from `~/.heare/api_token` (`Authorization: Bearer …`, or
`?token=` for `GET` only, since `EventSource` cannot set headers). Only
`GET /`, static assets, and `GET /api/health` are exempt, and the SPA
shell gets the token injected inline rather than needing it exempted too.

## One key, three homes, no warning

`DEEPSEEK_API_KEY` can live in `~/.heare/.env`, in `~/.heare/config.toml`,
and in the state database as `key_deepseek_api_key` — and the state wins,
because that is how a key is hot-swapped without a restart.

Observed: the key was rotated in `.env`, verified live with curl, and the
daemon still answered every request with 401. The revoked one was sitting
in the state database from some earlier swap, and nothing said so.

Nothing reports which source won, or that a value was shadowed. At
minimum the daemon should log the source of each key it resolves.

## Markdown and emoji are spoken aloud

Observed in live runs: TTS pronounced `` `echo hello` `` with the
backticks and read 😊 out loud. The system prompt forbids markdown, but a
prompt enforces nothing. A scrub before TTS is about ten lines.

## Whisper hallucinates on silence — fixed

Whisper is generative: handed silence it does not return nothing, it
returns the likeliest text. On this hardware that is `Дякую.` Measured in
a real session: eight of them in ninety seconds, interleaved with
`І серпу.` and a sentence of invented Ukrainian.

Each became a complete turn — model call, synthesis, utterance — and the
user's real questions queued up behind them. The assistant appeared slow
while it was busy answering a room.

VAD let them through because the thresholds were generous against a
microphone at 2.4× gain: confidence 0.3, minimum volume 0.1, 100 ms to
open a turn. Those are raised (0.6 / 0.25 / 0.2), and
`src/pipeline/stages/speech_energy_gate.py` catches what still gets
through: it sits after STT, where both the audio and the transcript are
visible, and drops any transcript whose segment was quieter than
`stt_min_rms` or shorter than `stt_min_speech_seconds` — plus known
filler phrases from short segments, since the user is still allowed to
say thank you.

## AEC in the main pipeline — fixed

`src/pipeline/stages/webrtc_aec_filter.py` is gone, along with its tests.
It was unreachable once `build_pipeline` moved to `src/core/aec.py`, and
leaving a module that destroys audio — with a passing test suite
certifying its behaviour — invites someone to wire it back. The main
pipeline now uses the continuous implementation, with the correlation
gate off and interruptions on. See
[echo-cancellation.md](echo-cancellation.md).

`experiments/spine/echo_probe.py` still has the same scale bug, plus a
blocking consumer loop that reads stale audio as a barge-in. Its recorded
results should be treated as meaningless.
