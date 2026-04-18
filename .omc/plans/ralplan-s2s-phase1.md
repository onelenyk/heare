# RALPLAN — heare S2S-realtime Phase 1 Implementation

Consensus plan for the first-phase rewrite on branch `s2s-realtime`. Builds
on the locked architecture in `.omc/plans/s2s-realtime-arch.md`.

> **Revision history:** v2 after Architect ITERATE — adds US-P1-00 latency
> gate, disambiguates persona sourcing, merges workspace stories, relocates
> `FIXED_PHRASES`, adds heartbeat/shutdown acceptance, clarifies
> GeneratorCLI vs ClaudeCLI, makes `build_for_generator` share core,
> reframes the feature flag as emergency-rollback only.

## RALPLAN-DR Summary

### Principles (what guides this work)

1. **Latency is the product.** Sub-2s time-to-first-audio is the only
   success metric that matters in Phase 1.
2. **Strip before you add.** Delete every piece of infra whose value isn't
   proven in the stripped baseline. Feature flags are emergency-rollback,
   not permanent A/B.
3. **One source of truth per concern.** Memory in `ConversationManager`.
   Policy in `prompts/generator.txt`. Routing in `GeneratorProcessor`.
   Persona rendered in exactly one place (decided in US-P1-03).
4. **Validate the premise before building.** If the core claim ("claude -p
   streaming hits <1s TTFT") is wrong, nothing else in the plan matters.
5. **Local-first backend.** Claude is invoked via `claude -p` CLI with
   minimal flags. No agent SDK, no MCP, no tool permissions.

### Decision Drivers (top 3)

1. Time-to-first-audio ≤2s (today: 7-13s)
2. No test-count regression on unchanged modules
3. One-commit revert remains possible (not via A/B flag, but via branch)

### Viable Options Considered

**A. Stateless generator + prompt caching** ✅ chosen
- Pros: simpler, no session bugs, ConversationManager is one source of
  truth, Anthropic's prefix caching gives ~90% input discount automatically
- Cons: slightly more tokens per call (cost, not latency); relies on
  Anthropic caching behaving as advertised

**B. Session-resumed generator via claude `--resume`**
- Pros: smallest input tokens per call
- Cons: we just fixed stale-session bugs yesterday; more state to break;
  ConversationManager would still need to exist for rolling context
  *Rejected:* added complexity without proportional benefit

**C. Swap to Ollama now (qwen2.5:3b)**
- Pros: <1s latency, fully local, zero per-call cost
- Cons: Ukrainian quality drop; two backends to maintain
- *Conditional reopen at US-P1-00:* if `claude -p` warm TTFT > 1.5s,
  Option C becomes the default and this plan pivots. See US-P1-00 gate.

### Known Tradeoff Tensions

1. **Scope strip vs. revertability.** Principle #2 says strip; driver #3
   says preserve revert. Resolved by: revert lives at the **branch** level
   (`s2s-realtime` → back to `main`), not in a production feature flag.
   The `generator_mode` flag is emergency-rollback only and must be
   removed by end of Phase 2.1.
2. **Local-first backend (Principle #5) vs. TTFA ≤2s (Driver #1).** If
   `claude -p` TTFT > 1s warm, these are incompatible. US-P1-00 is the
   explicit gate — failing it triggers an Option-C pivot, not a silent
   acceptance of slower latency.

---

## Scope: Phase 1 only

Phase 2 (intent queue, action worker, speaker ID, confirmation, modes,
heartbeat, conversation memory) is explicitly out of scope. Each gets its
own PRD when Phase 1 is merged.

---

## User Stories

### US-P1-00 — Latency baseline gate

**As** the team
**We need** to verify `claude -p` streaming TTFT is ≤1.5s warm before
investing in any other Phase 1 work
**So that** if the premise fails we pivot to Ollama immediately instead of
discovering a 2-week wasted sprint

**Acceptance criteria:**
- Run `time claude -p "коротко скажи привіт" --model haiku --output-format stream-json`
  five times cold, five times warm (sequential within 5 min), on the target
  machine. Record wall time AND time-to-first-stream-json event
  (measured via a small `tee` script or `pv -t`).
- Repeat for `--model sonnet`.
- Write results to `.omc/benchmarks/phase1-latency-baseline.md` with
  absolute numbers and a GO/NO-GO verdict.
- GO if: haiku warm TTFT ≤ 1500ms AND cold TTFT ≤ 3500ms
- NO-GO: pause plan, reopen Option C (Ollama) with an ADR amendment in
  `.omc/plans/s2s-realtime-arch.md`
- Story is DONE when the benchmark file exists, the verdict is written,
  and either (a) GO — proceed to US-P1-01 or (b) NO-GO — new plan drafted

**No code written in this story. Pure measurement and decision.**

### US-P1-01 — Generator CLI (reuse or new)

> **Post-pivot note (ADR-002, 2026-04-18):** this story shipped as
> `src/openrouter_cli.py` / `class OpenRouterCLI` (HTTP+SSE), not
> `generator_cli.py` / `class GeneratorCLI` as originally specced.
> All subprocess-specific ACs below (cwd argument, `--append-system-prompt`
> check, `asyncio.create_subprocess_exec` use) are obsolete; the surviving
> contract is the `async generate(prompt, system=None) -> AsyncIterator[str]`
> streaming shape and the 7 tests in `tests/test_openrouter_cli.py`.

**As** the heare daemon
**I need** a minimal `claude -p` wrapper that streams text output
**So that** the generator can emit TTS chunks before the LLM finishes

**Design decision (to fix in first commit of story):** add a new class
`GeneratorCLI` in `src/generator_cli.py` that **does NOT extend `ClaudeCLI`**.
Rationale: `ClaudeCLI.cwd` is set at construction from
`settings.workspace_dir`, persona is auto-appended via
`--append-system-prompt`, and session state is persisted. We want none of
those for the generator. Inheritance would fight the split; composition
(each class owns its subprocess invocation) keeps both simple.

**Acceptance criteria:**
- `src/generator_cli.py` exists with class `GeneratorCLI` (new, not
  subclassing `ClaudeCLI`)
- Constructor signature: `GeneratorCLI(settings: Settings, workspace_dir: Path)`
- `workspace_dir` is passed as `cwd` to `asyncio.create_subprocess_exec`
- `async generate(prompt: str) -> AsyncIterator[str]` method yields
  non-empty text chunks as they arrive from stream-json events
- Invokes `claude -p <prompt> --model <model> --output-format stream-json
  --dangerously-skip-permissions`
- Does NOT pass `--resume`, `--allowed-tools`, `--append-system-prompt`
- Persona rendering: **NOT** done here. Prompt comes in already rendered
  from `prompts/generator.txt` (single persona source — see US-P1-03)
- Handles subprocess errors: non-zero exit → raises `GeneratorError`
- Unit tests in `tests/test_generator_cli.py` that mock subprocess
  stdout stream and verify: (a) streaming yields incremental chunks,
  (b) non-zero exit raises `GeneratorError`, (c) `cwd` is passed
  correctly, (d) `--append-system-prompt` is NOT in the argv
  (≥4 tests)

### US-P1-02 — Workspace seeding and config (was US-P1-02 + US-P1-06 split)

**As** the heare daemon on startup
**I need** a separate workspace for the generator with seeded empty MCP
config, plus `main.py` wired to use it
**So that** the generator never inherits MCP servers or tool permissions

**Acceptance criteria:**
- `settings.generator_workspace_dir` exists in `src/config.py`, defaults
  to `HEARE_HOME / "workspace-generator"`
- `settings.ensure_dirs()` creates this directory if missing
- On startup, `main.py::_ensure_workspace_mcp(generator_workspace_dir)`
  is called (the same helper already used for `workspace_dir`). The
  generator workspace's `.mcp.json` is seeded with `{"mcpServers": {}}`
  (empty) — NOT inherited from `~/.claude.json`. Adjust
  `_ensure_workspace_mcp` to accept a `seed_empty: bool = False`
  parameter or write a thin new helper `_ensure_empty_mcp_json`.
- Existing `workspace_dir` untouched — it still seeds from `~/.claude.json`
- `tests/test_config.py` adds test for the new field default + creation
- `tests/test_main.py` updated to verify both workspaces get their
  `.mcp.json` seeded correctly

### US-P1-03 — Generator prompt template (single persona source)

**As** the generator role
**I need** a prompt template that always produces a reply and is the sole
place persona is rendered
**So that** the bot is conversational and there is exactly one persona
source (satisfying Principle #3)

**Persona source of truth:** the template. `GeneratorCLI` does NOT pass
`--append-system-prompt` (see US-P1-01). `ContextBuilder` does NOT hold a
`persona` field (US-P1-07 revised).

**Acceptance criteria:**
- `prompts/generator.txt` exists
- Placeholders: `{time}`, `{timezone}`, `{persona}`, `{recent_transcripts}`,
  `{transcript}` — exactly these, no others
- Template instructs: "respond in Ukrainian, 1-2 natural sentences, never
  refuse to reply, never output JSON, never mention rules or meta-text"
- No JSON output schema
- No `{conversation_summary}`, `{active_topics}`, `{entities}`, `{recent_turns}`
- Golden test in `tests/test_generator_prompt.py` verifies rendering
  produces expected shape (no placeholders left, Ukrainian instruction
  present, persona block included)

### US-P1-04 — GeneratorProcessor (Pipecat FrameProcessor)

**As** the pipeline
**I need** a replacement for `DeciderProcessor` that always speaks
**So that** the architecture matches the "continuous chat" model

**Acceptance criteria:**
- `src/generator.py` exists with class `GeneratorProcessor`
- Inherits from pipecat's `FrameProcessor` (real inheritance — verified
  by `.link` being present after construction)
- On `TranscriptionFrame`:
  1. Calls `ContextBuilder.build_for_generator(transcript)` for context
  2. Renders `prompts/generator.txt` with that context
  3. Calls `generator_cli.generate(prompt)` as an async iterator
  4. Pushes `TTSSpeakFrame(text=chunk)` downstream per chunk
- On non-transcription frames: `await super().process_frame(frame, direction)`
  (pass-through)
- Empty replies: log warning, no crash
- Exceptions in `generate()`: logged; falls back to cached phrase
  `"Хвилинку, щось не так."` via `TTSSpeakFrame` — phrase MUST be in
  `FIXED_PHRASES` (see US-P1-11)
- Exposes no-op methods: `async def shutdown(self) -> None` (for
  `main.run_until_stopped` teardown) and `async def on_heartbeat_tick(self) -> None`
  (for `HeartbeatTask` compatibility in legacy-flag-off mode only — see
  US-P1-08)
- `tests/test_generator.py`: normal streaming, empty reply, exception
  fallback, non-transcription pass-through, `shutdown()` idempotent,
  `on_heartbeat_tick` is no-op (≥6 tests)
- Logs `[TIMING] generator transcript="..." ttft=XXXms total_chunks=N`
  per turn (see US-P1-09)

### US-P1-05 — Pipeline wiring

**As** the pipeline builder
**I need** a slim pipeline when `generator_mode=True`
**So that** only the components we're keeping are live

**Acceptance criteria:**
- `src/pipeline.py::build_pipeline` — when `settings.generator_mode=True`,
  stages list is exactly:
  `[transport.input(), stt, generator, tts, transport.output()]`
- No `speaker_tagger`, `audio_buffer`, `turn_aggregator`, `decider` in
  that branch
- Legacy branch (flag=False) preserves current behavior (calls extracted
  helper `_build_pipeline_legacy(...)` if helpful; acceptable to keep
  inline with a single `if settings.generator_mode` branch)
- Signature of `build_pipeline` unchanged (still takes
  `conversation_manager` — passed as None by `main.py` in flag-True mode;
  passed as real instance in flag-False mode)
- Returns `(task, generator_or_decider, tts_cache)` — the middle
  element's concrete type depends on flag, but the call site
  (`main.run_until_stopped`) only needs `.shutdown()`, which both provide
- Integration test `tests/integration/test_s2s_pipeline.py` verifies
  stage list for flag-True; existing decider-pipeline tests verify
  flag-False path

### US-P1-06 — Main entry point update

**As** `main._cmd_start`
**I need** to wire the new pipeline, drop Phase-2 scaffolding when flag
is on, and keep the old behavior when flag is off
**So that** startup matches the Phase-1 architecture with clean fallback

**Acceptance criteria:**
- When `generator_mode=True`:
  - `ConversationManager` is NOT instantiated
  - `ContextBuilder` constructed with `(store, settings)` — no
    `conversation_manager` arg
  - `_ensure_workspace_mcp(workspace_dir)` AND
    `_ensure_empty_mcp_json(generator_workspace_dir)` both called
  - Onboarding passphrase prompt block (`main.py:174-187`) is SKIPPED
    (passphrase is a Phase-2 concern)
  - `HeartbeatTask` and `WarmupTask` still started — they target
    `generator`, which has no-op `on_heartbeat_tick` + proper
    `shutdown` (per US-P1-04)
  - Startup greeting `f"{settings.wake_word} на зв'язку"` is pushed to
    `generator` via `TTSSpeakFrame` (generator passes non-transcription
    frames through unchanged per US-P1-04 AC)
- When `generator_mode=False`: existing behavior preserved exactly
- `FIXED_PHRASES` warmup uses the relocated list from
  `src/tts_phrases.py` (US-P1-11), not `src/decider.py`
- Daemon starts, greets, and enters main loop in <5s (measured by
  `daemon.log` timestamps)
- `tests/test_main.py` updated: mock both flag paths, verify wiring

### US-P1-07 — ContextBuilder.build_for_generator (shared core)

**As** the generator
**I need** a minimal context builder method that cannot drift from `build()`
**So that** Phase-2 additions to `build()` are discovered, not silently
skipped

**Design:** `build_for_generator` MUST internally call `build()` or a
private helper `_build_base_context()`. It filters/selects keys for the
generator path but does not duplicate field construction.

**Acceptance criteria:**
- `ContextBuilder.build_for_generator(transcript: str) -> dict[str, Any]`
  exists in `src/context.py`
- Implementation either: (a) calls `self.build(transcript=transcript)` and
  returns a projection `{k: v for k, v in result.items() if k in {...}}`,
  OR (b) both methods delegate to a private `_build_base_context()` helper
- Returns only keys: `time`, `timezone`, `persona`, `recent_transcripts`,
  `transcript`
- **`persona` is pulled from the existing `claude_cli.persona` pattern
  via an injected callable or passed via `build_for_generator(... , persona=...)`**
  — NO new `ContextBuilder.persona` attribute. Persona still flows from
  `render_persona(persona_template, identity)` in `main.py`.
- `ContextBuilder.__init__` signature UNCHANGED — still `(store, settings,
  conversation_manager=None)`. No new constructor args.
- `tests/test_context.py` adds `test_context_builder_keys_accounted_for`:
  fetches `set(build().keys())`, subtracts `set(build_for_generator().keys())`,
  asserts residue equals a declared module-level constant
  `_EXCLUDED_FROM_GENERATOR_CTX` in `src/context.py`. This structurally
  prevents silent drift — any new key added to `build()` must explicitly
  join the excluded set or the generator view.

### US-P1-08 — Emergency rollback flag

**As** the repo maintainer
**I need** an emergency rollback to the decider pipeline
**So that** if Phase 1 goes sideways we can disable it without reverting
the branch

**This flag is NOT an A/B comparison mechanism.** It is short-lived
emergency rollback. It MUST be removed by the end of Phase 2.1 — failing
removal by that milestone is a process bug to be escalated.

**Acceptance criteria:**
- `settings.generator_mode: bool = True` in `config.py`
- When `False`: full decider/aggregator/speaker_id/conversation-manager
  pipeline runs (existing behavior)
- When `True`: Phase-1 pipeline runs (see US-P1-05/06)
- `README.md` gains a section "Experimental generator mode" documenting
  the flag, its temporary nature, and the planned removal milestone
- No dual-implementation of Phase-2 features during this window — if a
  Phase-2 PR touches only the generator path, that's fine; if it requires
  dual-path work, the flag must be removed first
- `tests/test_feature_flags.py` gains a test that both flag values
  produce a buildable pipeline without exceptions

### US-P1-09 — Latency telemetry

**As** an operator
**I need** measurable time-to-first-audio on each turn
**So that** we can prove the 2s target is met

**Acceptance criteria:**
- `GeneratorProcessor` logs `[TIMING] generator transcript="..." ttft=XXXms total_chunks=N`
  per turn (`ttft` = ms from `process_frame` entry to first yielded chunk)
- `tests/test_generator.py::test_ttft_logged` mocks generator to yield
  with a 300ms delay, asserts log line format
- Manual smoke test (in US-P1-12 verification): live daemon produces
  ttft <2s on cold start, <1.5s on warm, logged in `daemon.log`

### US-P1-10 — Regression baseline

**As** the codebase
**I need** no test regressions on unchanged modules
**So that** we know Phase 1 is side-effect free elsewhere

**Acceptance criteria:**
- All tests in these files pass unmodified: `test_storage.py`,
  `test_identity.py`, `test_rate_limit.py`, `test_tts_edge.py`,
  `test_tts_cache.py`
- `test_config.py`: existing tests pass; new tests added per US-P1-02
- Total test count ≥ 479 (existing) + net-new Phase-1 tests
- `make test` exits 0
- `make lint` exits 0

### US-P1-11 — Fallback phrase in cache (minimal)

**As** the generator's exception path
**I need** the fallback phrase to be TTS-cached for instant playback
**So that** error recovery doesn't add a live-TTS latency spike

**Descoped from v2:** full relocation of `FIXED_PHRASES` to
`src/tts_phrases.py` deferred to Phase 2.7 cleanup. Phase 1 only needs
the fallback phrase present in the existing list.

**Acceptance criteria:**
- `FIXED_PHRASES` in `src/decider.py` gains `"Хвилинку, щось не так."`
  as a new entry
- `main.py` continues to call `tts_cache.warmup(FIXED_PHRASES, ...)` —
  no import change required
- `src/generator.py` imports `FIXED_PHRASES` from `src.decider` (one
  line; relocation is a Phase 2 concern)
- No new file created
- Existing FIXED_PHRASES tests pass unchanged

### US-P1-12 — Live verification

**As** the plan author
**I need** a single, executable verification of Phase-1 completion
**So that** "done" is evidence-based

**Acceptance criteria:**
- All US-P1-01..P1-11 acceptance criteria pass
- `make test` green
- `make lint` green
- Live smoke: run daemon with `generator_mode=True`, greet it, speak 5
  test utterances. Measure `ttft` from `daemon.log` per turn. Target:
  median ttft ≤ 2000ms.
- Results written to `.omc/benchmarks/phase1-live-smoke.md`
- Architect reviewer signs off on the implementation PR against this PRD

---

## Out of Scope (Phase 2 triggers)

Each becomes its own PRD after Phase 1 merges:

- `phase2-intent-queue.md` — IntentQueue + ActionWorker
- `phase2-conversation-memory.md` — ConversationManager re-wire (and
  **removal of `generator_mode` flag** is folded into this or phase2-
  intent-queue, whichever lands first)
- `phase2-speaker-id.md` — speaker tagging as a soft context hint
- `phase2-confirmation.md` — passphrase gate inside action worker
- `phase2-modes.md` — engagement_floor flag replacing mode enum
- `phase2-heartbeat.md` — proactive nudges via generator
- `phase2-ollama.md` — Ollama as an alternate generator backend

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `claude -p` warm TTFT > 1.5s | Medium | Catastrophic | **US-P1-00 is a hard gate.** NO-GO reopens Option C. |
| TTS service doesn't handle streaming `TTSSpeakFrame` chunks | Medium | High | US-P1-04 test harness asserts chunk push. If buffered, fallback: accumulate to sentence boundary before pushing. |
| `generator_mode` flag outlives Phase 2.1 | Medium | Medium | US-P1-08 documents the removal milestone; `phase2-intent-queue.md` / `phase2-conversation-memory.md` MUST include flag removal. |
| Double-persona drift during Phase 2 re-introduction | Low | Medium | US-P1-03 + US-P1-07 enforce single persona source (prompt template). Any Phase-2 PR adding `--append-system-prompt` must justify. |
| `on_heartbeat_tick` / `shutdown` missing on generator → runtime crash | Low | High | US-P1-04 acceptance criterion requires both no-ops. US-P1-10 test coverage. |

---

## Verification Plan

See US-P1-12. Summary gate: Architect sign-off against this PRD before
merge.

---

## ADR

- **Decision:** Implement S2S-realtime as a two-phase rewrite on branch
  `s2s-realtime`. Phase 1 = stripped generator pipeline with `claude -p`
  one-shot backend. Phase 2 = incremental re-addition of speaker/
  confirmation/memory/modes/heartbeat.
- **Drivers:** Current 7-13s TTFA is the #1 UX blocker. Agent SDK adds
  3-5s overhead. Constraint: local LLM invocation only.
- **Alternatives considered:**
  - True S2S model (OpenAI Realtime / Gemini Live / Moshi): rejected —
    constraint + Ukrainian quality concerns
  - Incremental mutation of current decider: rejected — coupled state
    machine complexity; clean rewrite is simpler
  - Ollama immediately: deferred behind US-P1-00 gate; reopened if
    `claude -p` fails the latency premise
- **Why chosen:** Lowest-risk, highest-impact path that respects
  constraints. Feature flag gives short-lived emergency rollback. Small
  blast radius (generator/pipeline/main touched; storage/identity/tts
  untouched).
- **Consequences:**
  - Temporary loss of modes, speaker ID, confirmations, conversation
    memory, heartbeat substance (shells remain, no-op)
  - Flag-gated codebase for 1-2 weeks during Phase 2.1 rollout
  - Two `ContextBuilder` methods briefly; drift structurally prevented
    via shared core (US-P1-07)
- **Follow-ups:** see "Out of Scope" section above.
