# Two agents, one model

The only mode. `src/agent/hands.py` in the daemon,
`src/core/agent.py` in the small core; measured with the harnesses.

## What it is

Not two assistants. One model, one key, one codebase. The split is
**temporal**: what must fit in a second, and what must not block anything.

- **Voice** sees three verbs — `delegate`, `remember`, `recall` — and
  holds the conversation. Choosing among three is trivial; choosing among
  sixty-three under a latency budget is not.
- **Hands** (`Hands` in `agent.py`) sees every tool and has no deadline.
  It runs as an asyncio task, not a pipeline stage, so nothing in the
  speaking path waits on it.

A finished job re-enters the conversation as a user turn marked
`[результат роботи]`, so the voice agent phrases the answer itself — in
the right language, in its own voice, through the existing TTS path. No
second speaking mechanism.

## The measurement

`sleep 15`, with the user asking an unrelated question at 5 s:

|                | single agent | voice + hands |
|----------------|--------------|---------------|
| first token    | 6012 ms      | 2415 ms       |
| first audio    | 8175 ms      | 3523 ms       |
| utterances     | 2            | 3             |

Reproduce with:

```
uv run python -m src.core.harness --window 32 \
    --interject "а поки воно робиться — як тебе звати?" --interject-at 5 \
    "виконай sleep 15 і скажи коли завершиться"
uv run python -m src.core.harness --single ...      # the control
```

## What the numbers actually show

**Not a faster median.** Run the control twice and it answers at 1711 ms
once and 8175 ms the next — whether you hear anything before the work
begins is the model's whim. Sometimes it narrates before calling the
tool, sometimes it goes silent into it.

`delegate` returns instantly, so the acknowledgement is **structural**
rather than hoped for. That is the whole gain: not speed, the removal of
the silent case.

## One assumption that died

Tool calls were believed to block the conversation. They do not — in the
control run the assistant answered the interjection while `bash` was
still sleeping. Pipecat runs function calls concurrently.

The real cost of tools inline is the silence before the first utterance,
not a frozen pipeline. The argument for the split survives; the reason
given for it was wrong.

## What it cost to make it the only mode

Three things that had been true only by accident:

**`delegate` had to disappear when no worker runs.** Advertised, chosen
and guaranteed to fail is the worst kind of tool.

**The prompt had to stop describing the rest.** 2484 tokens to 1061:
skills, MCP, marketplace, background agents, a four-call budget and
narration rules for tools the voice agent cannot call. Telling a model
about someone else's job is how it ends up announcing work it never
delegated.

**Modes and the action log had to move with the work.** They lived in the
pipeline's handler wrapper, which the worker does not go through — so
without repeating them, every mode would quietly have become "allow
everything" and the actions table would have gone back to empty. Both are
now applied in `Hands._execute`.

## Routing

Errors are asymmetric. Delegating needlessly costs one extra sentence;
answering from nothing costs a fabrication. So the prompt says: when
unsure, delegate. The risk to watch for is the opposite failure — every
trivial exchange becoming two utterances. That is audible immediately,
and it is tuned in `DELEGATING` in `src/core/prompt.py`.
