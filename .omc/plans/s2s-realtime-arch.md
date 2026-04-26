# heare S2S-realtime Architecture — Two-Phase Plan

Goal: move heare from filter-based "decider" pattern to continuous-generator
with async action worker. Minimize latency (target <2s time-to-first-audio)
while keeping the STT→LLM→TTS stack. No true S2S model required.

## Mental model shift

Today: every turn pays for a blocking classifier + generator + (maybe) actor.
Tomorrow: every turn always gets a reply from a generator; actions are fired
and forgotten into a background worker that reports back via context.

```
┌─── Conversation loop (fast, always replies) ─────────────────────┐
│  audio → STT → Generator → reply → TTS → audio                   │
│                    └── intent_payload (optional)                 │
└──────────────────────────┼───────────────────────────────────────┘
                           ▼
┌─── Action worker (async, non-blocking) ──────────────────────────┐
│  intent_queue → executor → result                                 │
│                              └── ConversationManager.add_result   │
└───────────────────────────────────────────────────────────────────┘
```

---

## Phase 1 — Minimal viable rewrite (prove the pattern)

Branch: `s2s-realtime`. Clean rewrite, not a mutation. Delete before building.

### What ships

A stripped-down pipeline that always replies:

```
LocalAudioTransport (Silero VAD only, stop_secs=0.3)
  → GroqSTTService
  → GeneratorProcessor   ← new, replaces DeciderProcessor
  → EdgeTTSService (streaming)
  → LocalAudioTransport
```

### What `GeneratorProcessor` does

- Receives `TranscriptionFrame`
- Builds minimal context: time, mode, last 3 transcripts, persona
- Calls Claude CLI (or Ollama, behind flag) with a generator prompt
- Streams reply text → pushes `TTSSpeakFrame` chunks as tokens arrive
- Emits `IntentFrame` (new frame type) when LLM signals an intent block

### What gets deleted (temporarily)

- `DeciderProcessor` state machine (LISTENING/AWAITING_CONFIRMATION/EXECUTING)
- `type: nothing/speak/act` branching
- `silent` / `focus` / `ambient` mode-specific rules
- `TurnAggregator` and its timeouts
- Speaker ID + confirmation passphrase gates
- `HeartbeatTask` (reintroduce in Phase 2)
- `ConversationManager` (reintroduce in Phase 2)

### What stays

- Identity + persona rendering
- Basic context (time, recent transcripts)
- TTS cache for the greeting phrase
- Session persistence (claude CLI session)

### New prompt: `prompts/generator.txt`

Single-purpose: always respond. No JSON classification. Streams plain text
with an optional `<intent>{...}</intent>` tag for actions.

### Success criteria

1. Bot replies to every utterance
2. Time-to-first-audio ≤2s in a fresh session
3. No modes, no silence — always engages
4. Playback feels conversational (no 8s dead air)
5. 100% of existing tests for unchanged modules still pass (storage,
   identity, agent_sdk_cli, groq STT path)

### Out of scope for Phase 1

- Intent queue / action worker (generator may emit `<intent>` but we just log it)
- Speaker recognition
- Confirmation passphrases
- Conversation memory
- Proactive heartbeats
- Mode switching

---

## Phase 2 — Wire existing logic back in

After Phase 1 proves the pattern. Add one capability at a time, each behind
its own flag. Order matters: simplest dependencies first.

### 2.1 Intent queue + action worker

- New module `src/actions.py`: `IntentQueue`, `ActionWorker`
- Worker is a long-running asyncio task started in `main._cmd_start`
- Uses existing `ClaudeBackend.call_action` under the hood
- Results logged and stashed in `ConversationManager.completed_actions`
- Worker respects cancellation via queue signal

**Acceptance:** user says "додай хліб до списку" → bot says "додам"
immediately, action runs in background, next turn mentions completion.

### 2.2 ConversationManager

- Reintroduce `conversation.py` unchanged
- Wire `ContextBuilder` with `conversation_manager` (already done last week)
- Generator context includes: recent turns, active topics, pending actions,
  completed actions
- Topic extraction moves to **background task** — never blocks generator

**Acceptance:** generator naturally weaves previous topics into replies.

### 2.3 Speaker ID (flagged)

- Reintroduce speaker_id stack
- Generator context includes `{speaker_id: "owner" | "stranger" | null}`
- Generator prompt gets a soft rule: "if not owner, answer briefly or defer"
- NO hard block — generator decides tone based on speaker

**Acceptance:** strangers get polite brief replies; owner gets full engagement.

### 2.4 Confirmation passphrase (flagged)

- Action worker checks destructive intents against a passphrase
- If intent flagged destructive + no passphrase in last N seconds: worker
  pushes "потрібне підтвердження" into ConversationManager, waits
