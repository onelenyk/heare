# acp — can any agent CLI be heare's hands?

## The hypothesis

> One client of ~300 lines, stdlib only, speaks ACP to Claude Code and to
> opencode alike, and gives the voice what it needs: a quick `ask` that
> streams, a `do` that runs in the background without blocking an `ask`,
> a yes/no before the agent changes anything, and a stop that stops.

If it holds, `src/agent/` (the hand-written tools and agent loop, ~6 900
lines) can be replaced by this file plus a line per agent.

## How you would know it failed

- The first sentence of an `ask` takes more than ~4 s.
- An `ask` waits for a running `do`.
- The agent changes something after it was told no.
- `stop` leaves the agent working.
- Switching the agent means changing code, not a line in `AGENTS`.

## Running it

```
uv run python experiments/acp/try.py claude                  # you type
uv run python experiments/acp/try.py claude --model haiku
uv run python experiments/acp/try.py opencode
uv run python experiments/acp/try.py claude --bench          # the table below
```

`--bench` works in a temp dir and refuses every permission except reading,
so it cannot change the machine. Agent stderr goes to `logs/`.

## Measured, 25 September 2026

First sentence, i.e. when the voice could start speaking:

| | claude/haiku | claude/sonnet | claude/default | opencode |
|---|---|---|---|---|
| start (once) | 1.0 s | 1.0 s | 0.9 s | 1.2 s |
| ask «котра година?» | 3.1 s | 1.5 s | 4.5 s | 2.7 s |
| ask, follow-up | 3.9 s | 1.2 s | 4.5 s | 1.4 s |
| ask «що навантажує комп'ютер?» | 1.8 s ✗ | 3.2 s | 5.4 s | 4.5 s |
| ask while a do runs | 1.4 s | 1.3 s | 1.6 s | 1.0 s |
| do: asked before writing | yes | yes | yes | **no** |
| do: wrote after "no" | no | no | no | **wrote, never asked** |
| stop | at once | at once | at once | at once |

✗: haiku answered «я не бачу твого комп'ютера» without trying. Sonnet and
the default model ran `ps`/`top` themselves and answered correctly.

## What it showed

1. **It holds for Claude Code.** Every point of the hypothesis, on the
   first run, with no agent-specific code.
2. **Sonnet is the right default for `ask`**: 1.2–1.5 s to the first
   sentence when no tool is needed, ~3 s when one is. Haiku is not
   faster enough to pay for not using its tools.
3. **A yes/no depends on the agent, not the client.** opencode never
   sent `session/request_permission`; its own config decides whether it
   asks. The client cannot enforce a question the agent does not ask, so
   each agent's permission config is part of wiring it in.
4. **The agent does not know the time** unless it thinks to run `date`.
   The voice should prefix the current time, the way heare's prompt does.
