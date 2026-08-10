# core — the same assistant, assembled instead of removed

Built alongside the existing daemon, not carved out of it. Nothing in
`src/core/` imports `modes`, `indication`, `skills`, `mcp_bridge`,
`subagent_manager`, `browser_bridge`, `dashboard_data` or the frontend —
so none of them had to be untangled. Whatever this never imports is dead
by demonstration, and `git rm` becomes safe at the end rather than risky
at the start.

```
uv run python -m src.core.main --check   # builds everything, opens nothing
uv run python -m src.core.main           # listens and talks
```

## What it is

```
pipeline.py   265   ten stages
tools.py      306   a decorator and eleven tools
harness.py    249   drive it from text, no microphone
aec.py        248   echo cancellation that actually cancels
agent.py      148   hands — the second agent, no deadline
settings.py   127   one dataclass, one toml read, ~/.heare/.env for keys
main.py        95   entry point
audio_probe.py 69   level taps on the microphone path
prompt.py      49   voice, not policy
state.py       45   knobs, in-process
              ────
              1601
```

Against 30 602 lines in `src/`, with 63 tools and 27 stages.

## What it reuses

Imported rather than rewritten: `FixedLocalAudioTransport`, the Edge TTS
service and its cache, `SwitchableLLMService` with its provider registry,
`GroqSTTService`, and the sqlite memory backend.

Not reused: the echo canceller. `aec.py` replaces it — the original was
destroying the signal rather than cleaning it. See
[docs/findings/echo-cancellation.md](../../docs/findings/echo-cancellation.md).

## What it drops, and why

| dropped | reason |
|---|---|
| `echo_classifier` | awaited a 5 s DeepSeek call inside `process_frame`, and only while the user was interrupting — the one path that must never wait was the only one that did |
| smart-turn analyzer | `SpeechTimeoutUserTurnStopStrategy` ends a turn on VAD silence without `transformers` |
| five observers | a log line where the thing happens |
| modes, capabilities | policy as prose that nothing enforced |
| skills, MCP, subagents, browser | four mechanisms for "use other tools"; there should be one |
| canvas, dashboard, menubar, indication | they assume a screen; the target is a headless mini-server |

Barge-in is left **on** (`VADUserTurnStartStrategy(enable_interruptions=True)`)
rather than gated behind the classifier. AEC keeps our own voice out of
the microphone, so a voice heard during playback is the user. If that
turns out to be false on built-in speakers, it will be audible
immediately — it will cut itself off mid-sentence.

## Two agents, one model

`voice` sees three verbs — `delegate`, `remember`, `recall` — and must
answer within a second. `hands` (`agent.py`) sees every tool and has no
deadline; it runs as an asyncio task, not a pipeline stage, and delivers
its result back as a user turn so the voice agent phrases it itself.

## Measured, not assumed

`harness.py` drives the pipeline from text with the audio devices
disabled, so everything except the acoustics can be measured without a
microphone.

```
uv run python -m src.core.harness "..."                    # voice + hands
uv run python -m src.core.harness --single "..."           # control
uv run python -m src.core.harness --interject "..." --interject-at 5
```

`sleep 15`, with the user asking something else at 5 s:

| | single agent | voice + hands |
|---|---|---|
| first token | 6012 ms | 2415 ms |
| first audio | 8175 ms | 3523 ms |
| utterances | 2 | 3 |

The point is not the median. Run the control twice and it answers at
1711 ms once and 8175 ms the next time — whether you hear anything before
the work happens is the model's whim. `delegate` returns instantly, so
the acknowledgement is structural.

One assumption died here: pipecat does **not** block the conversation
during a tool call. The control answered the interjection while `bash`
was still running. The cost of tools inline is the silence before the
first utterance, not a frozen pipeline.

## What it does not have yet

Continuous listening, the conversation store, and the HTTP inject
endpoint. Each is now cheap: a tool costs a decorator and a dozen lines
instead of four tables across nine files.

## Barge-in

It works, on built-in speakers, which it never did before. Getting there
meant finding four separate bugs in the echo path — the decisive one an
int16/float scale overflow that clipped every sample to ±1 LSB, about
−90 dB. What looked for months like "it does not hear itself" was the
microphone being deaf during playback.

```
                  before            after
reference queue   0 ↔ 4600 ms       0–160 ms, stable
measured delay    29–352 ms         124–134 ms
suppression       6–40 dB           40–50 dB
```

Full account: [docs/findings/echo-cancellation.md](../../docs/findings/echo-cancellation.md).

The correlation gate is off by default (`--gate` re-enables it) — it
dropped 100% of frames during playback, which is a mute by another name.

## Honest status

Driven from text repeatedly, and through the microphone live: it hears,
answers, is interruptible, delegates, and brings the result back as a
separate utterance.

Not there yet: continuous listening, persistence of conversations
(context lives in process memory), and any HTTP surface. And `recall`
still ranks worst-first — see
[docs/findings/known-broken.md](../../docs/findings/known-broken.md).