- Next utterance containing passphrase releases the action

**Acceptance:** "видали всі файли" requires explicit "авторизую" within 30s.

### 2.5 Modes (scaled-down)

- Replace silent/focus/ambient with TWO flags:
  - `engagement_floor: "off" | "wake_word" | "low" | "normal"` — how eager
    the generator is to reply
  - Surface this in the generator prompt as an "eagerness" hint
- Drop `heare mode focus` CLI if no one uses it

**Acceptance:** `engagement_floor=off` → bot never replies until wake word.
`engagement_floor=normal` → current ambient behavior.

### 2.6 Heartbeat (proactive nudges)

- `HeartbeatTask` emits a synthetic `HeartbeatFrame` every N minutes
- Generator processes it like a normal turn but with empty transcript and
  context hint "long silence — optional nudge"
- Generator may respond with empty reply (treated as nothing)

**Acceptance:** after 30min silence, bot sometimes says something ambient-
relevant; other times stays quiet.

### 2.7 Cleanup passes

- Delete agent_sdk_cli complexity if not needed
- Decide on Ollama vs claude CLI per role (generator vs action worker)
- Streaming verification for both backends
- Full regression of integration tests

---

## Rollout

- Phase 1 lives on `s2s-realtime` branch, merged when acceptance criteria pass
- Phase 2 features each get their own feature flag in `config.toml`,
  defaulted off, so rollback is trivial
- Old `decider.py` code kept in git history; no dual maintenance
- Tests: write new generator tests fresh; delete tests bound to dead decider
  logic (states, nothing/speak/act branching)

## Latency budget (target)

| Stage | Today | Phase 1 | Phase 2 |
|-------|-------|---------|---------|
| VAD stop | 500ms | 300ms | 300ms |
| STT | 1500ms | 1500ms | 1500ms |
| Generator first token | 5000ms (full response) | ~500ms (streaming) | ~500ms |
| TTS TTFB | 700ms | 700ms (parallel) | 700ms (parallel) |
| **Total to first audio** | **~8000ms** | **~2000ms** | **~2000ms** |
| Action wait | blocks voice | n/a (Phase 1 drops actions) | non-blocking |

## ADR-002 — OpenRouter pivot (2026-04-18)

Benchmark results from `.omc/benchmarks/phase1-latency-baseline.md` and
`.omc/benchmarks/phase1-openrouter.md`:

- `claude -p` warm TTFT: ~3054ms (NO-GO against ≤1500ms target)
- `claude -p` floor is ~3s per call regardless of cold/warm or subprocess
  persistence — Claude Code loads ~31KB of tools/hooks context per turn
- `google/gemini-3.1-flash-lite-preview-20260303` via OpenRouter SSE:
  warm TTFT median ~1131ms, 95p ~1450ms (one 7.3s outlier)

**Decision:** Generator backend pivots from `claude -p` to OpenRouter
streaming HTTP. Constraint "local LLM invocation only" is relaxed to
allow OpenRouter for the generator role; Claude CLI is still used for
the action worker in Phase 2 where tool use justifies the per-turn cost.

Cost: ~$0.001/day at typical heare usage. One order of magnitude cheaper
than the Anthropic API was going to be.

## Decisions locked in

- **Backend split by role** (revised by ADR-002)
  - Generator: **OpenRouter SSE streaming**, model
    `google/gemini-3.1-flash-lite-preview-20260303`, timeout 5s per
    request, fallback phrase on error
  - Action worker (Phase 2): Claude CLI agent SDK retained for tool use
- **Stateless generator** — no Claude session. `ConversationManager` is
  the single source of conversation memory. Each generator call is a fresh
  `claude -p` with full context assembled from ConversationManager.
- **Trust Anthropic prefix caching** — persona + policy rules + stable
  context prefix auto-cache on Anthropic's side (~90% off input tokens).
  No explicit cache handling required in heare.
- **Workspace separation** — generator uses a dedicated workspace
  (`~/.heare/workspace-generator/`) with an empty `.mcp.json` and no tool
  permissions. Action worker keeps the existing `~/.heare/workspace/`.
  Principle of least privilege.
- **Speaker tagger in Phase 1** — deleted, reintroduced in Phase 2.3.
  Fewer moving parts during the pattern-proving spike.

## Still to decide

- Intent schema: structured JSON vs XML-tagged freeform. Leaning XML
  (`<intent>...</intent>`) because it's easier to stream-parse and degrades
  gracefully when the LLM adds surrounding prose. Revisit at 2.1.
- ConversationManager rolling-window policy for higher-frequency turns.
  Needs a size cap and a summarization trigger. Revisit at 2.2.
