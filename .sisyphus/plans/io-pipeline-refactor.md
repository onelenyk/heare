# Heare I/O Pipeline — Pluggable Multi-Output Refactor

## TL;DR

> **Quick Summary**: Refactor the rigid single-voice pipeline into pluggable multi-I/O. Multiple inputs (voice, text) feed the LLM. Multiple outputs (voice, text, canvas) consume from it. Mode profiles control availability. Mute only gates voice. Adding new I/O = one processor.
>
> **Deliverables**:
> - `outputs` field on `ModeProfile` — per-mode output availability
> - `OutputRouter` processor — inspects LLM response, dispatches to correct output
> - `VoiceOutputProcessor` — extracted from existing TTS chain
> - `TextOutputProcessor` — extracted from transcript logging
> - `CanvasOutputProcessor` — new, extends `show_display` pattern
> - Tagged-text LLM response format: `[voice]...[/voice][canvas]...[/canvas]`
> - `mute_output` renamed to `voice_muted` globally
>
> **Estimated Effort**: Medium (focused refactor, ~10 new/modified files)
> **Parallel Execution**: YES — 4 waves, up to 5 tasks parallel
> **Critical Path**: Wave 1 (prototype) → Wave 2 (router+outputs) → Wave 3 (integration) → Wave 4 (tests)

---

## Context

### Original Request
"So I want start from Input / Output system. Now it strictly STT - TTS. But I want actually rework this. So we will have an Input Source, and Output source. As input it will be TEXT and Voice by default. As Output - the Voice and Text and Canvas(or Code) — voice and text are clear, the canvas will be able to access a canvas field, where it can generate and run html/js code."

### Key Decisions
- **Option B**: Pipecat-native — each I/O is a frame processor in the existing pipeline architecture
- **Tagged text** for LLM output typing: `[voice]Hello[/voice][canvas]<html>...</html>[/canvas]` — NOT JSON streaming (preserves sub-100ms TTS latency)
- **Pipeline stays linear** — OutputRouter is a single inline processor, no branching topology
- **Canvas sink: file/DB first** — extends existing `show_display` → `displays` table pattern. WebSocket to desktop later.
- **mute_bot only gates voice** — text and canvas always flow. `mute_output` renamed to `voice_muted`

### Metis Review
**Critical Gaps Addressed**:
- JSON streaming would break TTS latency → switched to tagged text format
- Pipecat has no branching → OutputRouter is single linear processor with inline dispatch
- Desktop app source deleted → canvas uses file/DB sink, WS later
- tts_scrub must not touch canvas HTML → router sits before scrub, routes canvas around it
- `mute_output` ambiguous → renamed to `voice_muted`
- LLM typing must be validated before router → Wave 1 is a prototype/validation phase

---

## Work Objectives

### Core Objective
Add multi-output routing to the pipeline so the LLM can choose between voice (TTS), text (log), and canvas (HTML render) per response, constrained by mode profile availability and mute flags.

### Concrete Deliverables
- Modified: `src/agent/modes.py` — `outputs` field, `voice_muted` rename
- Modified: `src/pipeline/build.py` — wire output processors
- New: `src/pipeline/stages/output_router.py` — tagged text parser + dispatch
- New: `src/pipeline/stages/voice_output.py` — extracted TTS chain
- New: `src/pipeline/stages/text_output.py` — extracted transcript logging
- New: `src/pipeline/stages/canvas_output.py` — file/DB sink
- Modified: `src/pipeline/stages/mute_gate.py` — voice_muted rename
- Modified: `src/store/storage.py` — canvas entries in displays table
- Modified: `prompts/persona.txt` — output routing instructions
- Modified: `src/agent/llm/context_injector.py` — inject available outputs per mode

### Definition of Done
- [ ] LLM can emit `[voice]hello[/voice][canvas]<h1>42</h1>[/canvas]` and both outputs render
- [ ] Silent mode: LLM only gets `[text]` and `[canvas]` in prompt, voice blocked at router
- [ ] mute_bot=1: voice dropped, text/canvas flow normally
- [ ] `uv run pytest tests/ -q` — all tests pass
- [ ] Canvas content lands in `displays` table (file/DB sink working)

