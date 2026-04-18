# RALPLAN — heare Phase 2.1: Intent Queue + Async Action Worker

First execution chunk of Phase 2. Adds the core architectural piece the
two-phase plan has been building toward: the generator emits intents
inline, a background worker executes them via Claude CLI with tools, and
results flow back into conversation context so the bot can reference them
naturally in later turns.

This PRD does NOT reintroduce speaker ID, confirmation, modes, heartbeat
substance, or ConversationManager — those get their own Phase 2.x PRDs.

> **Revision history:**
> - v2 after Architect ITERATE — tightened US-P2.1-01 parser invariants,
>   resolved cancellation call site, split US-P2.1-07 into 07a/07b,
>   specified sentence-buffer/parser interaction, amended risk register,
>   added forced-failure live smoke scenarios.
> - v3 after Critic ITERATE — (C1) defined ActionWorker dispatch contract
>   (how bash tool is actually invoked via claude CLI), (C2) added
>   `action_timeout_seconds` to config + wiring, (M1) zombie-subprocess
>   kill moved into 2.1 scope, (M2) forced-failure harness specified
>   (env-var + pytest -m live), (M3) fixed decider-import grep pattern,
>   (M4) replaced test arithmetic with per-file floors, (M5) dropped
>   "стоп" from cancel keywords. Plus minor fixes: max_pending cap,
>   test_tts_cache.py import update, __all__ cleanup, worker-before-
>   greeting ordering.

## RALPLAN-DR Summary

### Principles

1. **Conversation never blocks on actions.** The generator produces a reply
   to every turn, immediately. Actions run in the background. This is the
   whole reason we moved to the two-phase architecture.
2. **Intents are data, not control flow.** The generator emits a structured
   payload; the worker decides what to do with it. Decoupled for testing
   and for future backend swaps.
3. **Single writer per concern.** Intent queue is the only intermediary
   between generator and worker. Worker is the only caller of Claude CLI
   for the `act` role. No shortcuts.
4. **Graceful degradation.** If the worker crashes, OpenRouter stalls, or
   Claude CLI fails, the conversation loop stays alive. Users notice
   "it couldn't do that" via a spoken error, not by the bot going silent.
5. **Emergency-rollback flag removable.** Landing Phase 2.1 is also the
   milestone for removing `generator_mode`. Legacy decider path becomes
   unreachable at the end of this PRD.

### Decision Drivers

1. Action execution latency ≤ conversation latency (user never waits for
   action completion before getting next conversational reply)
2. Worker failures must surface to the user without silencing the bot
3. `generator_mode` flag must be removed by end of this PRD (per
   US-P1-08 milestone commitment)

### Viable Options

**A. In-process asyncio queue + task** ✅ chosen
- Pros: zero deps, testable, matches the "single heare process" model,
  async-native so cancellation and timeouts are trivial
- Cons: bounded to a single process; no persistence across daemon restarts
  (acceptable for Phase 2.1 — persistence is a 2.x concern if needed)

**B. SQLite-backed queue**
- Pros: survives restarts, queryable for debugging
- Cons: polling overhead; migration burden; heare restarts blow away mic
  state anyway, so cross-restart durability buys little.
*Rejected:* revisit only when 2.4 (confirmation) or 2.6 (heartbeat) needs
it.

**C. Structured tool-calling via LLM function-call API**
- Pros: canonical, robust, no XML parsing
- Cons: OpenRouter support varies by model; Gemini 3.1 Flash Lite
  function-calling is inconsistent in practice.
*Rejected for Phase 2.1:* XML tags are the simpler ship-now path. Re-
evaluate in 2.7 if parser bug-rate exceeds 1/100 turns.

### Tradeoff Tensions (honest)

1. **Flag removal vs. post-merge safety net.** The flag is the only
   one-restart rollback we have today. Phase 2.1 adds four new failure
   modes (parser, queue, worker, claude-cli tool-use hang). Losing the
   flag in the PRD that adds the most new failure surface looks risky.
   We resolve this by (a) honoring the Phase 1 commitment, (b)
   strengthening US-P2.1-10 live-smoke to include three forced-failure
   scenarios BEFORE merge, so post-merge rollback is by `git revert`
   of a commit we know is safe under failure.

2. **Intents are data vs. cancellation needs control.** Cancellation is
   control (interrupt something happening). The plan chooses
   generator-side keyword detection for "скасуй" — minimal keyword list,
   lives in `src/generator.py`, calls `queue.cancel_latest()` directly
   before invoking the LLM. This keeps the intent payload pure data and
   isolates the one cancellation-keyword bit of control in the processor
   where it's visible. In-flight cancellation (killing a running claude
   CLI subprocess) is explicitly scoped out to 2.7.

