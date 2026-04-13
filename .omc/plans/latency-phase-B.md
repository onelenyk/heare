# heare Latency Phase B — Consensus Plan (APPROVED)

**Date**: 2026-04-13
**Workflow**: ralplan consensus (Planner → Architect → Critic, 4 iterations)
**Status**: APPROVED by Critic v4, ready for execution
**Goal**: Typical voice exchange from ~6.5s to under 2s perceived, while keeping `claude -p` CLI intact.

## Architectural Decision Record

- **Decision**: Ship 4 incremental latency optimizations. Streaming LLM→TTS is conditional on measurement.
- **Drivers**: Groq STT rate limit pressure is immediate; `claude -p` ecosystem (MCP/tools/skills) must be preserved; streaming parser risk must be bounded.
- **Alternatives considered**:
  - Direct Anthropic SDK rewrite — REJECTED (would rebuild MCP/tools/skills from scratch)
  - Big-bang streaming refactor — REJECTED (no stable baseline during transition)
  - Plugin-inside-Claude-Code-session — REJECTED (plugins are reactive, heare is proactive/ambient)
- **Why chosen**: Sequential ordering (B1 first, B3 last conditional) gives the lowest-risk path with incremental verifiable wins. B1 provides immediate Groq relief; B3 is gated behind measurement so we can descope it.
- **Consequences**: 2 days guaranteed work, 5 days worst-case. First Phase B wins land within hours.
- **Follow-ups**: If B-MEASURE triggers B3, add standalone streaming_json.py module + feature-flagged B3.

## Principles

1. Keep `claude -p` CLI unchanged — preserve MCP/tools/skills ecosystem
2. Every latency fix must be measurable via existing `[TIMING]` log instrumentation
3. No new external dependencies (stay within Pipecat + edge-tts + Groq + claude CLI)
4. Tests before marking done — each story includes specific test cases in AC
5. Backward compatible — existing `call_decider` consumers see the same interface

## Stories

### LAT-B1: Stronger ambient pre-filter

**Target files**: `src/decider.py`, `tests/test_decider.py`, `.omc/progress.txt`

**Step 0 (baseline)**: Record 5-min ambient session with current build. Grep `daemon.log` for `TIMING decider` lines. Commit baseline decider-calls-per-minute to `.omc/progress.txt`.

**Changes**:
Extend `is_quick_nothing(transcript, mode)` with explicit rule ordering (first match wins):
- RULE 0: Wake-word ALWAYS bypasses all filters (already exists in decider.py:91-93, verified)
- RULE 1: focus mode without wake-word → True (existing)
- RULE 2: other-person address without wake-word → True (existing)
- RULE 3 (NEW): `len(transcript.split()) < 3` in ambient → True
- RULE 4 (NEW): `_is_mostly_non_ukrainian(transcript)` in ambient → True (Cyrillic < 30%)
- RULE 5 (NEW): `not _looks_like_question(transcript)` in ambient → True (declarative speech)

Add helpers: `_is_mostly_non_ukrainian(text)`, `_looks_like_question(text)` (detects `?` or UA question words `чи|як|що|коли|чому|хто|де|навіщо|скільки`).

**Acceptance Criteria**:
- `test_ambient_short_transcript_filtered`: `is_quick_nothing("Ок.", AMBIENT) is True`
- `test_ambient_non_ukrainian_filtered`: `is_quick_nothing("That's a good capacitor", AMBIENT) is True`
- `test_ambient_declarative_filtered`: `is_quick_nothing("Це працює нормально.", AMBIENT) is True`
- `test_ambient_question_passes`: `is_quick_nothing("Як воно працює?", AMBIENT) is False`
- `test_ambient_wake_word_bypass_all_rules`: `is_quick_nothing("Гава що", AMBIENT) is False`
- `test_is_quick_nothing_wake_word_english_bypasses_non_ukrainian_filter`: `is_quick_nothing("heare status", AMBIENT) is False`
- Helper tests: `test_is_mostly_non_ukrainian_helper`, `test_looks_like_question_helper` (≥5 cases each)
- Post-B1 5-min session: decider-calls-per-minute must drop by ≥30% vs baseline
- All existing decider tests pass
- `ruff check` clean