### Must Have
- Voice, text, and canvas outputs all functional
- Mode profiles control availability
- mute_bot only gates voice
- Backward compatible: existing single-output behavior preserved

### Must NOT Have (Guardrails)
- No JSON streaming (breaks TTS latency)
- No branching pipeline topology (Pipecat is linear)
- No desktop app changes (source deleted — separate project)
- No change to input sources beyond what already exists (voice STT + text injection stay)
- Do NOT refactor tool calling, LLM provider switching, or non-I/O pipeline stages
- Canvas content MUST be sanitized (no external resource loads — XSS vector)

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest)
- **Automated tests**: Tests-after
- **Framework**: pytest

### QA Policy
Agent-executed QA scenarios using curl + sqlite3 + python -c. Canvas verified via SQLite `displays` table.

---

## Execution Strategy

```
Wave 1 (Start Immediately — validation + mode data):
├── T1: LLM typing prototype — validate tagged text works with DeepSeek [deep]
├── T2: ModeProfile.outputs field + voice_muted rename [quick]
└── T3: Update prompt template + context injector [quick]

Wave 2 (After Wave 1 — output processors, MAX PARALLEL):
├── T4: OutputRouter processor — tagged text parser + dispatch [deep]
├── T5: VoiceOutputProcessor — extract TTS chain [deep]
├── T6: TextOutputProcessor — extract transcript logging [quick]
├── T7: CanvasOutputProcessor — file/DB sink via displays table [quick]
└── T8: Wire processors into build.py pipeline [unspecified-high]

Wave 3 (After Wave 2 — mute + mode integration):
├── T9: Update mute_gate — voice_muted rename, text/canvas bypass [quick]
├── T10: Mode-aware prompt — available outputs per profile [quick]
├── T11: Mode switch handling — in-flight output behavior [deep]
└── T12: Storage schema — canvas entries in displays + usage tracking [quick]

Wave 4 (After Wave 3 — tests + verification):
├── T13: Test output_router.py [deep]
├── T14: Test canvas_output.py + extend test_mute_gate.py [unspecified-high]
├── T15: Full test suite + regression check [deep]
└── T16: Dead code cleanup — remove old single-output assumptions [quick]

Wave FINAL (After Wave 4):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real QA — multi-output scenarios (unspecified-high)
└── F4: Scope fidelity check (deep)
```

---

## TODOs

### Wave 1: Validation + Mode Data (3 tasks)

- [x] 1. LLM typing prototype — validate tagged text with DeepSeek

  **What to do**:
  - Write a standalone test script that sends a prompt to DeepSeek with tagged output instructions
  - Prompt: *"You can respond using these tags: [voice]text[/voice] for speech, [text]text[/text] for written, [canvas]html[/canvas] for visual. Choose the best output per response. Example: [voice]Hello![/voice]"*
  - Test with 20 varied queries. Measure: compliance rate (did it use tags?), accuracy (right tag for right content?), latency (any overhead vs untagged?)
  - Also test: what happens when only `[text]` is available (simulate silent mode prompt constraint)?
  - Save results to `.sisyphus/evidence/task-1-tagged-text-results.txt`
  - **Gate**: Only proceed to T4 if compliance >80%. If not, explore tool-call approach instead.

  **Must NOT do**:
  - Do NOT commit to the tagged-text approach until validation passes
  - Do NOT modify any source files — this is a throwaway prototype script

  **Recommended Agent Profile**: `deep` | **Skills**: []
  **Parallelization**: Wave 1, can run with T2+T3. Blocks Wave 2.

  **QA Scenarios**:
  ```
  Scenario: DeepSeek uses [voice] tag for conversational responses
    Tool: Bash (python script)
    Steps: Send "How are you?" → check response uses [voice] tag
    Expected: Content wrapped in [voice]...[/voice]
    Evidence: .sisyphus/evidence/task-1-voice-tag.txt

  Scenario: DeepSeek uses [canvas] tag for code/visual requests
    Tool: Bash (python script)
    Steps: Send "Show me a bar chart of sales" → check for [canvas] tag
    Expected: HTML wrapped in [canvas]...[/canvas]
    Evidence: .sisyphus/evidence/task-1-canvas-tag.txt
  ```

  **Commit**: NO (prototype only)

