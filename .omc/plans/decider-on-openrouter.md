# Topic Extraction on OpenRouter — Revised Plan

> **Revision 2 (2026-04-25).** Previous plan (v1) was REJECTED by Architect and
> Critic for targeting `DeciderProcessor.call_decider` — dead code on this
> branch since Phase 2.1 (commit `576a0c4`). This revision targets the ONE
> live `call_decider` consumer: `ConversationManager.extract_topics()`.

---

## RALPLAN-DR Summary (Deliberate Mode)

### Principles

1. **Verify before you build** — every file:line reference in this plan was read
   and confirmed live via grep + manual inspection. No references to dead code.
2. **Narrow scope, real impact** — one method, one new module, one config flag.
3. **Default-safe** — ships with `topic_extraction_backend = "claude"`. OpenRouter
   path is opt-in until validated in production.
4. **Graceful degradation** — topic extraction is a background enrichment task.
   Failure returns `[]` (empty topics). Speech path is never affected.
5. **Separation of concerns** — non-streaming JSON-mode client is a new module,
   not bolted onto the streaming `OpenRouterCLI`.

### Decision Drivers (top 3 — observable in production)

| # | Driver | How to observe |
|---|--------|---------------|
| 1 | **Claude API call count per turn** | Log grep: `call_decider` invocations in `AgentSDKCLI._run_query` or `ClaudeCLI`. With OpenRouter backend, these drop by 1 per turn. |
| 2 | **Background memory update latency** | `[MEMORY UPDATE conv=X topics=N turn_len=M]` log timestamp vs the `_background_memory_update` entry timestamp. OpenRouter should be faster than holding the SDK lock. |
| 3 | **SDK lock contention** | `AgentSDKCLI` holds an `asyncio.Lock` (line 84-89 comment). `extract_topics` competes with `call_action` for this lock. Moving topics off Claude frees the lock for actions. |

### Viable Options

#### Option A: Move `extract_topics` to OpenRouter (recommended)

- New `OpenRouterTopicExtractorCLI` module: non-streaming, JSON-mode, `httpx`-based.
- `ConversationManager` gains an optional `topic_extractor` parameter.
- Config flag `topic_extraction_backend` controls routing (`claude` | `openrouter`).
- **Pros**: Eliminates 1 Claude API call/turn, frees SDK lock, ~200ms vs ~1-2s.
- **Cons**: New module to maintain, OpenRouter dependency for a second use-case.

#### Option B: Status quo — keep `extract_topics` on Claude

- No code changes.
- **Pros**: Zero risk, zero work.
- **Cons**: Every turn pays a Claude `call_decider` round-trip for a task that
  needs only JSON array extraction (not tool use, not reasoning). The SDK lock
  serializes topic extraction behind any concurrent `call_action`, adding latency
  to memory updates that feed the next turn's context.
- **Invalidation rationale**: The user explicitly requested moving `call_decider`
  off Claude to OpenRouter. The status quo does not satisfy the request. Additionally,
  the cost and lock-contention are real: `agent_sdk_cli.py:84-89` documents the
  serialization problem and names `ConversationManager.extract_topics` as a
  contender. This is the lowest-risk win available.

#### Option C: Use z.ai (Anthropic-compatible) instead of OpenRouter

- Reuse the `ZaiCLI` pattern for topic extraction.
- **Pros**: z.ai is already configured; Haiku is fast.
- **Cons**: z.ai is streaming-only (Anthropic SDK `stream()`); topic extraction
  wants a single JSON response, not a stream. Would require buffering the full
  stream then parsing — unnecessary complexity. Also, z.ai availability is
  user-specific (requires `~/.claude.json` session token).
- **Invalidation rationale**: The user's request specifically says "OpenRouter",
  and the non-streaming use-case is a poor fit for the Anthropic streaming SDK.
  A separate non-streaming httpx client is simpler and more testable.

### ADR (summary — full ADR at end of document)