3. **Parser eager-emit vs. no tag leakage.** If parser holds buffered
   text until closing tag arrives, TTFT goes up. If it emits eagerly, a
   stalled tag can leak `<` to TTS. Resolution: parser holds bytes from
   the first `<` onward until it can disprove the intent-tag prefix
   match, then releases them. Adds at most a few hundred ms on any turn
   that has an intent.

---

## Scope

- New: `src/actions.py` (IntentQueue, ActionWorker, Intent dataclass)
- New: `src/intent_parser.py` (streaming XML tag extraction)
- New: `src/tts_phrases.py` (relocated FIXED_PHRASES — completes Phase 1
  US-P1-11's deferred work)
- Modified: `src/generator.py` (emit intents from LLM stream, cancellation
  keyword gate, filter tags from TTS, import FIXED_PHRASES from new home)
- Modified: `src/main.py` (start/stop worker task, remove generator_mode
  branch, update FIXED_PHRASES import)
- Modified: `src/config.py` (remove generator_mode, add worker settings)
- Modified: `src/pipeline.py` (remove flag branch — only generator path)
- Modified: `src/decider.py` (re-export FIXED_PHRASES from tts_phrases for
  backward compat with any remaining importers; noted for 2.7 deletion)
- Modified: `prompts/generator.txt` (teach generator the `<intent>` syntax)
- Tests: new actions + intent_parser suites; existing tests updated

Out of scope — each gets its own PRD:
- 2.2: ConversationManager re-wire (action results → context)
- 2.3: Speaker ID
- 2.4: Confirmation passphrase (flagged intents waiting for passphrase)
- 2.5: Engagement modes
- 2.6: Heartbeat proactivity
- 2.7: Cleanup / Ollama backend option / in-flight cancellation /
  decider.py deletion

---

## User Stories

### US-P2.1-01 — Intent parser (streaming-safe, anti-leakage)

**As** the generator
**I need** a parser that extracts `<intent>...</intent>` blocks from a
streaming token iterator without leaking partial tags to TTS
**So that** reply text streams freely to TTS and intents reach the queue
cleanly regardless of how Gemini chunks its output

**Acceptance criteria:**
- `src/intent_parser.py` has class `IntentStreamParser`
- `parser.feed(chunk: str) -> tuple[str, list[dict]]` — returns
  `(emittable_text, completed_intents)`
- `parser.flush() -> tuple[str, list[dict]]` — called at end-of-stream to
  release any held bytes that turned out NOT to be a tag prefix; logs
  warning if a tag is still unclosed
- **Streaming-safety invariants** (each individually tested):
  1. **No `<` leakage.** The parser MUST NOT emit any byte starting from a
     `<` until it can prove the `<` is not the start of `<intent>` or
     `<INTENT>` (case-insensitive). Implementation: greedy hold starting
     at `<`; release held bytes as plain text the moment a byte arrives
     that violates the `<intent>` prefix.
  2. **Markdown fence tolerance.** The parser MUST strip
     ```` ```json ```` and ```` ``` ```` fences **inside or around**
     `<intent>` blocks before JSON.parse. Fences in plain reply text are
     left alone (they'll rarely be emitted by the prompt, but if they
     are, that's a different concern).
  3. **Opener normalization.** Accepts `<intent>`, `<INTENT>`, `<Intent>`,
     `< intent >`, `<intent  >`. Rejects anything else — those bytes flush
     to emittable_text.
  4. **Trailing prose tolerance.** Accepts `<intent>{...}</intent> далі…`
     — the trailing `" далі…"` must reach emittable_text.
  5. **JSON body validity.** `json.loads(body)` fails → log warning, drop
     intent, emittable_text unchanged. Do NOT leak the raw tag bytes.
  6. **Nested-tag rejection.** `<intent><intent>...</intent></intent>` →
     treat outer as malformed, drop, log warning.
  7. **Emoji/whitespace between opener and JSON.** `<intent>\n  🎉  {...}`
     — whitespace/control chars consumed silently; emoji bytes cause
     JSON.parse failure → drop silently (intent dropped, no leakage).
- **One intent per stream** in Phase 2.1. A second `<intent>...` block
  in the same LLM response is parsed and silently dropped (logged).
- tests/test_intent_parser.py ≥11 tests:
  (a) tag whole in one chunk; (b) tag split across 2 chunks between
  opener bytes (`<int` + `ent>{...}</intent>`); (c) tag split across 3
  chunks around the JSON body; (d) malformed JSON inside body logged
  and dropped; (e) no-tag stream passes through unchanged; (f) tag after
  reply text emits text first then intent; (g) text-after-closing-tag
  reaches emittable_text; (h) markdown fence around tag stripped;
  (i) uppercase `<INTENT>` accepted; (j) `<otherthing>` bytes flush to
  text (no false-positive hold); (k) end-of-stream with unclosed tag
  logs warning and releases held bytes

### US-P2.1-02 — Intent schema + prompt update

**As** the generator prompt
**I need** clear instructions for emitting intents
**So that** Gemini produces parseable payloads consistently

**Acceptance criteria:**
- `prompts/generator.txt` updated with an `INTENTS` section describing:
  - When to emit: user explicitly asks for an action
    ("запусти X", "додай до списку", "знайди Y", "зроби Z")
  - Format: ONE intent per reply, AFTER reply text, on its own line:
    ```
    <intent>{"tool":"bash","args":"<command>"}</intent>
    ```
  - Reply text is spoken; intent tag is NEVER spoken
  - Do NOT wrap the tag in markdown fences
- The parser (US-P2.1-01) tolerates prompt violations (fences, wrong
  order) — but the prompt states the preferred shape
- Golden test in tests/test_generator_prompt.py verifies the INTENTS
  section exists and that the schema documented in the prompt is the
  schema `IntentStreamParser` accepts

### US-P2.1-03 — IntentQueue + cancellation (pending only)

**As** the action worker
**I need** an async queue I can pull intents from
**So that** intents are processed sequentially without blocking the
conversation loop

**Acceptance criteria:**
- `src/actions.py` has class `IntentQueue` and `@dataclass Intent` with
  fields: `id: int`, `tool: str`, `args: str`, `raw: dict`,
  `submitted_at: float`
- `async submit(intent_payload: dict) -> int | None` — wraps in `Intent`,
  assigns per-daemon-boot monotonically-increasing `id` (starts at 1 on
  each daemon start; NOT persisted across restarts — documented), enqueues,
  returns `id`. If queue already holds `max_pending` items, logs a warning
  `[INTENT DROPPED — queue full]` and returns None (drop policy; no
  back-pressure to caller).
- `async next() -> Intent` — blocks until an intent is available
- `cancel_latest() -> Intent | None` — **synchronous** — removes and
  returns the NEWEST still-pending intent, or None if queue empty.
  Does NOT cancel intents already being executed by the worker.
- `pending_count() -> int` — size of queue
- `max_pending: int = 32` (constructor arg; sourced from
  `settings.intent_queue_max_pending` via US-P2.1-07a config addition)
- In-memory only (wraps `collections.deque` under an asyncio.Event for
  wakeup — chosen over `asyncio.Queue` so cancel_latest can pop from
  the tail)
- **Explicit out-of-scope for Phase 2.1:** in-flight cancellation of the
  currently-executing intent. Canceling "скасуй" when the worker has
  already started the action is deferred to 2.7 (see Risk Register).
- tests/test_actions.py `IntentQueue` tests ≥6:
  (a) submit+next roundtrip; id starts at 1 and increments monotonically;
  (b) FIFO order across 3 submissions;
  (c) cancel_latest removes newest; subsequent next() returns older one;
  (d) cancel_latest on empty queue returns None;
  (e) pending_count matches submissions minus cancels minus nexts;
  (f) submit beyond max_pending returns None and logs warning

### US-P2.1-04 — ActionWorker

**As** the daemon
**I need** a long-running task that pulls from IntentQueue and executes
intents via Claude CLI
**So that** actions happen without blocking the conversation

**Dispatch contract (resolves Critic C1):** `ClaudeBackend.call_action`
takes a natural-language description as its first positional argument and
executes via `claude -p <description> --dangerously-skip-permissions`,
which lets Claude Code use its built-in Bash / Edit / etc. tools. The
worker constructs that description from `intent.tool + intent.args`:

```
description = f"Use the {intent.tool} tool: {intent.args}"
# for tool="bash", args="echo hi": 
#   "Use the bash tool: echo hi"
```

This matches how `DeciderProcessor` dispatches actions today (it
constructs a similar description from `{intent}` + `{action}` fields in
legacy decisions). Claude Code's tool system handles the actual
execution. The integration test (US-P2.1-08) asserts a REAL side-effect
(e.g., a sentinel file written by the bash tool), not a mocked summary.

**Acceptance criteria:**
- `src/actions.py` has class `ActionWorker`
- Constructor:
  `ActionWorker(queue, claude_cli, on_result, on_error, timeout=120.0)`
  — callbacks are `async` with signatures
  `(intent: Intent, result_text: str)` / `(intent: Intent, exc: BaseException)`
- `async run()` loop:
  1. `intent = await queue.next()`
  2. `description = f"Use the {intent.tool} tool: {intent.args}"`
  3. `task = asyncio.create_task(claude_cli.call_action(description))`
  4. `result = await asyncio.wait_for(asyncio.shield(task), timeout=self.timeout)`
  5. On success: `await on_result(intent, result["summary"])`
- Exception during `call_action` → `await on_error(intent, exc)`; loop
  continues on next iteration
- **`asyncio.TimeoutError` handling (resolves Critic M1):** after the
  `wait_for` raises, cancel the inner task AND call
  `claude_cli.kill_running_action()` (new method, see below) to reap
  the claude subprocess. Log
  `[ACTION TIMEOUT id=N killed_subprocess=true/false]`. Fire `on_error`
  with the `TimeoutError`.
- `ClaudeBackend` protocol gains `async kill_running_action() -> bool`
  (default implementation: no-op returning False). Concrete
  `ClaudeCLI` / `AgentSDKCLI` implementations in 2.1 may either
  implement this or return False — Phase 2.1 tolerates "no-op"
  implementations but logs the outcome in the ACTION TIMEOUT line. A
  full kill implementation is tracked as a `kill_running_action`
  follow-up in the ClaudeBackend tests but is NOT a merge blocker
  for 2.1.
- Supports cancellation: `run()` exits cleanly on `CancelledError` from
  the outside; does NOT swallow
- On_result / on_error exceptions are caught and logged — must not
  kill the worker loop
- tests/test_actions.py `ActionWorker` tests ≥7:
  (a) happy path — one intent produces one on_result call; description
      passed to call_action matches `f"Use the {tool} tool: {args}"`;
  (b) exception in call_action triggers on_error and loop continues to
      process the next intent;
  (c) timeout triggers on_error(TimeoutError) AND calls
      `claude_cli.kill_running_action()` exactly once;
  (d) CancelledError propagates out of run() cleanly;
  (e) on_result raises → caught and logged, loop continues;
  (f) on_error raises → caught and logged, loop continues;
  (g) `kill_running_action` default no-op returns False without error

### US-P2.1-05 — Generator integration (intent emission + cancel gate)

**As** the `GeneratorProcessor`
**I need** to route each LLM chunk through `IntentStreamParser`, speak
the non-intent text, submit intents to the queue, and synchronously
cancel the newest queued intent when the user says "скасуй"
**So that** the conversation flows, actions fire, and the user can
recall a pending action

**Acceptance criteria:**
- `GeneratorProcessor.__init__` accepts `intent_queue` as a REQUIRED
  argument (no None default; tests inject a real or fake queue).
  Resolves Critic Minor #2 — no silent-bypass path.
- **Cancellation keyword gate** (generator-side, before LLM call):
  When an incoming transcript matches the regex
  `(?i)(?:^|[\s.,!?—])(скасуй|відміни)(?:$|[\s.,!?—])` (case-insensitive;
  `"стоп"` dropped per Critic M5 because of substring collisions like
  `"стоп-кадр"` / `"автостоп"`; word-boundary tightened by explicit
  separator class), GeneratorProcessor calls
  `intent_queue.cancel_latest()` synchronously, logs
  `[INTENT CANCELLED id=N]` if a pending intent was cancelled,
  still calls the LLM to generate a reply (user expects spoken
  acknowledgment), and proceeds normally. This is the ONE allowed
  keyword → control bypass, documented inline in the code.
- Negative test cases MUST NOT trigger cancel: `"стоп-кадр"`,
  `"автостоп"`, `"відомо"`, `"скаси"` (typo) — verified by dedicated
  test cases, see below.
- **Per-chunk streaming flow:**
  1. `speech, intents = parser.feed(chunk)`
  2. `speech` (may be empty) is concatenated into the sentence buffer;
     sentence buffer then flushes on terminator as today
  3. Each new intent → `await intent_queue.submit(intent_dict)`; log
     `[INTENT SUBMITTED id=N tool=X]`
  4. If `intent_queue` is None (defensive default for tests), intents
     are logged and dropped
- **End-of-stream:**
  - After OpenRouter stream ends, call `parser.flush()` to release any
    held bytes + handle unclosed tag
  - Then flush the sentence buffer tail as today
- **Parser/sentence-buffer invariant:** the sentence buffer never sees
  `<` bytes that belong to an intent tag — parser's anti-leakage
  guarantee (US-P2.1-01 invariant #1) is the contract boundary. A
  mid-stream test exercises this:
  stream emits `"Додам. "`, `"<intent>{\"tool\":\"bash\","`,
  `"\"args\":\"x\"}</intent>"`, `" далі щось."` — TTS sees exactly
  `"Додам."` and `"далі щось."`; IntentQueue gets exactly one intent
- End-of-turn log: `[TIMING] generator transcript="..." ttft=Xms
  chunks=N intents=M cancelled=K` (K≥0)
- tests/test_generator.py adds:
  - `test_intent_emission_and_tts_separation` — stream emits reply +
    intent, verify no tag in TTS and one intent in queue
  - `test_mid_stream_tag_no_leakage` — stream interleaves
    `"Додам. "` + `"<intent>{json}"` + `"</intent> далі."` across
    3 chunks; assert TTS receives exactly `"Додам."` and `"далі."`,
    IntentQueue receives exactly one intent, no `<` in any TTSSpeakFrame
  - `test_cancel_keyword_pops_pending_intent` — queue has one intent;
    next transcript contains `"скасуй"`; verify `cancel_latest` called
    and `[INTENT CANCELLED]` logged
  - `test_cancel_keyword_on_empty_queue_logs_nothing_special` — same
    but queue empty; no error, generator still replies
  - `test_cancel_keyword_negative_cases` — transcripts `"стоп-кадр"`,
    `"автостоп"`, `"скаси мене"` (typo, different stem) do NOT call
    `cancel_latest`
  - `test_cancel_keyword_positive_edge_cases` — `"скасуй!"`,
    `"ну скасуй"`, `"скасуй, будь ласка"`, `"відміни замовлення"` DO
    call `cancel_latest`

### US-P2.1-06 — Main daemon wiring

**As** `main._cmd_start`
**I need** to instantiate the queue + worker + wire callbacks
**So that** the worker lifecycle is tied to the daemon lifecycle

**Acceptance criteria:**
- `_cmd_start` creates `IntentQueue(max_pending=settings.intent_queue_max_pending)`,
  `ActionWorker(queue, claude_cli, on_result, on_error, timeout=settings.action_timeout_seconds)`,
  passes queue to `GeneratorProcessor` via `build_pipeline`
- Worker callbacks in Phase 2.1 just log:
  `[ACTION RESULT id=N] summary=...` / `[ACTION ERROR id=N] exc=...`
  (ConversationManager wiring is 2.2)
- **Startup ordering (resolves Critic Missing #4):** the worker task
  starts BEFORE the deferred-greeting task so any intent a too-eager
  generator submits during boot has a live queue consumer. The ordering
  is deterministic, not based on sleep races:
  1. Build pipeline + IntentQueue + ActionWorker (synchronous)
  2. Start `worker_task = asyncio.create_task(worker.run())`
  3. Start `runner_task = asyncio.create_task(runner.run(pipeline))`
  4. Schedule the deferred greeting task (existing 1s delay)
- Worker task is awaited alongside pipeline/heartbeat in
  `run_until_stopped`; cancelled cleanly on SIGTERM / shutdown; any
  pending intents dropped with a warning log
- tests/test_main.py asserts:
  - IntentQueue + ActionWorker instantiated
  - worker task started BEFORE runner task (order via monkeypatched
    `asyncio.create_task` that records invocation order)
  - worker task cancelled on daemon shutdown
  - worker callbacks fire `[ACTION RESULT]` / `[ACTION ERROR]` log
    lines with expected formatting

### US-P2.1-07a — generator_mode flag removal (plumbing only)

**As** the codebase
**I need** the `generator_mode` flag removed and the legacy pipeline
branch deleted
**So that** we honor the Phase 1 US-P1-08 commitment and stop paying
the dual-path tax

**Acceptance criteria (each file/line spelled out):**
- `src/config.py`: delete `generator_mode: bool = False` field
- `src/config.py`: ADD `action_timeout_seconds: float = 120.0`
  (resolves Critic C2)
- `src/config.py`: ADD `intent_queue_max_pending: int = 32`
- `src/pipeline.py`: delete the `if settings.generator_mode:` branch
  entirely; only the generator path remains; signature drops
  `conversation_manager` arg (reintroduced in 2.2)
- `src/pipeline.py`: delete `from .decider import create_decider_processor`
  import — no longer needed (resolves Critic M3 audit consequence)
- `src/main.py`: delete the `if settings.generator_mode:` /
  `elif settings.conversation_memory_enabled:` branch (lines currently
  around 150-164); always instantiate `OpenRouterCLI`
- `src/main.py`: delete the onboarding passphrase prompt block
  (currently around 211-227); mark `HEARE_HOME / ".onboarded"` as an
  artifact kept for 2.4 reintroduction (not deleted from disk)
- `src/main.py`: ActionWorker constructor uses
  `timeout=settings.action_timeout_seconds`; IntentQueue uses
  `max_pending=settings.intent_queue_max_pending`
- `README.md`: "Experimental generator mode" section replaced with
  "Architecture" section reflecting the single path
- `tests/test_feature_flags.py`: delete
  `test_generator_mode_default_is_false` and
  `test_generator_mode_both_values_settable`
- `tests/test_config.py`: add tests for new
  `action_timeout_seconds` + `intent_queue_max_pending` defaults
- `tests/test_pipeline.py`: existing decider-pipeline tests that relied
  on `generator_mode=False` — convert to use the new single path OR
  delete if no longer meaningful (case-by-case, documented in commit
  message)

### US-P2.1-07b — decider import surface audit

**As** the codebase
**I need** to enumerate every non-decider import from `src/decider.py`
and relocate or document per-symbol
**So that** `src/decider.py` can be cleanly deleted in 2.7 without
hunting landmines

**Acceptance criteria:**
- Create `src/tts_phrases.py` owning `FIXED_PHRASES: list[str]` —
  completes Phase 1 US-P1-11's deferred relocation
- `src/generator.py` imports `FIXED_PHRASES` from `src.tts_phrases`
- `src/generator.py::__all__` updated: remove `"FIXED_PHRASES"` since it
  no longer originates here (resolves Critic Missing #5)
- `src/main.py` imports `FIXED_PHRASES` from `src.tts_phrases`
- `tests/test_tts_cache.py` updated to import `FIXED_PHRASES` from
  `src.tts_phrases` (resolves Critic Minor #1)
- `src/decider.py` keeps `FIXED_PHRASES` as a re-export
  (`from .tts_phrases import FIXED_PHRASES`) for any remaining in-file
  usages; this line is explicitly flagged for deletion in 2.7
- **Import audit (resolves Critic M3):** run
  `rg 'from (\.|src\.)decider import' src/ tests/` — capture output
  in the commit message under "Decider import audit". Every remaining
  hit must be one of:
  (a) decider-internal (within `src/decider.py` itself) — N/A, we don't
      self-import;
  (b) test-file referencing a decider symbol under test
      (e.g., `tests/test_decider.py`, `tests/test_feature_flags.py`);
  (c) the `src/decider.py` re-export line we just added;
  (d) explicitly documented in the commit as "deferred to 2.x" with
      the target sub-phase
- Pipeline.py + main.py + generator.py MUST NOT appear in the audit
  output after 07a/07b land (they should import nothing from decider)
- No behavior changes — this is a pure refactor; US-P2.1-09 regression
  suite must stay green

### US-P2.1-08 — End-to-end intent-execution smoke

**As** the user
**I need** one-line proof the whole chain works
**So that** we know Phase 2.1 shipped, not just compiled

**Acceptance criteria (resolves Critic C1 — real side-effect check):**
- Integration test `tests/integration/test_intent_flow.py`:
  - Stub `OpenRouterCLI.generate` to yield:
    `"Додам зараз. "`, `"<intent>{\"tool\":\"bash\","`,
    `"\"args\":\"echo hi\"}</intent>"`
  - Use a `FakeClaudeCLI` (NOT AsyncMock) whose `call_action`
    implementation records the `description` argument AND simulates
    the tool-dispatch contract by parsing the description pattern
    `f"Use the {tool} tool: {args}"` — assert `tool=="bash"`,
    `args=="echo hi"`, then return `{"summary": f"ran: {args}"}`.
    This makes the test fail if the worker passes the wrong argument
    shape (C1 regression guard).
  - Build the pipeline with a mocked transport; push a TranscriptionFrame
  - Verify:
    (a) TTS received exactly `"Додам зараз."` as a TTSSpeakFrame
    (b) No `<` characters in any TTSSpeakFrame text
    (c) IntentQueue received exactly one intent with `tool="bash"`,
        `args="echo hi"`
    (d) FakeClaudeCLI.call_action received description matching
        `"Use the bash tool: echo hi"` (exact-match)
    (e) Worker processed and `on_result` was invoked with intent.id=1
        and summary=`"ran: echo hi"` within 2s (test timeout)

### US-P2.1-09 — Regression + lint baseline

**As** the codebase
**I need** all unchanged modules + updated tests to pass
**So that** we know Phase 2.1 didn't break adjacent code

**Acceptance criteria (resolves Critic M4 — per-file floors, no arithmetic):**
- `make test` exits 0
- `make lint` exits 0 on `src/` and `tests/`
- Per-file test-count floors met (not a single-number total):
  - `tests/test_intent_parser.py` ≥ 11 tests (per US-P2.1-01)
  - `tests/test_actions.py` IntentQueue class ≥ 6 tests (per US-P2.1-03)
  - `tests/test_actions.py` ActionWorker class ≥ 7 tests (per US-P2.1-04)
  - `tests/test_generator.py` has ≥ 6 new Phase-2.1 tests
    (streaming emission, no-leakage, 4 cancel-keyword cases)
  - `tests/test_main.py` has ≥ 3 new Phase-2.1 tests (queue+worker
    instantiation, startup ordering, shutdown cancellation)
  - `tests/test_config.py` has ≥ 2 new tests (action_timeout_seconds,
    intent_queue_max_pending defaults)
  - `tests/integration/test_intent_flow.py` exists with the US-P2.1-08
    assertion set
- No skipped tests other than pre-existing optional-dep skips
- `make test` total count is NOT regulated — passing is the only criterion

### US-P2.1-10 — Architect sign-off + live smoke (with forced failures)

**As** the plan maintainer
**I need** the architect to certify implementation matches this PRD AND
the live daemon to survive three forced-failure scenarios
**So that** we can merge and retire `generator_mode` knowing the new
architecture won't page us at 11pm

**Acceptance criteria:**
- All US-P2.1-01..09 passes=true in prd.json
- Architect review: APPROVED verdict
- Live happy-path smoke:
  1. "привіт" → bot replies, NO `[INTENT SUBMITTED]` in log
  2. "запусти echo hello" → bot replies immediately AND
     `[INTENT SUBMITTED id=1 tool=bash]` +
     `[ACTION RESULT id=1] summary=...` within 10s
  3. Submit an intent, then within 3s say "скасуй" → 
     `[INTENT CANCELLED id=N]` logged; no `[ACTION RESULT]` fires
     for id=N
- **Forced-failure smoke (resolves Critic M2 — specified harness):**
  Runs as `pytest -m phase2_live tests/live/test_forced_failures.py`
  (pytest marker `phase2_live` added to `pyproject.toml`; default
  `make test` does NOT collect this marker — opt-in). Each scenario is
  a pytest function that starts a daemon subprocess with specific env
  overrides and asserts expected log lines:

  4. **Malformed intent JSON:** env `HEARE_FAKE_OPENROUTER=malformed_intent`
     makes `OpenRouterCLI` (via a test-only injection hook in
     `src/openrouter_cli.py::OpenRouterCLI.__init__` reading this env
     var — documented as test-only, skipped by default). LLM stream
     emits `<intent>{not json}</intent>`. Assert: log contains
     `[INTENT PARSE ERROR]`, no `[INTENT SUBMITTED]`, reply text still
     pushed to TTS, daemon still running after 3s.
  5. **Claude CLI timeout:** env `HEARE_ACTION_TIMEOUT_SECONDS=2` +
     `HEARE_FAKE_CLAUDE_SLEEP=5` makes `ActionWorker`'s call_action
     stub sleep 5s. Assert log contains `[ACTION TIMEOUT id=N]`,
     `[ACTION ERROR id=N] exc=TimeoutError`, worker still responsive
     to next intent within 1s.
  6. **Burst load:** submit 3 intents programmatically via
     `IntentQueue.submit()` directly (not via LLM — deterministic).
     Assert log shows all three processed in FIFO order
     (`[ACTION RESULT id=1]` before id=2 before id=3), none dropped,
     total elapsed < 3× per-action budget.

- The env-var injection points (`HEARE_FAKE_OPENROUTER`,
  `HEARE_FAKE_CLAUDE_SLEEP`, `HEARE_ACTION_TIMEOUT_SECONDS`) are
  documented as test-only in `src/openrouter_cli.py` + `src/actions.py`
  docstrings; production users should never set them.
- Live happy-path smoke remains manual (mic + user voice), captured
  with timestamps + transcript snippets
- Results captured in `.omc/benchmarks/phase2.1-live-smoke.md`
- Deslop pass on changed files

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LLM emits malformed `<intent>` JSON | High | Medium | US-P2.1-01 parser drops + logs; user still hears reply; US-P2.1-10 forced-failure #4 proves it |
| LLM wraps intent in markdown fences | Medium | Medium | US-P2.1-01 invariant #2 strips fences; tested in test (h) |
| `<` leakage to TTS ("<int" spoken aloud during stall) | Medium | High | US-P2.1-01 invariant #1 (anti-leakage hold); US-P2.1-05 mid-stream-tag test enforces |
| ClaudeCLI tool-use hangs indefinitely | Medium | High | Per-intent timeout (default 120s); worker continues after timeout; US-P2.1-10 forced-failure #5 proves |
| In-flight intent cannot be cancelled | Medium | Medium | **Scoped out of 2.1.** "скасуй" cancels pending only; in-flight cancellation deferred to 2.7. Documented in US-P2.1-03 and ADR. |
| Head-of-line blocking: intent 1 takes 120s, intents 2+3 starve | Low | Medium | Single-worker serial design; mitigated by 120s default cap. If 2.7 data shows frequent stalls, add max-concurrent-per-tool. |
| Parser over-buffers pending `<` → noticeable TTFT regression | Low | Medium | Parser holds max ~8 bytes while disproving `<intent` prefix. Metric: if parser `held_flushed_as_text` log count > 50/day, flag for review in 2.7. |
| Decider imports remain after flag removal → `decider.py` can't be deleted in 2.7 | High if ignored | Low | US-P2.1-07b explicitly audits all non-decider imports; relocates FIXED_PHRASES to `tts_phrases.py` |
| Post-merge production bug, no one-line rollback | Medium | High | US-P2.1-10 forced-failure gate before merge; rollback = `git revert` of the merge commit (heare is a single-user daemon, acceptable cost) |

---

## Verification Plan

See US-P2.1-10. Merge gate is:
1. All 11 stories passes=true (07a + 07b both)
2. `make test` + `make lint` green (≥ 532 tests)
3. Architect APPROVED
4. Live happy-path smoke passes (3 scenarios)
5. Live forced-failure smoke passes (3 scenarios)

---

## ADR

- **Decision:** Build an in-process asyncio IntentQueue + long-running
  single-sequential ActionWorker task. Generator emits
  `<intent>{...}</intent>` tags the parser extracts from the stream;
  worker executes via Claude CLI. Cancellation keyword ("скасуй")
  lives as a generator-side gate and cancels **pending only**.
- **Drivers:**
  1. Conversation must never block on action execution.
  2. We need a clear seam between generation and action so 2.4
     (confirmation) can inject without restructuring.
  3. The `generator_mode` flag's Phase 1 removal commitment must be honored.
- **Alternatives considered:**
  - SQLite-backed queue (rejected: premature; heare restarts blow away
    mic state anyway)
  - Native LLM function-calling API (rejected: Gemini 3.1 Flash Lite
    inconsistent; XML is the simpler ship-now path; revisit in 2.7)
  - Parallel worker (rejected: claude CLI not concurrency-safe; serial
    is sufficient for observed intent frequency)
  - LLM-emitted cancel intent (rejected: cancel is control, not data;
    relying on LLM to recognize "скасуй" is brittle; deterministic
    regex in generator is 1 line)
  - Keep flag through 2.2 as emergency kill switch (rejected: Phase 1
    commitment + dual-path tax; mitigated instead by forced-failure
    smoke gate before merge)
- **Why chosen:** Lowest-risk architectural piece that unblocks 2.2-2.7.
  Complexity stays in `actions.py` + `intent_parser.py`; generator and
  pipeline get thinner.
- **Consequences:**
  - `generator_mode` gone → only `git revert` rollback; US-P2.1-10
    forced-failure gate is the insurance
  - `src/decider.py` becomes unreachable in the generator path;
    FIXED_PHRASES relocated to `tts_phrases.py`; remaining decider
    symbols kept for 2.2-2.5 reintroduction; full decider deletion in 2.7
  - In-flight cancellation of running actions explicitly deferred to 2.7
  - Sentence-buffer/parser coupling documented; any change to parser
    anti-leakage invariants must re-run US-P2.1-05 mid-stream tests
- **Follow-ups:**
  - `phase2.2-conversation-memory.md`
  - `phase2.3-speaker-id.md`
  - `phase2.4-confirmation.md`
  - `phase2.5-modes.md`
  - `phase2.6-heartbeat.md`
  - `phase2.7-cleanup.md` — in-flight cancellation, Ollama option,
    decider.py deletion, parallel worker evaluation, native
    function-calling re-evaluation