**Expected impact**: 50-65% of ambient calls eliminated → Groq + Claude cost reduction.

---

### LAT-B2: Tighten prompt reply length + force key order

**Target files**: `prompts/decider.txt`, `tests/fixtures/decider_fixture_post_b2.json`, `tests/fixtures/decider_fixture_post_b2.json.promptsha`, `scripts/capture_decider_fixture.sh`, `scripts/check_fixture_fresh.sh`, `tests/test_context.py`

**Changes**:
1. Edit `prompts/decider.txt`:
   - `"r":"<Ukrainian reply, MAX 8 words>"`
   - `"i":"<short English intent verb phrase, MAX 5 words>"`
   - Add "FIELD ORDER IS STRICT: 't' MUST be the first key; 'r' MUST immediately follow 't' in speak responses"
2. `scripts/capture_decider_fixture.sh`: runs `claude -p --output-format json --model haiku` with rendered prompt, saves JSON output + sha256 of prompt
3. `scripts/check_fixture_fresh.sh`: compares current sha256 of `prompts/decider.txt` vs stored hash; fails if different

**Acceptance Criteria**:
- `prompts/decider.txt` contains "MAX 8 words" and "FIELD ORDER IS STRICT"
- `tests/fixtures/decider_fixture_post_b2.json` checked in
- `tests/fixtures/decider_fixture_post_b2.json.promptsha` checked in
- `scripts/capture_decider_fixture.sh` executable and documented
- `test_captured_fixture_has_t_first_key_order`: parses fixture, uses Python 3.7+ dict-insertion-order guarantee, asserts `list(decision.keys())[0] == "t"` and if `t=="s"` then `keys[1] == "r"`
- `test_fixture_up_to_date`: sha256 match via `scripts/check_fixture_fresh.sh`
- `test_render_real_decider_template` updated assertions
- Full suite green

**Expected impact**: Output tokens 30-50 → 10-15; API time 0.7s → 0.3s per speak call.

**Rollback**: Single-file `git revert` of the prompt commit.

---

### LAT-B4: Speculative context build on UserStartedSpeakingFrame

**Target files**: `src/decider.py`, `src/pipeline.py` (possibly), `tests/test_decider.py`

**Step 0 (prereq verification)**:
1. Add temporary DEBUG log in `DeciderProcessor.process_frame` that records every frame type
2. Run daemon, speak once, grep for `UserStartedSpeakingFrame`
3. Three outcome branches:
   - **(a) Frame reaches decider** → attach handler in `process_frame`
   - **(b) Frame consumed upstream** → insert sibling observer processor in `pipeline.py` Pipeline list, positioned after `stt`, before `decider`
   - **(c) Frame not in Pipecat version** → DESCOPE B4, mark `passes: true` with rationale "architecturally unsupported"
4. Record outcome in `.omc/progress.txt`

**Changes** (under outcomes a/b):
1. Add `_speculative_ctx: dict | None` and `_speculative_prompt: str | None` fields to `DeciderProcessor`
2. On `UserStartedSpeakingFrame`, spawn async task to build context (no transcript yet) and render prompt template
3. In `_handle_listening`, if `_speculative_prompt` available, substitute transcript into `{transcript_or_heartbeat}` placeholder (simple `str.replace`)
4. Clear on `UserStoppedSpeakingFrame` if no decider fire within 5s (stale guard)

**Acceptance Criteria** (outcomes a/b only):
- `test_speculative_context_built_on_user_started_speaking`
- `test_speculative_context_reused_in_handle_listening`
- `test_speculative_context_cleared_after_use`
- `test_speculative_context_cleared_on_stale_vad`: 6s elapses without transcript → ctx cleared
- `test_speculative_context_handles_no_speculation`: direct `_handle_listening` call (no prior UserStarted) → falls back to normal build
- All existing tests pass
- `ruff check` clean