- **Decision**: Option A — new `OpenRouterTopicExtractorCLI` for `extract_topics`.
- **Drivers**: Claude API call count per turn, background memory update latency,
  SDK lock contention.
- **Alternatives considered**: Status quo (B), z.ai (C) — see full ADR for
  rejection rationale.
- **Why chosen**: Narrowest scope, default-safe rollout, frees SDK lock, routes
  around latent Claude-path parser bug for opted-in users.
- **Consequences**: See full ADR section for positive/negative breakdown.
- **Follow-ups**: (1) Flip default to `openrouter` after production validation.
  (2) Fix Claude-path topic extraction parser. (3) Retire `DeciderProcessor`.

---

## Context

Phase 2.1 (commit `576a0c4`) replaced the two-stage decider-then-generator
architecture with a single-stage `GeneratorProcessor` pipeline. The old
`DeciderProcessor` class in `src/decider.py` is dead code — only tests
instantiate it.

However, `ConversationManager.extract_topics()` (`src/conversation.py:115`)
still calls `self.claude.call_decider(prompt)` to extract topic tags from each
assistant reply. This is invoked from `GeneratorProcessor._background_memory_update`
(`src/generator.py:313`) as a fire-and-forget background task after every turn.

This plan moves that single call from Claude Code to OpenRouter.

**Latent Claude-path bug (not fixed here):** When `extract_topics` runs on the
Claude backend today, `parse_decider_response` (`src/claude_backend_common.py:68-91`)
rejects bare JSON arrays as "not a dict" and the post-call parser at
`conversation.py:117-125` falls through to the `[]` exception path. This means
the Claude path silently discards valid topic arrays in some code paths. This
plan does **not** fix that bug; it only ensures the new OpenRouter path is
correct from the start. The Claude-path fix is tracked in Follow-ups: "fix the
Claude-path topic extraction parser as a separate cleanup."

---

## Work Objectives

1. Create a non-streaming OpenRouter client for topic extraction (JSON-mode).
2. Inject it into `ConversationManager` behind a config flag.
3. Wire the new backend in `src/main.py`.
4. Add unit tests for the new client; update existing conversation tests.
5. Preserve default behavior: `topic_extraction_backend = "claude"`.

---

## Guardrails

### Must Have

- Default on `main` after merge: still uses Claude for `extract_topics`.
- All existing `tests/test_conversation.py` tests pass unchanged with default config.
- New OpenRouter client is independently testable via `httpx.MockTransport`.
- Timeout / HTTP error / malformed JSON all degrade to `[]` (empty topics).
- Observability: log line emitted on every `extract_topics` call showing
  `backend=claude|openrouter` and `ms=N` elapsed time.

### Must NOT Have

- No changes to `DeciderProcessor`, `call_action`, `bootstrap_identity`.
- No changes to `ClaudeBackend` Protocol.
- No changes to the speech/generator streaming path.
- No changes to `OpenRouterCLI` (streaming client).
- No new required environment variables (OpenRouter key is already optional).

---

## Out of Scope

- **`DeciderProcessor`** is dead code on this branch. This plan does NOT touch it.
  A separate cleanup plan should remove it.
- **`call_action`** and **`bootstrap_identity`** stay on Claude Code untouched.
- **`GeneratorProcessor._handle_transcription`** already uses OpenRouter; not modified.
- **Flipping the default** to OpenRouter. That is a follow-up after production validation.

---

## Task Flow

```
Story 1: OpenRouterTopicExtractorCLI
  |
  v
Story 2: ConversationManager dual-backend
  |
  v
Story 3: main.py wiring + config
  |
  v
Story 4: Tests + observability
```

---

## Detailed TODOs

### Story 1: Create `src/openrouter_topic_extractor.py`

**Goal**: Non-streaming OpenRouter client that sends a prompt and returns
parsed JSON (not streamed deltas).

**File**: `src/openrouter_topic_extractor.py` (NEW)