- [x] 2. Add `outputs` field to ModeProfile + rename `mute_output` → `voice_muted`

  **What to do**:
  - In `src/agent/modes.py`: Add `outputs: frozenset[str]` field to `ModeProfile`
  - Default for ambient/focus/assistant: `frozenset({"voice", "text", "canvas"})`
  - Default for silent: `frozenset({"text", "canvas"})`
  - Default for meeting: `frozenset({"text"})`
  - Add `voice_muted: bool = False` field, keep `mute_output` as deprecated alias (set both in `__post_init__`)
  - Update all MODE_PROFILES entries: replace `mute_output=True` with `voice_muted=True`
  - Search ALL consumers of `mute_output` across codebase: `grep -rn mute_output src/`
  - Update consumers: mute_gate.py (use `voice_muted`), assistant_response_logger.py (use `voice_muted`)
  - Update `VALID_MODES` — no change needed
  - Update any tests that reference `mute_output`

  **Must NOT do**:
  - Do NOT change mode behavior — same 5 modes, same defaults
  - Do NOT remove `mute_output` entirely — keep as deprecated property for backward compat

  **Recommended Agent Profile**: `quick` | **Skills**: []
  **Parallelization**: Wave 1, can run with T1+T3.

  **QA Scenarios**:
  ```
  Scenario: ModeProfile.outputs enum is correct per mode
    Tool: Bash (python -c)
    Steps:
      1. from src.agent.modes import MODE_PROFILES
      2. assert MODE_PROFILES['silent'].outputs == {'text', 'canvas'}
      3. assert MODE_PROFILES['ambient'].outputs == {'voice', 'text', 'canvas'}
    Expected: Correct frozensets
    Evidence: .sisyphus/evidence/task-2-outputs.txt
  ```

  **Commit**: YES
  - Message: `feat(modes): add outputs field, rename mute_output to voice_muted`
  - Files: `src/agent/modes.py`, `src/pipeline/stages/mute_gate.py`, `src/pipeline/stages/assistant_response_logger.py`

- [x] 3. Update system prompt + context injector for available outputs

  **What to do**:
  - In `src/agent/llm/context_injector.py`: inject available outputs into system prompt based on `session_state.profile.outputs`
  - Add to prompt: *"Available output channels: {channels}. Use [voice]...[/voice] for speech, [text]...[/text] for written, [canvas]...[/canvas] for HTML/visuals. Choose the best channel per response."*
  - If `"voice"` not in outputs: add *"Voice output is UNAVAILABLE. Do not use [voice] tags."*
  - If `"canvas"` not in outputs: omit canvas from available list
  - Keep prompt_addendum functionality unchanged — this is additive
  - Also update `prompts/persona.txt` if it has output-related text (read it first)

  **Must NOT do**:
  - Do NOT make the prompt longer than +3 sentences
  - Do NOT break the existing language-aware system prompt rebuild

  **Recommended Agent Profile**: `quick` | **Skills**: []
  **Parallelization**: Wave 1, can run with T1+T2.

  **QA Scenarios**:
  ```
  Scenario: Silent mode prompt excludes voice
    Tool: Bash (python -c)
    Steps:
      1. Build context with session_state.mode="silent"
      2. Assert "Voice output is UNAVAILABLE" in system prompt
      3. Assert "[voice]" NOT in available list
    Expected: Voice excluded from prompt
    Evidence: .sisyphus/evidence/task-3-prompt.txt
  ```

  **Commit**: YES
  - Message: `feat(prompt): inject available outputs per mode into system prompt`
  - Files: `src/agent/llm/context_injector.py`, `prompts/persona.txt`

### Wave 2: Output Processors (5 tasks, ALL parallel after T1 gate)