**Expected impact**: ~50-200ms saved per call. Outcome (c) = 0ms saved but B4 closed cleanly.

---

### LAT-B-MEASURE: Measurement gate

**Deliverable**: Live measurement session to decide whether B3 is needed.

**Protocol**:
1. ≥30 phrases across ≥2 separate 10-min sessions (not one 15-min block)
2. Phrase mix: 5 wake-word addressed, 5 non-wake-word questions, 5 declarative background, 5 short non-addressed, 5 English, 5 long Ukrainian monologue
3. Collect `[TIMING]` logs, compute:
   - p50 `decider` duration (gate metric)
   - p50 `tts ttfb_pcm` duration (gate metric)
   - Decider-calls-per-minute (B1 verification)
   - Count filtered vs llm-called

**Gate thresholds**:
- **DESCOPE B3** iff: p50 decider < 2000ms AND p50 tts ttfb < 1000ms
- **PROCEED to B3** iff: p50 decider ≥ 2000ms OR p50 tts ttfb ≥ 1000ms
- **Tie band**: p50 within ±50ms of threshold = DESCOPE (favor shipping)

**Re-run criterion**: If post-ship regression later pushes p50 above threshold, re-gate.

---

### LAT-B3 (CONDITIONAL): Streaming claude output → streaming TTS

**Triggered only if B-MEASURE fails the gate.**

**Target files**: `src/streaming_json.py` (NEW), `src/claude_cli.py`, `src/decider.py`, `src/config.py`, `tests/test_streaming_json.py` (NEW), `tests/test_decider_streaming.py` (NEW), `tests/fixtures/stream_fixture.ndjson`, `scripts/capture_stream_fixture.sh`

**Step 0 (CLI compat check)**:
```bash
claude -p --output-format stream-json --help 2>&1 | grep -qi stream-json || exit 1
```
If fails → DESCOPE B3 with rationale "requires claude CLI upgrade".

**Architecture (three layers, strict boundaries)**:

```
Layer 1 (src/streaming_json.py): NDJSON envelope reader
  Input:  raw stream-json stdout lines
  Output: yields text_delta events from Claude Code's session protocol
  Failure mode: malformed line → log + skip

Layer 2 (src/streaming_json.py): Text-delta accumulator
  Input:  text_delta events
  Output: growing buffer of model's raw response text
  Failure mode: no text within 800ms of stream start → abort + fallback

Layer 3 (src/streaming_json.py): Streaming JSON value parser
  Input:  growing text buffer
  Output: (key, partial_value_str) events as string values are parsed
  Char-by-char state machine, handles UTF-8 multi-byte + JSON escapes
  Failure mode: parse error → abort + fallback
```

**`src/streaming_json.py` is standalone — zero dependencies on rest of codebase**.

**Feature flag**: `Settings.enable_streaming_decider: bool = False` — SHIPS DISABLED.

**Auto-disable safety**:
- `ClaudeCLI._stream_attempt_count` and `_fallback_count` counters
- After each fallback, if `_stream_attempt_count >= 10`:
  - First 10 attempts: auto-disable on >50% rate
  - After 10: auto-disable on 3 consecutive batches >15% rate
- Disable is process-scoped (restart re-enables if config still on)

**Key enforcement**:
```python
# Must see "t":"s" first (B2 guarantees this); otherwise fall through to full decision
if known_type is None and buffer matches r'^\{"t":"([nsa])"':
    if known_type != "s": 
        return await drain_to_full_decision(stream, buffer)  # no streaming
```

**Session ID handling**:
- Background task ALWAYS drains stream to terminal `result` event
- `_absorb_session_id` runs on terminal event regardless of whether TTS fired early
- TTS and session capture are decoupled

**Bot-speaking interaction**:
- Before pushing TTSSpeakFrame mid-stream, check `self._bot_speaking is False`
- If bot speaking OR in `_bot_cooldown_task`, buffer partial reply text
- Flush buffered text as single TTSSpeakFrame on `BotStoppedSpeakingFrame` AND cooldown expiry