**Design** (adapted from the previous plan's `OpenRouterDeciderCLI`, renamed):

```
class OpenRouterTopicExtractorCLI:
    def __init__(self, api_key, model, timeout, *, transport=None)
    async def extract_topics(self, prompt: str) -> list[str]
```

Key decisions:
- Uses `httpx.AsyncClient` with `stream=False` (non-streaming POST).
- Does NOT send `response_format: {"type": "json_object"}` in the request body.
  The prompt instructs the model to return a bare JSON array; defensive parsing
  (`_extract_first_json_array()`) handles malformed output. This avoids the
  top-level-object requirement that some providers enforce in JSON-object mode,
  which conflicts with the bare-array format the prompt requests.
- Response parsing: `json.loads(body)` expecting a JSON array of strings.
  Defensive: if body contains preamble text, use `_extract_first_json_array()`
  helper (find first `[`, find matching `]`, parse that substring).
- Timeout: `httpx.TimeoutException` -> return `[]`.
- HTTP error: log warning, return `[]`.
- JSON parse error: log warning, return `[]`.
- Cap at 5 topics (same as `conversation.py:137`).
- Consecutive-failure tracking: maintain `_consecutive_failures: int = 0` on
  the instance. On any failure path (timeout, HTTP error, JSON parse error,
  empty/malformed payload), increment the counter and return `[]`. After 3+
  consecutive failures, emit
  `logger.warning("[TOPIC EXTRACT consecutive_failures=%d backend=openrouter] degraded — check OpenRouter status or flip topic_extraction_backend=claude", n)`.
  Reset to 0 on success.

**Acceptance criteria**:
- [ ] Module exists with `OpenRouterTopicExtractorCLI` class.
- [ ] `extract_topics(prompt)` returns `list[str]`.
- [ ] Constructor accepts `transport` kwarg for test injection.
- [ ] All error paths return `[]` and log a warning.
- [ ] Consecutive failures >= 3 produce a WARN log; success resets the counter.

### Story 2: Dual-backend `ConversationManager`

**Goal**: `ConversationManager.extract_topics()` routes to either Claude or
OpenRouter based on which backend is injected.

**Files**:
- `src/conversation.py` — modify `__init__` and `extract_topics`

**Changes to `ConversationManager.__init__`** (line 28):
- Add optional parameter: `topic_extractor: OpenRouterTopicExtractorCLI | None = None`
- Store as `self._topic_extractor = topic_extractor`

**Changes to `extract_topics`** (line 93-142):
- If `self._topic_extractor is not None`: call `self._topic_extractor.extract_topics(prompt)`
  directly (it returns `list[str]`, same contract).
- Else: existing Claude `call_decider` path (unchanged).
- Add timing + backend log line at method entry/exit:
  `logger.info("[TOPIC EXTRACT backend=%s ms=%d topics=%d]", backend, elapsed_ms, len(topics))`

**Acceptance criteria**:
- [ ] `ConversationManager(store, claude)` (no topic_extractor) works identically to today.
- [ ] `ConversationManager(store, claude, topic_extractor=extractor)` routes to OpenRouter.
- [ ] Timing log line emitted on every call.
- [ ] Existing tests pass without changes (they don't pass `topic_extractor`).

### Story 3: Wiring in `main.py` + config

**Goal**: Construct `OpenRouterTopicExtractorCLI` when config says so, inject
into `ConversationManager`.

**Files**:
- `src/config.py` — add fields (near line 214-218, alongside existing OpenRouter fields)
- `src/main.py` — modify wiring block (line 234-237)

**Config changes** (`src/config.py`):
- Add `topic_extraction_backend: str = "claude"` — values: `"claude"` | `"openrouter"`
- Add `topic_extraction_openrouter_model: str = "google/gemini-2.0-flash-exp:free"` — cheap/fast default
- Add `topic_extraction_openrouter_timeout_seconds: float = 5.0`
- **Placement**: insert these three fields **immediately after `openrouter_timeout_seconds`
  (line 218)** in `src/config.py`, preserving the logical grouping with the existing
  OpenRouter settings. Do NOT insert them between `topic_extraction_enabled` (line 214)
  and `openrouter_api_key` (line 216).

**Wiring changes** (`src/main.py:234-237`):
```python
topic_extractor = None
if (
    settings.topic_extraction_backend == "openrouter"
    and settings.openrouter_api_key
):
    from .openrouter_topic_extractor import OpenRouterTopicExtractorCLI
    topic_extractor = OpenRouterTopicExtractorCLI(
        api_key=settings.openrouter_api_key,
        model=settings.topic_extraction_openrouter_model,
        timeout=settings.topic_extraction_openrouter_timeout_seconds,
    )

conversation_manager = ConversationManager(
    store, claude_cli, topic_extractor=topic_extractor
)
```

**Acceptance criteria**:
- [ ] With `topic_extraction_backend = "claude"` (default): no `OpenRouterTopicExtractorCLI` created. Behavior identical to today.
- [ ] With `topic_extraction_backend = "openrouter"` + valid key: extractor injected.
- [ ] Missing `openrouter_api_key` with `backend = "openrouter"`: falls back to Claude (log warning).

### Story 4: Tests + observability

**Goal**: Unit tests for the new client; verify existing tests still pass.

**Files**:
- `tests/test_openrouter_topic_extractor.py` (NEW)
- `tests/test_conversation.py` (minor additions)
- `tests/test_config.py` (add new fields)

**New tests** (`tests/test_openrouter_topic_extractor.py`):
- `test_extract_topics_success` — mock transport returns `["a","b","c"]`, verify list.
- `test_extract_topics_with_preamble` — response has text before JSON array.
- `test_extract_topics_object_wrapped` — response is `{"topics": ["a","b"]}`, verify unwrap.
- `test_extract_topics_timeout` — mock transport raises timeout, verify `[]`.
- `test_extract_topics_http_error` — mock transport returns 500, verify `[]`.
- `test_extract_topics_invalid_json` — mock transport returns garbage, verify `[]`.
- `test_extract_topics_limits_to_5` — mock returns 7 items, verify capped at 5.

**Additions to `tests/test_conversation.py`**:
- `test_extract_topics_uses_openrouter_when_injected` — pass a mock `topic_extractor`,
  verify `call_decider` is NOT called, verify extractor was called.
- `test_extract_topics_falls_back_to_claude_when_no_extractor` — existing behavior,
  explicit test that `call_decider` IS called.

**Config tests** (`tests/test_config.py`):
- Verify `topic_extraction_backend` defaults to `"claude"`.
- Verify `topic_extraction_openrouter_model` and `topic_extraction_openrouter_timeout_seconds` have defaults.

**Acceptance criteria**:
- [ ] All new tests pass: `make test`
- [ ] All existing tests pass: `make test`
- [ ] `ruff check` passes: `make build`

---

## Rollout Sequence

1. **Merge with default `topic_extraction_backend = "claude"`** — zero behavior change.
2. **Manual validation**: set `topic_extraction_backend = "openrouter"` in config,
   run daemon, verify `[TOPIC EXTRACT backend=openrouter ms=N topics=M]` logs.
3. **Compare**: run both backends side-by-side, compare topic quality and latency.
4. **Flip default** (separate PR): change default to `"openrouter"` once validated.

---

## Success Criteria (observable in production)

1. `[TOPIC EXTRACT backend=openrouter ms=N topics=M]` log line emitted on every
   turn when `topic_extraction_backend = "openrouter"`.
2. Claude API call count per turn drops by 1 (no `call_decider` from `extract_topics`).
3. `_background_memory_update` completes faster (no SDK lock contention).
4. All existing tests pass with default config (`backend = "claude"`).
5. New tests cover OpenRouter client error paths and ConversationManager routing.

---

## Pre-mortem (3 failure scenarios)

### Scenario 1: The work targets a code path that turns out to be unused

**Mitigation**: Verified by reading the code. Evidence chain:
- `src/conversation.py:115` — `extract_topics` calls `self.claude.call_decider(prompt)`.
- `src/generator.py:313` — `topics = await self.conversation_manager.extract_topics(reply_text)` inside `_background_memory_update`.
- `src/generator.py:302-327` — `_background_memory_update` is called after every generator reply when conversation memory is active.
- `src/main.py:237` — `conversation_manager = ConversationManager(store, claude_cli)`.
- `src/main.py:302` — `conversation_manager=conversation_manager` passed to `build_pipeline`.
- Gate: `settings.conversation_memory_enabled` (config.py:212) and `settings.topic_extraction_enabled` (config.py:214). When both are True, the path is live.
- **Conclusion**: Path is live when conversation memory + topic extraction are enabled. Unlike `DeciderProcessor`, this code has active callers in production.

### Scenario 2: OpenRouter JSON-mode returns unexpected format

The prompt asks for a bare JSON array `["a", "b", "c"]`, but some models
wrap it in an object `{"topics": ["a", "b", "c"]}` when `response_format:
json_object` is set (some providers require a top-level object for JSON mode).

**Mitigation**: `_extract_first_json_array()` parser handles this — it finds
the first `[` in the response body and extracts the array. If the model wraps
in an object, we try `json.loads()` first; if result is a dict, look for any
value that is a list; fall back to array extraction. All parse failures degrade
to `[]`.

### Scenario 3: OpenRouter rate-limits or goes down during production use

Topic extraction is fire-and-forget (`_background_memory_update` catches all
exceptions at generator.py:314-315: `logger.exception("generator: extract_topics failed (non-fatal)")`).
A down OpenRouter means `[]` topics for those turns. The conversation summary
(`update_summary`) still runs with empty topics. Speech is never affected.

**Mitigation**: The `topic_extraction_backend = "claude"` default means Claude
is always available as fallback. Operators can flip back with a config change,
no code deploy needed.

---

## Expanded Test Plan

### Unit tests

| Test | File | What it verifies |
|------|------|-----------------|
| Happy path: JSON array response | `test_openrouter_topic_extractor.py` | `["a","b"]` parsed correctly |
| Preamble text before array | `test_openrouter_topic_extractor.py` | `_extract_first_json_array` works |
| Object-wrapped array | `test_openrouter_topic_extractor.py` | `{"topics": [...]}` unwrapped |
| Timeout -> `[]` | `test_openrouter_topic_extractor.py` | Graceful degradation |
| HTTP 500 -> `[]` | `test_openrouter_topic_extractor.py` | Graceful degradation |
| Invalid JSON -> `[]` | `test_openrouter_topic_extractor.py` | Graceful degradation |
| Cap at 5 topics | `test_openrouter_topic_extractor.py` | Same limit as conversation.py |
| Routes to extractor when injected | `test_conversation.py` | `call_decider` NOT called |
| Falls back to Claude when no extractor | `test_conversation.py` | `call_decider` IS called |
| Config defaults | `test_config.py` | `topic_extraction_backend = "claude"` |

### Integration tests

- Existing `tests/integration/test_conversation_flow.py` must pass unchanged
  (it mocks `extract_topics` at the method level, so backend routing is transparent).

### Golden-prompt regression

- `extract_topics` does not have a golden file today. The prompt is inline
  (`src/conversation.py:106-112`). No golden regression test needed — the
  prompt text is not changed by this plan.

### Observability

- `[TOPIC EXTRACT backend=claude|openrouter ms=N topics=M]` log line on every call.
- Existing `[MEMORY UPDATE conv=X topics=N turn_len=M]` log (generator.py:322)
  continues to work and shows topic count downstream.

---

## Open Questions

- [ ] **Which OpenRouter model for topic extraction?** — Plan defaults to
  `google/gemini-2.0-flash-exp:free` (zero cost, fast). May want
  `google/gemini-3.1-flash-lite-preview-20260303` (same as generator) for
  consistency. Decision needed before Story 3 config, but easy to change later.
- [x] **Should `response_format: {"type": "json_object"}` be used?** — RESOLVED:
  No. The prompt instructs the model to return a bare JSON array; defensive
  parsing (`_extract_first_json_array()`) handles malformed output. Skipping
  `response_format` avoids the top-level-object requirement that some providers
  enforce in JSON-object mode, which conflicts with the bare-array format.

---

## Follow-ups (not in this plan)

1. **Flip default to OpenRouter** — after manual production validation, change
   `topic_extraction_backend` default from `"claude"` to `"openrouter"`.
2. **Fix the latent Claude-path bug in `extract_topics` parsing** —
   `parse_decider_response` rejects bare JSON arrays as "not a dict"; the
   post-call parser falls through to `[]`. Separate cleanup.
3. **Dead-code cleanup** — consider retiring `DeciderProcessor` from
   `src/decider.py` as dead code in a separate cleanup plan.
4. **SDK lock elimination** — once `extract_topics` is off Claude, the only
   `call_decider` consumers are in dead code. The SDK lock contention between
   topics and actions is eliminated.

---

## Architecture Decision Record (ADR)

### Decision

Move `ConversationManager.extract_topics()` from the Claude SDK backend to a
new `OpenRouterTopicExtractorCLI` module, opt-in via `topic_extraction_backend`
config flag, defaulting to `"claude"`.

### Drivers

1. **Claude API call count per turn** — every turn currently spends one
   `call_decider` round-trip on topic extraction, a task that needs only JSON
   array extraction (not tool use, not reasoning).
2. **Background memory update latency** — `extract_topics` holds the SDK lock
   while competing with `call_action`, serializing background memory updates
   behind action execution.
3. **SDK lock contention** — `AgentSDKCLI` uses an `asyncio.Lock`
   (line 84-89). Moving topic extraction off Claude frees the lock for
   `call_action`, the higher-priority consumer.

### Alternatives considered

- **A. New `OpenRouterTopicExtractorCLI` module (chosen)** — non-streaming,
  `httpx`-based, JSON-mode client dedicated to topic extraction.
- **B. Extend `OpenRouterCLI` with a non-streaming method (rejected)** — mixes
  streaming and non-streaming concerns in a single class; violates separation
  of concerns principle.
- **C. z.ai with OpenRouter fallback like the generator (rejected)** — heavier
  dependency (Anthropic streaming SDK), topic extraction is too small a use-case
  to justify multi-provider complexity, and z.ai is streaming-only which is a
  poor fit for a single JSON response.

### Why chosen

Option A has the narrowest scope: one new file, one new constructor parameter,
one config flag. Default-safe rollout (`topic_extraction_backend = "claude"`)
means zero risk to `main`. Frees the SDK lock from contention with
`call_action`. Fixes the latent Claude-path topic extraction parser bug *for
opted-in users* by routing around it entirely.

### Consequences

**Positive:**
- Observable production gains via `[TOPIC EXTRACT backend=openrouter ms=N topics=M]`
  log lines.
- Reduced SDK lock contention — `call_action` no longer competes with
  `extract_topics` for the lock.
- Opt-in rollout — operators flip one config field; no code deploy to revert.
- No risk to default `main` behavior.

**Negative:**
- Second runtime dependency on OpenRouter for memory updates (in addition to
  the generator).
- Silent failure mode when OpenRouter is down — mitigated by the
  consecutive-failure WARN log (fires after 3+ failures) and the existing
  fire-and-forget error handling in `_background_memory_update`.

### Follow-ups

1. Flip `topic_extraction_backend = "openrouter"` as the default after manual
   production validation.
2. Fix the latent Claude-path bug in `extract_topics` parsing
   (`parse_decider_response` rejects bare JSON arrays as "not a dict").
3. Consider retiring `DeciderProcessor` as dead code in a separate cleanup plan.
