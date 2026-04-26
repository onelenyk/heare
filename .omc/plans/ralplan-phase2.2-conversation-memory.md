# RALPLAN — heare Phase 2.2: Conversation Memory Re-wire

Phase 2.2 turns the generator from a forgetful reply machine into a bot
that references prior context naturally. Most of the code already exists
(`src/conversation.py` from pre-Phase-2.1); this PRD re-wires it through
the post-2.1 architecture, feeds action results into it, and extends the
generator prompt to consume conversation context.

## RALPLAN-DR Summary

### Principles

1. **Conversation memory never blocks the reply loop.** Topic extraction
   and summary updates run in the background; the generator always has
   a best-effort context snapshot available.
2. **Action results are first-class conversation context.** The bot
   should be able to say "раніше запустив echo hi" without the user
   needing to re-mention the action.
3. **Context window is bounded.** With every turn landing in memory,
   summaries/topics/entities must age out; we cap what flows into the
   generator prompt.
4. **One source of truth for context shape.** `ContextBuilder` owns the
   projection from raw memory → generator prompt variables. Generator
   does not reach into ConversationManager directly.
5. **Graceful degradation.** If topic extraction or summary updates
   fail, the conversation loop continues with stale-but-usable context.

### Decision Drivers

1. Generator TTFT must stay ≤2s even with conversation context in prompt
2. Topic extraction (an extra Claude call) must NEVER block the reply
3. Action results must reach conversation context before the NEXT turn
   (so the bot can reference them naturally)

### Viable Options

**A. Synchronous topic extraction on turn complete** ❌ rejected
- Pros: simplest; context is always fresh
- Cons: adds ~1-3s per turn — kills Phase 2.1's TTFT gain.

**B. Background topic extraction (asyncio.create_task)** ✅ chosen
- Pros: topic extraction never blocks; context freshness within 1 turn
- Cons: brief window where a topic isn't in context yet (acceptable)

**C. Batch extraction every N turns**
- Pros: lowest LLM cost
- Cons: bot references are stale by up to N turns; bad UX
*Rejected:* Option B gives near-realtime at small cost overhead.

### Tradeoff Tensions

1. **Rich context vs. prompt-token budget.** Including N recent turns +
   summary + entities inflates the system prompt. We cap: 3 recent turns
   verbatim, summary ≤300 chars, top 5 entities. Empirically tunable.
2. **Action-result timing.** Action may complete BEFORE or AFTER the
   user's next utterance. We store in ConversationManager as soon as
   `on_result` fires; the next generator build reads fresh state.

---

## Scope

- Modified: `src/main.py` — always instantiate `ConversationManager`;
  re-wire `on_result`/`on_error` callbacks to write action outcomes
  into `ConversationManager`.
- Modified: `src/context.py` — `build_for_generator` accepts
  `conversation_manager` in `__init__` OR gets it passed in via a new
  method arg; projects conversation fields into the returned ctx dict.
- Modified: `src/conversation.py` — extend to track action outcomes
  (`pending_actions`, `completed_actions`). Topic extraction becomes a
  background task fired after each turn.
- Modified: `prompts/generator.txt` — add placeholders for
  `{conversation_summary}`, `{active_topics}`, `{entities}`,
  `{recent_turns}`, `{recent_actions}`.
- Modified: `src/generator.py` — fire a background topic-extraction
  task on turn complete (non-blocking).
- Modified: `src/pipeline.py` — thread `conversation_manager` into
  `build_pipeline` call.
- New: `tests/test_conversation_memory_phase2.py` — end-to-end that
  submits a turn, waits for background task to settle, verifies next
  turn's context reflects the update.

Out of scope — each gets its own PRD:
- 2.3: Speaker ID (independent)
- 2.4: Confirmation passphrase gate
- 2.5: Engagement modes
- 2.6: Heartbeat proactivity
- 2.7: Cleanup / full decider.py deletion

---

## User Stories

### US-P2.2-01 — ConversationManager gains action-result tracking (lock-protected)

**As** the conversation memory layer
**I need** to track submitted and completed actions in a thread-safe way
**So that** the bot can reference "я щойно запустив X" in later turns
without racing the generator hot path