**MUST use `TTSSpeakFrame`**, NOT `TextFrame` (known footgun from commit 6f318bd).

**Rate limiter**: streaming attempt AND fallback retry EACH consume one token. Intentional.

**Acceptance Criteria**:
- `src/streaming_json.py` standalone module exists
- `tests/test_streaming_json.py` ≥10 unit tests (happy path, escapes, UTF-8, malformed, partial feeds)
- `tests/fixtures/stream_fixture.ndjson` checked in
- `scripts/capture_stream_fixture.sh` exists
- `test_stream_decider_against_recorded_fixture`: replay fixture, assert both streamed sentences AND final decision match
- `test_streaming_respects_type_first_ordering`: `{"t":"n"}` → no TTS frames
- `test_streaming_type_a_does_not_stream`: `{"t":"a",...}` → no partial TTS, full decision dict only
- `test_streaming_fallback_on_no_text_within_800ms`
- `test_streaming_fallback_on_parse_error`
- `test_streaming_captures_session_id_even_after_early_tts`
- `test_streaming_buffers_during_bot_speaking`
- `test_fallback_rate_triggers_auto_disable`: simulate 20+ failures → `use_streaming` flips to False
- `test_fallback_consumes_second_rate_limit_token`
- `test_ships_with_streaming_disabled_by_default`: `Settings().enable_streaming_decider is False`
- All 199+ existing tests pass
- `ruff check` clean

**Expected impact** (feature flag enabled): first audio ~700-1000ms (stream start + first sentence).
**Worst case** (fallback): 800ms + ~4500ms = ~5300ms (bounded but worse than baseline — 15% rate cap mitigates).

---

## Execution Order

1. **LAT-B1 baseline** (Step 0 measurement) — 5 min
2. **LAT-B1** implementation — 1 day
3. **LAT-B1 verification** (decider-calls-per-min ≥30% drop) — 10 min
4. **LAT-B2** (prompt + fixture + tests) — 2 hours
5. **LAT-B4 frame verification** — 15 min
6. **LAT-B4** implementation (conditional on outcome a/b) — half day
7. **LAT-B-MEASURE** — ~25 min live (2 × 10-min sessions)
8. **LAT-B3** (conditional on gate + CLI compat) — 1-2 days:
   - Standalone streaming_json.py + unit tests (half day)
   - ClaudeCLI.stream_decider with feature flag (half day)
   - DeciderProcessor integration + bot-speaking buffer (half day)
   - Fixture capture + integration test (2 hours)
   - Deploy with flag OFF, document opt-in in release notes

**Total**: ~2 days guaranteed, ~5 days worst-case with B3.

## Verification Strategy

**Per story**: failing tests first → implement → `uv run pytest tests/<target>` green → `uv run pytest tests/` full suite green → `ruff check` clean → mark `passes: true`.

**End-to-end after all 4 stories**: restart daemon → speak test phrases → grep `daemon.log` for `[TIMING]` → assert thresholds met.

**Rollback**: each story is a separate commit; `git revert` in place if live measurement regresses.

## Review Chain

- **Planner**: drafted v1 (4 stories, sequential)
- **Architect**: flagged B3 streaming fragility, recommended measurement gate
- **Planner v2**: added gate, redesigned B3 with layered parser
- **Critic v2**: ITERATE — 2 critical (B4 routing, B1 filter ordering) + 3 major (N=10 theater, fallback tax, B2 behavioral test)
- **Planner v3**: addressed all findings
- **Critic v3**: ITERATE — 1 critical (cold-start monitoring) + 3 major (brittle assertion, CLI prereq, one-way disable)
- **Planner v4**: feature flag default-off, JSON parse key-order, CLI compat gate, restart-re-enable
- **Critic v4**: **APPROVED** — all findings resolved, no new blockers

## Next Step

Execute via ralph or team. Ralph is preferred for sequential per-story verification + architect sign-off loop.
