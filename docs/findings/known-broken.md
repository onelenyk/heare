# Known broken, not yet fixed

Each verified by reading the code and running it, not inferred. Line
numbers as of 2026-08-10.

## Memory search returns the worst matches first

`src/memory/sqlite_backend.py:160`

```sql
ORDER BY rank * (
    1.0
    - 0.3 * MAX(0.0, 1.0 - (strftime('%%s','now') - m.last_accessed) / 2592000.0)
    ...
) ASC
```

The query is built with an f-string, so `%%s` reaches SQLite as the
literal text `%s`, not the epoch seconds. SQLite coerces it to `0`, so
`(0 - last_accessed) / 2592000` is about −689, and the multiplier becomes
roughly **−206** instead of ~1. FTS5's `rank` is negative for better
matches, so multiplying by a negative number inverts the order and `ASC`
puts the worst matches on top.

`recall` has therefore been feeding the model the least relevant memories
it could find. **This affects `src/core/` too** — it reuses the same
backend.

Fix: delete one `%`. The test suite never caught it because the tests
assert that results exist, not that they are the right ones.

## Barge-in waits on a five-second network call

`src/pipeline/stages/echo_classifier.py:164`

```python
result = await asyncio.wait_for(self._classify_echo(prompt), timeout=timeout)
```

An inline DeepSeek call inside `process_frame`, guarded by
`self._bot_speaking` — so it fires *only when the user interrupts*.
`deepseek_timeout_seconds` is 5.0 (`src/config.py:320`) and
`echo_classifier_enabled` defaults to True (`src/config.py:205`), not
overridden in `~/.heare/config.toml`. The stage sits in the main chain
before `transcription_gate` (`src/pipeline/build.py:382`).

The fastest action in the system — "stop, I'm talking" — is the only one
with a network call and five seconds of patience in its path. Echo is
already filtered twice before STT; this third pass costs latency at the
worst possible moment.

Not present in `src/core/`.

## The local API has no authentication at all

`src/api.py` — 48 handlers, zero checks for `Origin`, `Host` or
`Authorization`. One of them runs `bash`.

Bound to `127.0.0.1`, so today any web page the user visits can post a
form at it and run a shell command. **On a mini-server on a network this
becomes an opening from outside**, which is exactly the deployment being
planned.

`src/core/` exposes no HTTP at all yet. Whatever it grows must not repeat
this.

## Markdown and emoji are spoken aloud

Observed in live runs: TTS pronounced `` `echo hello` `` with the
backticks and read 😊 out loud. The system prompt forbids markdown, but a
prompt enforces nothing. A scrub before TTS is about ten lines.

## Whisper hallucinates on silence

Near-silent segments come back as `Дякую.` (or `Thank you.` in English) —
Whisper filling a gap. When echo leaks through, these are transcribed,
answered, and the assistant talks to itself. The echo fix removed the
cause; an energy floor before STT would remove the class.

## AEC bugs still live in the old tree

`src/pipeline/stages/webrtc_aec_filter.py` still passes int16-scaled
values into an API expecting `[-1, 1]`, and still only processes frames
while the bot speaks. See [echo-cancellation.md](echo-cancellation.md).
The stale-reference slice was fixed there; the other two were not.

`experiments/spine/echo_probe.py` has the same scale bug, plus a blocking
consumer loop that reads stale audio as a barge-in. Its recorded results
should be treated as meaningless.