**Acceptance criteria (revised v3 — addresses critic blocker #1):**
- **Threading model:** all callers of `record_action_*` and
  `build_context` run on the asyncio event loop in the same thread.
  Python dict/deque mutations are atomic under the GIL at the
  opcode level, and no `await` appears in the sync methods — so
  NO lock is needed. Plan previously specified `asyncio.Lock` which
  was contradictory with the "no await" contract.
- `src/conversation.py::ConversationManager` gains:
  - `record_action_pending(intent_id: int, tool: str, args: str) -> None`
    — **synchronous, dict/deque-only write**. No DB I/O. No `await`.
  - `record_action_result(intent_id: int, summary: str) -> None`
    — same contract.
  - `record_action_error(intent_id: int, error: str) -> None`
    — same contract.
  - An internal `_action_log: collections.deque` bounded to
    `maxlen=16`; each entry `{id, tool, args, status, result?, error?, ts}`.
    `deque.append` + `maxlen` is atomic; oldest drops automatically.
  - `build_context` iterates `list(self._action_log)` (shallow copy
    at read time — deque iteration is atomic at Python level).
- `build_context(conversation_id)` returns a new key
  `recent_actions: list[dict]` — last 5 entries, newest first,
  taken from `_action_log`.
- **DB-write boundary:** `_action_log` is flushed into
  `conversation.entity_map["actions"]` ONLY inside `update_summary`
  (which runs from `_background_memory_update` in US-P2.2-02,
  never the hot path).
- tests/test_conversation_memory_phase2.py has ≥5 tests:
  (a) action lifecycle pending→done updates _action_log in order;
  (b) action error path records status="error" with error text;
  (c) deque cap of 16 — 17th push drops oldest (maxlen behavior);
  (d) build_context returns at most 5 newest in reverse order;
  (e) interleaved record_action_* + build_context via asyncio.gather
  of 50 alternating coroutine ops completes without error
  (functional smoke; no lock required on single event loop).

### US-P2.2-02 — Background topic extraction in GeneratorProcessor

**As** the GeneratorProcessor
**I need** to fire topic extraction + summary update as a background task
**So that** the next turn's reply has updated context without adding
latency to the current turn

**Acceptance criteria:**
- `GeneratorProcessor.__init__` accepts optional `conversation_manager`
- On successful turn-complete (after LLM stream ends + TTS text pushed):
  - Spawn `asyncio.create_task(self._background_memory_update(...))`
  - Never await the background update inside `_handle_transcription`
    — the turn is "done" once TTS frames are pushed.
- **conversation_id acquisition (addresses critic #3):**
  At the TOP of `_handle_transcription` (before LLM call), call
  `conversation_id = await conversation_manager.get_or_create_active()
  if conversation_manager else None`. Pass this to
  `context_builder.build_for_generator(..., conversation_id=conversation_id)`.
  One DB read per turn, <10ms budget; acceptable given the full
  TTFT budget is ~2000ms.
- `_background_memory_update(transcript: str, reply_text: str, conversation_id: int)`:
  1. If `settings.topic_extraction_enabled`:
     `topics = await conversation_manager.extract_topics(reply_text)`
     else `topics = []`.
  2. `await conversation_manager.update_summary(conversation_id,
     transcript + " " + reply_text, topics)` — this is where
     `_action_log` is flushed into `entity_map` per US-P2.2-01.
  3. Log `[MEMORY UPDATE conv=X topics=N turn_len=M]`.
- Background task exceptions logged, NEVER crash or reach user.
- tests/test_generator.py adds ≥3 tests: happy-path memory update,
  extract_topics failure swallowed, conversation_manager=None no-op.

### US-P2.2-03 — ContextBuilder.build_for_generator surfaces memory

**As** the generator prompt renderer
**I need** conversation summary/topics/entities/recent_turns/recent_actions
in the context dict
**So that** prompts/generator.txt can reference them naturally

**Acceptance criteria (revised v3 — addresses critic blockers #2 and #3):**
- `ContextBuilder.__init__` accepts optional `conversation_manager`
  (already exists — verify still present post-2.1)
- **`build_for_generator` signature extended:**
  `build_for_generator(transcript: str, persona: str,
  conversation_id: int | None = None)`. Internally it calls
  `self.build(transcript=transcript, heartbeat=False,
  conversation_id=conversation_id)` — this is the ONLY path that
  flows real conversation data through to the generator prompt.
- **conversation_id acquisition (resolves critic #3):** caller
  (GeneratorProcessor, see US-P2.2-02) is responsible for acquiring
  the active id once per turn via
  `await conversation_manager.get_or_create_active()` at the top
  of `_handle_transcription` (ONE DB read, <10ms budget). The
  result is passed to `build_for_generator`. If
  `conversation_manager is None`, caller passes
  `conversation_id=None` and all conversation fields degrade to
  empty strings.
- **Drift-guard math (resolves critic #2):**
  - `_EXCLUDED_FROM_GENERATOR_CTX` in `src/context.py` —
    REMOVE 4 keys: `conversation_summary`, `active_topics`,
    `entities`, `recent_turns`. Those keys are now PROJECTED
    through from `build()` into `build_for_generator` output.
    KEEP `conversation_active` in `_EXCLUDED` (it's a yes/no flag
    used internally by `build()` and not surfaced to the generator
    prompt).
  - `recent_actions` is a NEW key that `build_for_generator`
    injects AFTER projecting from `build()`. It does NOT appear
    in `build()`'s output. This leaves the drift test's math
    intact: `set(build()) - set(build_for_generator())` still
    equals the new `_EXCLUDED_FROM_GENERATOR_CTX` (7 keys), and
    bfg-only keys (persona, transcript, recent_actions) do not
    appear in `build()`, matching the existing pattern.
  - `tests/test_context.py::test_build_for_generator_returns_minimal_keys`
    updated to expect the new key set (10 keys total):
    `{"time", "timezone", "persona", "recent_transcripts",
      "transcript", "conversation_summary", "active_topics",
      "entities", "recent_turns", "recent_actions"}`.
  - `tests/test_context.py::test_context_builder_keys_accounted_for`
    continues to pass after both sides update in lockstep.
- **build_for_generator output keys:**
  - `conversation_summary: str` (≤300 chars; trimmed if longer)
  - `active_topics: str` (comma-separated, top 5)
  - `entities: str` (formatted via `_format_entities`)
  - `recent_turns: str` (formatted via existing `_format_recent_turns`)
  - `recent_actions: str` (new `_format_recent_actions` helper —
    "- [14:23] ✓ додав хліб" / "- [14:25] ⋯ пошук рейсів" /
    "- [14:27] ✗ помилка: ...")
- When `conversation_manager is None` OR `conversation_id is None`:
  all five keys render as empty strings / `(none)` placeholders
  from the existing default branch in `build()`.
- tests/test_context.py: ≥3 new tests: build_for_generator with
  conversation_id populates real fields; recent_actions formatting
  edge cases (empty, pending only, mixed); recent_actions limit
  enforced at 5 entries.

### US-P2.2-04 — Generator prompt template update

**As** the generator prompt
**I need** placeholders for the new conversation context
**So that** Gemini can reference prior topics + actions naturally

**Acceptance criteria:**
- `prompts/generator.txt` adds a "Контекст розмови" section BEFORE the
  user's new input:
  ```
  Контекст розмови:
  - Підсумок: {conversation_summary}
  - Активні теми: {active_topics}
  - Сутності: {entities}
  - Останні репліки: {recent_turns}
  - Нещодавні дії: {recent_actions}
  ```
- Instructions: "Використовуй цей контекст, щоб природно згадувати
  попередні теми або дії, але НЕ повторюй їх буквально без потреби"
- tests/test_generator_prompt.py adds: all 5 placeholders present +
  substitution leaves no placeholders

### US-P2.2-05 — Action worker callbacks write into ConversationManager

**As** `main._cmd_start` wiring
**I need** `on_result` / `on_error` action callbacks to update
ConversationManager
**So that** completed actions appear in the next turn's context

**Acceptance criteria (revised v2 — addresses architect #2):**
- `_on_action_result(intent, summary)` calls
  `conversation_manager.record_action_result(intent.id, summary)`
  BEFORE logging. This is a lock-guarded dict write (US-P2.2-01),
  NOT a DB operation — no `await` can sneak in.
- `_on_action_error(intent, exc)` calls
  `conversation_manager.record_action_error(intent.id, str(exc))`.
  Same dict-only contract.
- On intent submission by GeneratorProcessor: also call
  `conversation_manager.record_action_pending(intent_id, tool, args)`
  — synchronous dict-only call in the generator hot path. The
  generator does NOT `await` this (US-P2.2-01 methods are sync).
- **No DB I/O anywhere in these callback paths.** DB flush of
  `_action_log` → `entity_map["actions"]` happens only inside
  `_background_memory_update` in GeneratorProcessor (US-P2.2-02),
  which is a background task that never blocks the hot path.
- tests/test_main.py adds: stub claude_cli + conversation_manager,
  submit an intent, verify record_action_pending + record_action_result
  both called in correct order, AND verify no hot-path await on
  conversation_manager in the generator (smoke-check by asserting
  record_action_pending is NOT a coroutine function).

### US-P2.2-06 — ConversationManager always instantiated in main.py

**As** `main._cmd_start`
**I need** to always create a ConversationManager (flag removed)
**So that** the generator has memory by default

**Acceptance criteria (revised v2 — addresses architect #4):**
- `main.py` unconditionally creates `ConversationManager(store, claude_cli)`
- `ContextBuilder` constructed with `conversation_manager` arg
- **Config-flag semantics (resolves collision with existing
  `topic_extraction_enabled`):**
  - `settings.conversation_memory_enabled` — KEPT, semantics UNCHANGED
    from pre-2.2 (overall feature toggle). Default stays `False` in
    code; users who want memory opt in via config.toml. No silent
    semantic change for existing config files.
  - `settings.topic_extraction_enabled` — the existing flag at
    `src/config.py:93` (default `True`, previously unused) becomes
    the narrow gate for the Claude topic-extraction call. When
    `False`, `_background_memory_update` skips the
    `conversation_manager.extract_topics` call but still runs
    `update_summary` (cheap, DB-only).
  - Users who previously set `conversation_memory_enabled = false`
    continue to get no conversation memory at all. No migration needed.
  - Users who want everything except the Claude-based topic extraction
    can set `topic_extraction_enabled = false` while keeping
    `conversation_memory_enabled = true`.
- **main.py Phase 2.2 default:** if `conversation_memory_enabled` is
  absent from config.toml AND OPENROUTER_API_KEY is set (the 2.1
  minimum baseline), instantiate `ConversationManager` regardless.
  This makes the default experience rich-context without requiring
  existing users to add a new flag. Mechanic: `main.py` checks
  `settings.conversation_memory_enabled or settings.openrouter_api_key`
  to decide; when false-by-config-default, runs without memory.
- Graceful startup: if Claude CLI is unavailable at topic-extract
  time, log and continue (reply loop not impacted).
- `tests/test_feature_flags.py` gains a test that covers the new
  semantic: `topic_extraction_enabled=False` skips Claude call but
  memory still flows; and a regression-guard that confirms
  `conversation_memory_enabled=False` still disables the whole feature
  (no migration break).

### US-P2.2-07 — TTS-leak scrubber + saturated-context guard (architect #5, #6)

**As** the generator
**I need** a parser-level defense against tool-name literals reaching TTS
**So that** the bot never speaks "bash" or `{"tool":...}` aloud even if
Gemini temporarily ignores the prompt rule

**Acceptance criteria (new in v2):**
- `src/generator.py::_scrub_tts_text(text: str) -> str` — post-parser
  sanitizer applied BEFORE `push_frame(TTSSpeakFrame(text))` in
  `_handle_transcription`. Strips or neutralizes literals that would
  sound wrong spoken aloud:
  - Standalone `bash` / `Bash` token (when not inside a longer word)
  - Literal `{"tool":` / `"args":` / `</intent>` / `<intent>` fragments
    that slipped through the parser
  - Common JSON punctuation clusters: `{}`, `[]`, trailing `":`
- Implementation: compiled regex list, conservative replacements
  (usually just drop the token). If the sanitized text becomes empty,
  log a warning and skip the push (better silent than speaking literals).
- Tests in `tests/test_generator.py` ≥3:
  - `test_tts_scrubber_removes_tool_names` — input
    `"Добре, запустив bash echo hi."` → TTS receives
    `"Добре, запустив echo hi."` (or similar, no `bash` word)
  - `test_tts_scrubber_handles_json_fragment_leak` — input
    `"Зроблю зараз {"tool":"bash","args":"x"} далі."` → no JSON bytes
    reach TTS
  - `test_tts_scrubber_sentinel_passthrough` — clean text with the
    word `bashful` (unrelated) unchanged

### US-P2.2-07b — Saturated-context intent-compliance guard (architect #6)

**As** the prompt/parser pipeline
**I need** to verify Gemini still emits the intent tag correctly when
the system prompt is at its token budget limit
**So that** Phase 2.1's tag-compliance guarantee doesn't regress under
Phase 2.2's added conversation context

**Acceptance criteria:**
- `tests/test_generator.py::test_intent_emission_under_saturated_context`
  — construct a GeneratorProcessor with a mocked OpenRouter that
  requires the rendered prompt to contain the INTENTS section before
  yielding a valid `<intent>...</intent>` tag; assert the tag still
  parses cleanly even when the context includes 3 recent turns
  (50+ chars each), 5 topics, 5 entities, a 300-char summary, and
  5 recent actions.
- Uses existing IntentStreamParser and asserts parser output includes
  one intent + no `<` leakage.

### US-P2.2-08 — Regression + lint

**As** the codebase
**I need** all tests green, lint clean
**So that** Phase 2.2 doesn't break 2.1.

**Acceptance criteria:**
- `make test` exits 0 (target: 543 + ≥13 new Phase 2.2 tests ≈ ≥556)
- `make lint` exits 0
- No new skipped tests
- Integration test `test_intent_flow.py` still passes (2.1 regression)

### US-P2.2-09 — Architect sign-off + live smoke

**As** the plan maintainer
**I need** architect APPROVE + a happy-path live smoke
**So that** the rich-context bot really works end-to-end.

**Acceptance criteria:**
- All US-P2.2-01..07 passes=true
- `make test` + `make lint` green
- Architect APPROVED
- Live smoke scenarios:
  1. Say "завтра купити хліб" → bot replies
  2. Wait 3s (background memory update settles)
  3. Say "що я хотів зробити?" → bot's reply references "хліб" from context
  4. Say "запусти echo done" → intent submitted + completes
  5. Say "чи все вдалося?" → bot's reply references the echo action success
- Results written to `.omc/benchmarks/phase2.2-live-smoke.md`
- Deslop pass on changed files (or `--no-deslop` documented)

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Topic extraction Claude call adds 3s on first turn | Medium | Low | Background task — never on hot path |
| Prompt token bloat degrades Gemini quality | Medium | Medium | Context caps (3 turns / 300-char summary / top 5 topics / top 5 actions) + A/B test in live smoke |
| Action reference leaks into TTS ("я щойно виконав bash...") | Low | Medium | Prompt instructs "reference naturally, don't recite tool names" |
| Memory update fires but bot never references it | Low | Low | Gemini quirk; live smoke scenario #5 validates |
| Pending action status drifts (action finishes but context still "pending") | Medium | Medium | `on_result` updates sync before logging; background task ordering verified in tests |
| ConversationManager raising in hot path kills TTS | Low | High | All calls into ConversationManager from generator's hot path wrapped in `try/except` with warning log |

---

## ADR

- **Decision:** Re-wire existing `src/conversation.py` through the
  post-2.1 architecture. Topic extraction becomes background; action
  outcomes tracked in ConversationManager. Generator prompt expanded
  with 5 new placeholders.
- **Drivers:** (1) "Conversation never blocks on actions" now extends
  to "context doesn't block on extraction"; (2) bot must be able to
  reference past actions naturally (key Phase 2 UX promise).
- **Alternatives considered:** sync extraction (too slow), batch
  extraction (stale refs). Both rejected.
- **Why chosen:** Preserves 2.1 latency budget; minimal new code
  (~200 LOC); uses existing ConversationManager almost verbatim.
- **Consequences:** Prompt size grows by ~500 tokens; Claude call
  count per turn increases from 0 to ~1 (background); conversation
  schema gains action fields.
- **Follow-ups:** 2.3 speaker_id / 2.4 confirmation / 2.5 modes.