- [x] 4. Create `OutputRouter` processor — tagged text parser + dispatch

  **What to do**:
  - New file: `src/pipeline/stages/output_router.py`
  - `OutputRouter` is a Pipecat `FrameProcessor` that sits between `assistant_response_logger` and downstream outputs
  - Parses LLM text for tags: `[voice]...[/voice]`, `[text]...[/text]`, `[canvas]...[/canvas]`
  - Streaming-aware: partial `[voice]Hel` starts TTS immediately (no wait for closing tag)
  - Emits typed frames: `VoiceContentFrame`, `TextContentFrame`, `CanvasContentFrame`
  - Untagged text → defaults to `TextContentFrame`
  - Unknown/corrupt tags → log warning, emit as text
  - Pipeline stays LINEAR — router pushes frames downstream sequentially, no branching

  **Must NOT do**:
  - Do NOT create branching pipeline topology
  - Do NOT import from TTS or canvas modules

  **Recommended Agent Profile**: `deep` | **Skills**: []
  **Parallelization**: Wave 2, depends on T1 passing gate (>80% compliance).

  **QA Scenarios**:
  ```
  Scenario: [voice] tag routes correctly
    Tool: Bash (python -c)
    Steps: Push LLMTextFrame("[voice]hello[/voice]") through router
    Expected: VoiceContentFrame emitted with text "hello"
    Evidence: .sisyphus/evidence/task-4-voice.txt
  ```

  **Commit**: YES — `feat(pipeline): add OutputRouter with tagged text parsing`

- [x] 5. Create `VoiceOutputProcessor` — extracted TTS chain

  **What to do**:
  - New file: `src/pipeline/stages/voice_output.py`
  - Extracted from current pipeline: tts_scrub → EdgeTTSService → tts_fade → mute_gate
  - Consumes `VoiceContentFrame`, passes through TTS chain, respects `voice_muted` flag
  - If `"voice"` not in profile.outputs: drop frame + log
  - This is a REFACTOR — move existing code, don't rewrite

  **Must NOT do**: Do NOT change TTS behavior or configuration

  **Recommended Agent Profile**: `deep` | **Skills**: []
  **Parallelization**: Wave 2, can run with T4,T6-T8.

  **QA Scenarios**:
  ```
  Scenario: VoiceContentFrame produces TTSAudioRawFrame
    Tool: Bash (python -c)
    Steps: Push VoiceContentFrame("test") → Assert audio frames downstream
    Evidence: .sisyphus/evidence/task-5-voice.txt
  ```

  **Commit**: YES — `refactor(pipeline): extract VoiceOutputProcessor from TTS chain`

- [x] 6. Create `TextOutputProcessor` — extracted transcript logging

  **What to do**:
  - New file: `src/pipeline/stages/text_output.py`
  - Consumes `TextContentFrame`, writes to `transcripts` table via `TranscriptStore`
  - `agent_spoken=false`, always logs (never gated by mute or mode)
  - Does NOT pass through tts_scrub or mute_gate

  **Must NOT do**: Do NOT duplicate voice logging from assistant_response_logger

  **Recommended Agent Profile**: `quick` | **Skills**: []
  **Parallelization**: Wave 2, can run with T4,T5,T7,T8.

  **QA Scenarios**:
  ```
  Scenario: Text output lands in transcripts table
    Tool: Bash (sqlite3)
    Steps: Push frame → query transcripts → assert row exists
    Evidence: .sisyphus/evidence/task-6-text.txt
  ```

  **Commit**: YES — `refactor(pipeline): extract TextOutputProcessor from transcript logging`

- [x] 7. Create `CanvasOutputProcessor` — file/DB sink

  **What to do**:
  - New file: `src/pipeline/stages/canvas_output.py`
  - Consumes `CanvasContentFrame`, writes to `displays` table (`content_type="canvas/html"`)
  - Sanitize: strip external `<script src>`, `<link href>`, `<img src=http...>` — XSS prevention
  - Truncate >64KB with warning
  - If `"canvas"` not in profile.outputs: drop + log
  - Pluggable `sink` parameter for future WS support

  **Must NOT do**: Do NOT implement WebSocket sink (desktop deleted — separate project)

  **Recommended Agent Profile**: `quick` | **Skills**: []
  **Parallelization**: Wave 2, can run with T4-T6,T8.

  **QA Scenarios**:
  ```
  Scenario: Canvas HTML lands in displays table
    Tool: Bash (sqlite3)
    Steps: Push CanvasContentFrame → query displays → assert row exists
    Evidence: .sisyphus/evidence/task-7-canvas.txt
  ```

  **Commit**: YES — `feat(pipeline): add CanvasOutputProcessor with file/DB sink`

- [x] 8. Wire output processors into `build.py` pipeline

  **What to do**:
  - Insert `OutputRouter` AFTER `assistant_response_logger`
  - Add `VoiceOutputProcessor`, `TextOutputProcessor`, `CanvasOutputProcessor` as sequential stages
  - Each processor only consumes its own frame type, passes others through
  - Pipeline stays LINEAR: ... → router → voice_output → text_output → canvas_output → ...
  - Update `_assemble_native_stages()` params and `build_pipeline()` assembly

  **Must NOT do**: Do NOT create branching topology

  **Recommended Agent Profile**: `unspecified-high` | **Skills**: []
  **Parallelization**: Wave 2, depends on T4-T7.

  **QA Scenarios**:
  ```
  Scenario: Pipeline assembles with new output processors
    Tool: Bash (python -c)
    Steps: from src.pipeline.build import _assemble_native_stages → no error
    Evidence: .sisyphus/evidence/task-8-pipeline.txt
  ```

  **Commit**: YES — `refactor(build): wire OutputRouter and output processors into pipeline`

### Wave 3: Mute + Mode Integration (4 tasks, ALL parallel)

- [x] 9. Update mute_gate — voice_muted rename, text/canvas bypass

  **What to do**:
  - Rename `mute_output` references to `voice_muted` in `mute_gate.py`
  - Verify: mute_gate only drops `TTSAudioRawFrame` (already true, just confirm)
  - Add explicit comment: "This gate only mutes voice. Text and canvas frames bypass this processor."
  - Update `create_mute_gate()` signature if needed

  **Must NOT do**: Do NOT add text/canvas mute logic — they're never muted

  **Recommended Agent Profile**: `quick` | **Skills**: []
  **Parallelization**: Wave 3.

  **Commit**: YES — `refactor(mute): rename mute_output to voice_muted, clarify scope`

- [x] 10. Mode-aware prompt — available outputs per profile

  **What to do**:
  - In `context_injector.py`: read `session_state.profile.outputs`, build output availability string
  - Inject: `"Available outputs: voice, text, canvas"` (ambient) or `"Available outputs: text, canvas. Voice is UNAVAILABLE."` (silent)
  - Regenerate prompt on mode change (listener already exists for language changes, extend pattern)
  - Verify prompt is ≤ +3 sentences

  **Must NOT do**: Do NOT change existing prompt structure

  **Recommended Agent Profile**: `quick` | **Skills**: []
  **Parallelization**: Wave 3.

  **Commit**: YES — `feat(prompt): dynamic output availability per mode`

- [x] 11. Mode switch handling — in-flight output behavior

  **What to do**:
  - When mode switches mid-response: current voice output finishes (don't interrupt mid-word)
  - Next turn: new mode takes effect (already works via system_prompt_injector rebuild)
  - If switching to silent: any queued canvas/text frames continue, voice frames dropped
  - Use existing `session_state.set_mode_change_listener()` pattern
  - No new mechanism needed — mode changes are per-turn already

  **Must NOT do**: Do NOT interrupt in-flight TTS on mode switch (jarring UX)

  **Recommended Agent Profile**: `deep` | **Skills**: []
  **Parallelization**: Wave 3.

  **QA Scenarios**:
  ```
  Scenario: Mode switch to silent drops voice, keeps text
    Tool: Bash (python -c)
    Steps: Set mode=silent mid-turn → assert voice frames dropped, text logged
    Evidence: .sisyphus/evidence/task-11-mode-switch.txt
  ```

  **Commit**: YES — `feat(modes): graceful output handling on mode switch`

- [x] 12. Storage schema — canvas entries + usage tracking

  **What to do**:
  - Add `content_type` and `title` columns to `displays` table if not present
  - Ensure `CanvasOutputProcessor` writes with `content_type="canvas/html"`
  - Add canvas char count to `usage_recorder` — track canvas output tokens
  - No new tables needed — reuse existing `displays` table from `show_display` tool

  **Must NOT do**: Do NOT create a new database table

  **Recommended Agent Profile**: `quick` | **Skills**: []
  **Parallelization**: Wave 3.

  **Commit**: YES — `feat(storage): canvas entries in displays table with usage tracking`

### Wave 4: Tests + Verification (4 tasks)

- [x] 13. Test output_router.py

  **What to do**:
  - New file: `tests/test_output_router.py`
  - Test: `[voice]hello[/voice]` → VoiceContentFrame
  - Test: `[text]note[/text]` → TextContentFrame
  - Test: `[canvas]<h1>x</h1>[/canvas]` → CanvasContentFrame
  - Test: untagged text → TextContentFrame (fallback)
  - Test: nested tags → graceful (emit as text)
  - Test: partial tag streaming (no closing tag yet) → buffers
  - Test: empty tags `[voice][/voice]` → skipped
  - Test: unknown tag `[bogus]x[/bogus]` → TextContentFrame with warning

  **Recommended Agent Profile**: `deep` | **Skills**: []
  **Parallelization**: Wave 4, depends on Wave 2.

  **Commit**: YES — `test: add OutputRouter unit tests`

- [x] 14. Test canvas_output + extend test_mute_gate

  **What to do**:
  - New file: `tests/test_canvas_output.py`
  - Test: valid HTML written to displays table
  - Test: external resources stripped
  - Test: >64KB truncated
  - Test: canvas disabled in mode → dropped
  - Extend `tests/test_mute_gate.py`: verify text/canvas frames survive mute_bot=1
  - Extend `tests/test_mute_gate.py`: verify voice frames dropped with mute_bot=1

  **Recommended Agent Profile**: `unspecified-high` | **Skills**: []
  **Parallelization**: Wave 4.

  **Commit**: YES — `test: canvas output tests + mute gate extension`

- [x] 15. Full test suite + regression check

  **What to do**:
  - `uv run pytest tests/ -v --tb=short`
  - Verify no regression in existing tests
  - All new tests pass
  - Import smoke: `uv run python -c "from src.pipeline.stages.output_router import OutputRouter; print('OK')"`

  **Recommended Agent Profile**: `deep` | **Skills**: []
  **Parallelization**: Wave 4, depends on T13+T14.

  **Commit**: NO (verification only)

- [x] 16. Dead code cleanup

  **What to do**:
  - Remove old `mute_output` references (keep deprecated alias, remove usage)
  - Remove any hardcoded single-output assumptions in pipeline comments
  - Verify `assistant_response_logger` still works correctly alongside new TextOutputProcessor
  - Check: no orphaned TTS path code outside VoiceOutputProcessor

  **Recommended Agent Profile**: `quick` | **Skills**: []
  **Parallelization**: Wave 4.

  **Commit**: YES — `chore: remove dead single-output code`

---

## Final Verification Wave

- [x] F1. **Plan Compliance Audit** — `oracle`
  Verify: all 3 output types work, mode gating correct, mute only gates voice, tagged text used (not JSON). Output: `VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Lint + tests + generic code check. Output: `VERDICT`

- [x] F3. **Real QA** — `unspecified-high`
  Multi-output scenarios: voice+canvas same turn, mute drops voice only, silent mode no voice. Output: `Scenarios [N/N] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  Per-task diff review, guardrails check. Output: `VERDICT`

---

## Commit Strategy

| Wave | Pattern |
|------|---------|
| 1 | `feat(modes):` / `feat(prompt):` |
| 2 | `feat(pipeline):` / `refactor(pipeline):` |
| 3 | `refactor(mute):` / `feat(modes):` / `feat(storage):` |
| 4 | `test:` / `chore:` |

---

## Success Criteria

```bash
# Mode outputs correct
uv run python -c "from src.agent.modes import MODE_PROFILES; assert MODE_PROFILES['silent'].outputs == frozenset({'text','canvas'})"

# Pipeline assembles
uv run python -c "from src.pipeline.build import _assemble_native_stages; print('OK')"

# All tests pass
uv run pytest tests/ -q --tb=short

# Canvas writes to DB
# (integration test via CanvasOutputProcessor test)
```

### Final Checklist
- [ ] 3 output channels functional (voice, text, canvas)
- [ ] Mode profiles control availability
- [ ] mute_bot only gates voice
- [ ] Tagged text format validated with DeepSeek (>80% compliance)
- [ ] Pipeline stays linear (no branching)
- [ ] Canvas uses DB sink (displays table)
- [ ] All existing tests pass
