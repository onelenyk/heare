# Heare Slimdown — Remove 6 Features

## TL;DR

> **Quick Summary**: Strip heare from ~400MB bundle target to ~150MB by removing 6 features (overlay UI, MCP memory server, YAMNet audio events, speaker recognition, pyaudio, Claude CLI dependency). Pipeline goes 24→18 stages, tools 48→37, drops onnxruntime (~100MB native), eliminates macOS lock-in.
>
> **Deliverables**:
> - ~45 files deleted, ~40 files modified
> - 5 pyproject.toml extras removed
> - 4 DB columns migrated off `transcripts` table
> - Pure Python `edit` tool implemented (replaces dead `execution="claude"` path)
> - Pipeline stages reduced from 24 to ~18
> - All tests pass with removed features
>
> **Estimated Effort**: Large (45+ files, multi-wave execution)
> **Parallel Execution**: YES — 5 waves, up to 8 tasks parallel
> **Critical Path**: Wave 1 → Wave 2 (tool cleanup) → Wave 3 (pipeline integration) → Wave 5 (verification)

---

## Context

### Original Request
"Lets plan removing some parts then. Lets remove speaker recognition, yamnet event detection, overlay, mcp memory server, Lets work on portaudio. lets fully remove usage of claude cli"

### Key Decisions
- **`edit` tool**: Implement as pure Python diff/handler (was dead code with `execution="claude"` and no handler)
- **`~/.claude.json` seeding**: Drop entirely — users configure MCPs in `workspace/.mcp.json` directly
- **PortAudio**: Keep as documented system requirement (needed by `sounddevice` which is the actual runtime dep)

### Research Findings
- 5 explore agents exhaustively mapped every import, config key, pipeline stage, tool schema, DB column, and test reference
- **Critical Discovery (Metis)**: `edit` tool is already broken — `get_claude_tools()` and `COMPLEX_TOOLS` are dead code, never consumed in the runtime path
- **Critical Discovery (Metis)**: `pyaudio` is only used by `test_recognizer.py` (deleted with speaker); actual runtime uses `sounddevice`
- **Critical Discovery (Metis)**: DB migration requires table recreation (SQLite can't DROP COLUMN); schema version must bump to 7

### Metis Review
**Identified Gaps** (addressed):
- `edit` tool already broken → Decision: implement pure Python handler
- `~/.claude.json` seeding policy → Decision: drop entirely
- DB migration complexity → Addressed: dedicated migration script in Wave 4
- Watch dashboard speaker/audio refs → Addressed: dedicated Wave 4 cleanup task
- `get_claude_tools()` / `COMPLEX_TOOLS` dead code → Addressed: remove in Wave 2

---

## Work Objectives

### Core Objective
Remove 6 features to shrink the dependency tree, eliminate the Claude CLI requirement, and enable cross-platform capability — without breaking the core daemon functionality (STT → LLM → TTS pipeline).

### Concrete Deliverables
- Deleted: `src/overlay/`, `src/store/memory/`, `src/audio_event/`, `src/voice/speaker/`, `src/daemon/claude_capabilities.py`
- Modified: `pyproject.toml`, `src/config.py`, `src/pipeline/build.py`, `src/main.py`, `src/store/storage.py`, `src/agent/tools/*`, `hearectl`
- New: `migrations/007_drop_speaker_audioevent.sql` (DB migration)
- New: `src/agent/tools/direct.py` — `edit` tool handler
- Clean: all test files, docs, README

### Definition of Done
- [ ] `uv run python -c "import src.main"` succeeds without Claude CLI, onnxruntime, pyaudio, or pywebview installed
- [ ] `uv run python -m src.main start --help` shows no deleted CLI commands
- [ ] `uv run pytest tests/ -v -x --tb=short` — all tests pass, no import errors from deleted modules
- [ ] `uv sync --no-dev` on clean venv completes without errors
- [ ] Pipeline assembles with `_assemble_native_stages()` accepting ~6 fewer params
- [ ] DB migration 007 applies to existing v6 DB without data loss
- [ ] `edit` tool works: `uv run python -c "from src.agent.tools.direct import execute_direct; ..."` → edits a file

### Must Have
- Core pipeline works: mic → STT → LLM → TTS → speaker (unchanged)
- All remaining tools work (bash, read, write, web_search, web_fetch, browser bridge, skill install, etc.)
- Watch dashboard compiles and runs
- Tests pass

### Must NOT Have (Guardrails)
- No `import` of `onnxruntime` in any remaining source file (it was only used by speaker + YAMNet)
- No `import` of `fastmcp`, `pywebview`, `pyobjc`, `fastapi`, `uvicorn` in any remaining source
- No `shutil.which("claude")` or `claude -p` subprocess calls
- No `import pyaudio`
- No `execution="claude"` in tool registry
- No `audio_event_label`, `audio_event_score`, `speaker_id`, `speaker_confidence` in DB schema or queries
- No dead code left: `get_claude_tools()`, `COMPLEX_TOOLS`, `_is_simple_tool()` must be removed

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (pytest via `uv run pytest`)
- **Automated tests**: Tests-after (existing tests validate correctness; we fix tests after deletions)
- **Framework**: pytest (existing)
- **Agent-Executed QA**: ALL tasks include Playwright/curl/bash scenarios

### QA Policy
Every task MUST include agent-executed QA scenarios.
- **CLI/API**: Use Bash (curl + python -c) for import checks, tool execution, and pipeline assembly
- **Tests**: `uv run pytest` with specific filters
- Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.txt`

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — 8 tasks, all independent):
├── T1: pyproject.toml cleanup [quick]
├── T2: config.py dead settings removal [quick]
├── T3: Edit tool pure Python implementation [deep]
├── T4: Delete overlay package [quick]
├── T5: Delete memory server package [quick]
├── T6: Delete audio_event package [quick]
├── T7: Delete speaker package + scripts + tests [quick]
└── T8: Delete claude_capabilities + bench scripts + hearectl edit [quick]

Wave 2 (After Wave 1 — 7 tasks, all independent):
├── T9: Clean pipeline/build.py [deep]
├── T10: Clean agent/tools/ (registry, schemas, direct.py) [deep]
├── T11: Clean main.py (CLI, imports, onboarding) [unspecified-high]
├── T12: Clean storage.py + migration script [deep]
├── T13: Clean context.py + context_injector.py [unspecified-high]
├── T14: Clean indication/core.py + skills/installer.py [quick]
└── T15: Clean onboarding.py + workspace.py [quick]

Wave 3 (After Wave 2 — 4 tasks):
├── T16: Clean watch/ dashboard files [unspecified-high]
├── T17: Clean pipeline/stages/ files [quick]
├── T18: DB migration (apply + test) [deep]
└── T19: Update all docs (README, ARCHITECTURE_*) [quick]

Wave 4 (After Wave 3 — 3 tasks):
├── T20: Delete dedicated test files [quick]
├── T21: Fix cross-referencing test files [unspecified-high]
└── T22: Full test suite run + import smoke test [deep]

Wave FINAL (After Wave 4 — 4 parallel reviews):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real manual QA (unspecified-high)
└── F4: Scope fidelity check (deep)
```

**Critical Path**: T1/T2/T3 → T10 → T18 → T22 → F1-F4
**Max Concurrent**: 8 (Wave 1)
**Parallel Speedup**: ~70% faster than sequential

### Agent Dispatch Summary
- **Wave 1**: 8 — T1-T2 → `quick`, T3 → `deep`, T4-T8 → `quick`
- **Wave 2**: 7 — T9 → `deep`, T10 → `deep`, T11-T15 → `unspecified-high`/`quick`
- **Wave 3**: 4 — T16 → `unspecified-high`, T17 → `quick`, T18 → `deep`, T19 → `quick`
- **Wave 4**: 3 — T20 → `quick`, T21 → `unspecified-high`, T22 → `deep`
- **FINAL**: 4 — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

### Wave 1: Foundation + Deletions (8 tasks, ALL parallel)

- [x] 1. Clean pyproject.toml — remove 5 optional extras

  **What to do**:
  - Remove `[project.optional-dependencies]` entries for: `local` (pyaudio), `overlay` (fastapi+uvicorn+pywebview+pyobjc), `memory` (fastmcp), `audio-event` (onnxruntime+numpy), `speaker` (onnxruntime+numpy+huggingface-hub)
  - Keep: core deps (sounddevice, pipecat-ai, etc.), dev deps (pytest, ruff, mypy)
  - After removal, run `uv lock` to regenerate uv.lock

  **Must NOT do**:
  - Do NOT remove `sounddevice` from core deps (needed for pipeline audio)
  - Do NOT remove `numpy` if it's still a transitive dep of pipecat-ai (check uv.lock after)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with T2-T8)
  - **Blocks**: T9-T15 (all wave 2 tasks)
  - **Blocked By**: None

  **References**:
  - `pyproject.toml:19-44` — Current optional-dependencies section with all 5 extras

  **QA Scenarios**:
  ```
  Scenario: pyproject.toml has no removed extras
    Tool: Bash
    Steps:
      1. grep -c '\[project.optional-dependencies\]' pyproject.toml → should be 0 (no extras section)
      2. grep -c 'pyaudio\|fastmcp\|pywebview\|pyobjc\|onnxruntime.*speaker\|onnxruntime.*audio-event' pyproject.toml → should be 0
    Expected Result: All 5 extras removed, no orphaned dependency strings
    Evidence: .sisyphus/evidence/task-1-pyproject-clean.txt

  Scenario: uv lock succeeds
    Tool: Bash
    Steps:
      1. uv lock
      2. Check exit code = 0
    Expected Result: Lockfile regenerates without removed packages
    Evidence: .sisyphus/evidence/task-1-uv-lock.txt
  ```

  **Commit**: YES
  - Message: `chore: remove 5 optional extras (overlay, memory, audio-event, speaker, local)`
  - Files: `pyproject.toml`, `uv.lock`

- [x] 2. Clean src/config.py — remove dead settings

  **What to do**:
  - Remove 4 YAMNet settings: `audio_event_detection_enabled`, `audio_event_threshold`, `yamnet_model_path`, `audio_event_file` (lines 201-208)
  - Remove 24 speaker settings: all `speaker_id_*`, `speaker_namer_*`, `speakers_file` (lines 232-272)
  - Remove 5 deprecated Claude CLI fields: `claude_cli`, `claude_timeout_seconds`, `claude_max_retries`, `claude_max_calls_per_minute`, `claude_decider_model` (lines 219-223)
  - Remove `claude_sdk_cli_path` (line 273)
  - Remove `memory_db_path` (line 392) and `HEARE_MEMORY_DB` env handler (lines 492-494)
  - Remove `HEARE_CLAUDE_CLI` env handler (lines 537-540, already marked no-op)

  **Must NOT do**:
  - Do NOT remove `speaker_namer_model` if it's only used as part of the now-removed speaker settings block (which it should be part of)
  - Do NOT remove any non-deprecated, actively used settings

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Can run in parallel with all Wave 1 tasks. Blocks nothing specific (other tasks read config.py at runtime).

  **References**:
  - `src/config.py:201-208` — YAMNet settings block
  - `src/config.py:219-223` — Deprecated claude_cli fields
  - `src/config.py:232-272` — All speaker settings
  - `src/config.py:273` — claude_sdk_cli_path
  - `src/config.py:392` — memory_db_path
  - `src/config.py:492-494` — HEARE_MEMORY_DB env handler
  - `src/config.py:537-540` — HEARE_CLAUDE_CLI env handler

  **QA Scenarios**:
  ```
  Scenario: No removed settings in config.py
    Tool: Bash (grep)
    Steps:
      1. grep -c 'audio_event_detection_enabled\|audio_event_threshold\|yamnet_model_path\|audio_event_file' src/config.py → 0
      2. grep -c 'speaker_id_enabled\|speaker_namer_enabled\|speakers_file\|speaker_id_onnx' src/config.py → 0
      3. grep -c 'claude_cli\|claude_timeout\|claude_max_retries\|claude_max_calls\|claude_decider\|claude_sdk_cli' src/config.py → 0
      4. grep -c 'memory_db_path\|HEARE_MEMORY_DB\|HEARE_CLAUDE_CLI' src/config.py → 0
    Expected Result: Zero matches for all removed settings
    Evidence: .sisyphus/evidence/task-2-config-clean.txt
  ```

  **Commit**: YES
  - Message: `chore: remove dead settings from config.py (speaker, yamnet, claude_cli, memory)`
  - Files: `src/config.py`

- [x] 3. Remove `edit` tool and dead execution="claude" code

  **What to do**:
  - Remove `edit` tool from registry.py (lines 106-112)
  - Remove `edit` tool schema from schemas.py
  - Remove `get_claude_tools()` function from registry.py (lines 506-508) — dead code
  - Remove `COMPLEX_TOOLS` import/usage from direct.py (lines 43, 46) — dead code
  - Remove `ExecutionType` literal `"claude"` — change to `Literal["direct", "workflow", "mcp"]`
  - Remove `edit` from meeting mode's `denied_tool_patterns` in modes.py (if present)

  **Must NOT do**:
  - Do NOT remove `write` tool (LLM uses it to create/modify files)
  - Do NOT leave `execution="claude"` as a valid type — remove from Literal

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Can run in parallel with all Wave 1 tasks.

  **QA Scenarios**:
  ```
  Scenario: edit tool no longer in registry
    Tool: Bash (grep)
    Steps:
      1. grep -c '"edit"' src/agent/tools/registry.py → 0
      2. grep -c 'execution.*claude' src/agent/tools/registry.py → 0
      3. grep -c 'get_claude_tools\|COMPLEX_TOOLS' src/agent/tools/direct.py → 0
    Expected Result: Zero matches
    Evidence: .sisyphus/evidence/task-3-edit-removed.txt
  ```

  **Commit**: YES
  - Message: `chore: remove broken edit tool and dead claude execution code`
  - Files: `src/agent/tools/registry.py`, `src/agent/tools/schemas.py`, `src/agent/tools/direct.py`

- [x] 4. Delete overlay UI package

  **What to do**:
  - `rm -rf src/overlay/`
  - `rm -f tests/test_overlay_server.py`
  - `rm -f .omc/plan-overlay-ui.md`
  - Edit `hearectl`: remove `OVERLAY_PID_FILE` (line 20), overlay start from `cmd_start()` (lines 81-89), overlay stop from `cmd_stop()` (lines 93-96), entire `cmd_overlay()` (lines 186-214), entire `cmd_overlay_stop()` (lines 216-230), help lines, case branches
  - Update pipeline comments (build.py:901, cancel_flag_gate.py:5) — remove "overlay" from lists

  **Must NOT do**:
  - Do NOT touch `cancel_flag_gate.py` or `mute_gate.py` logic — only update comments
  - Do NOT remove the flag file mechanism (mute.flag, cancel.flag) — still used by watch dashboard

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Can run in parallel with all Wave 1 tasks.

  **QA Scenarios**:
  ```
  Scenario: Overlay files no longer exist
    Tool: Bash
    Steps:
      1. test -d src/overlay && echo "FAIL" || echo "PASS"
      2. test -f tests/test_overlay_server.py && echo "FAIL" || echo "PASS"
      3. grep -c 'overlay' hearectl → 0
    Expected Result: All overlay files deleted, hearectl has no overlay references
    Evidence: .sisyphus/evidence/task-4-overlay-gone.txt
  ```

  **Commit**: YES
  - Message: `chore: remove overlay UI (pywebview always-on-top window)`
  - Files: `src/overlay/`, `tests/test_overlay_server.py`, `.omc/plan-overlay-ui.md`, `hearectl`

- [x] 5. Delete MCP memory server package

  **What to do**:
  - `rm -rf src/store/memory/`
  - `rm -f tests/test_memory_server.py tests/test_memory_store.py`

  **Must NOT do**:
  - Do NOT touch `src/store/conversation.py` (ConversationManager — completely separate)
  - Do NOT touch `tests/test_conversation_memory_phase2.py` (unrelated)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Can run in parallel with all Wave 1 tasks.

  **QA Scenarios**:
  ```
  Scenario: Memory server files deleted
    Tool: Bash
    Steps:
      1. test -d src/store/memory && echo "FAIL" || echo "PASS"
      2. test -f tests/test_memory_server.py && echo "FAIL" || echo "PASS"
    Expected Result: All deleted
    Evidence: .sisyphus/evidence/task-5-memory-gone.txt
  ```

  **Commit**: YES
  - Message: `chore: remove MCP memory server (fastmcp)`
  - Files: `src/store/memory/`, `tests/test_memory_server.py`, `tests/test_memory_store.py`

- [x] 6. Delete audio event (YAMNet) package

  **What to do**:
  - `rm -rf src/audio_event/`
  - `rm -f scripts/yamnet_mic.py scripts/yamnet_probe.py`
  - `rm -f tests/test_audio_event.py`
  - `rm -f .omc/plans/yamnet-audio-event-detection.md`

  **Must NOT do**:
  - Do NOT touch pipeline/build.py yet (handled in T9)
  - Do NOT touch storage.py yet (handled in T12)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Can run in parallel with all Wave 1 tasks.

  **QA Scenarios**:
  ```
  Scenario: Audio event files deleted
    Tool: Bash
    Steps:
      1. test -d src/audio_event && echo "FAIL" || echo "PASS"
      2. test -f scripts/yamnet_mic.py && echo "FAIL" || echo "PASS"
      3. test -f tests/test_audio_event.py && echo "FAIL" || echo "PASS"
    Expected Result: All deleted
    Evidence: .sisyphus/evidence/task-6-audioevent-gone.txt
  ```

  **Commit**: YES
  - Message: `chore: remove YAMNet audio event detection`
  - Files: `src/audio_event/`, `scripts/yamnet_*.py`, `tests/test_audio_event.py`, `.omc/plans/yamnet-audio-event-detection.md`

- [x] 7. Delete speaker recognition package + tests + scripts

  **What to do**:
  - `rm -rf src/voice/speaker/`
  - `rm -f src/test_recognizer.py`
  - `rm -f scripts/fetch_speaker_onnx.py`
  - `rm -f tests/test_speaker_id.py tests/test_speaker_gallery.py tests/test_speaker_processor.py tests/test_speaker_namer.py`
  - `rm -f .omc/plans/speaker-recognition.md .omc/plans/speaker-recognition-phase-2.md .omc/prd-speaker-phase-1-completed.json .omc/prd-speaker-phase-2-track-b-completed.json`

  **Must NOT do**:
  - Do NOT touch pipeline/build.py yet (handled in T9)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Can run in parallel with all Wave 1 tasks.

  **QA Scenarios**:
  ```
  Scenario: Speaker files deleted
    Tool: Bash
    Steps:
      1. test -d src/voice/speaker && echo "FAIL" || echo "PASS"
      2. test -f src/test_recognizer.py && echo "FAIL" || echo "PASS"
      3. test -f tests/test_speaker_id.py && echo "FAIL" || echo "PASS"
    Expected Result: All deleted
    Evidence: .sisyphus/evidence/task-7-speaker-gone.txt
  ```

  **Commit**: YES
  - Message: `chore: remove speaker recognition (ECAPA + gallery + namer)`
  - Files: `src/voice/speaker/`, `src/test_recognizer.py`, `scripts/fetch_speaker_onnx.py`, 4 test files, 4 OMC plans

- [x] 8. Delete claude_capabilities + bench scripts

  **What to do**:
  - `rm -f src/daemon/claude_capabilities.py`
  - `rm -f scripts/bench_claude_ttft.py scripts/bench_persistent_claude.py scripts/bench_openrouter.py`
  - Edit `hearectl`: remove `claude` from the HELP message and any claude-related setup steps (review full file)

  **Must NOT do**:
  - Do NOT remove `src/daemon/workspace.py` (MCP seeding, but we drop ~/.claude.json dependency in T15)
  - Do NOT remove `src/agent/llm/switchable.py` (uses Anthropic API, not claude CLI binary)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Can run in parallel with all Wave 1 tasks.

  **QA Scenarios**:
  ```
  Scenario: Claude capabilities file deleted
    Tool: Bash
    Steps:
      1. test -f src/daemon/claude_capabilities.py && echo "FAIL" || echo "PASS"
      2. test -f scripts/bench_claude_ttft.py && echo "FAIL" || echo "PASS"
    Expected Result: All deleted
    Evidence: .sisyphus/evidence/task-8-claude-gone.txt
  ```

  **Commit**: YES
  - Message: `chore: remove claude_capabilities and bench scripts`
  - Files: `src/daemon/claude_capabilities.py`, `scripts/bench_*.py`, `hearectl`

### Wave 2: Cross-Cut Source Changes (7 tasks, ALL parallel)

- [x] 9. Clean pipeline/build.py — remove speaker + YAMNet stages

  **What to do**: Remove `speaker_buffer`, `speaker_tagger`, `audio_event_observer` params from `_assemble_native_stages()`. Delete speaker chain creation block (lines 548-566). Delete YAMNet observer creation block (lines 935-953). Update pipeline diagram comments.
  **Must NOT do**: Do NOT remove always-on stages.
  **Recommended Agent Profile**: `deep` | **Skills**: []
  **Parallelization**: Wave 2 (with T10-T15). Depends on Wave 1.
  **QA**: `uv run python -c "from src.pipeline.build import _assemble_native_stages; import inspect; sig = inspect.signature(_assemble_native_stages); assert 'speaker_buffer' not in str(sig); print('OK')"`
  **Commit**: YES — `refactor: remove speaker and YAMNet stages from pipeline`

- [x] 10. Clean agent/tools/ — unregister speaker tools, remove edit + claude type

  **What to do**: Remove 5 speaker tools from registry.py, schemas.py, direct.py. Remove `get_claude_tools()` dead code. Remove `execution="claude"` from ExecutionType literal. Remove `edit` tool registration.
  **Recommended Agent Profile**: `deep` | **Skills**: []
  **Parallelization**: Wave 2 (with T9, T11-T15)
  **QA**: `uv run python -c "from src.agent.tools.registry import TOOLS; print(f'{len(TOOLS)} tools'); assert len(TOOLS) < 48"`
  **Commit**: YES

- [x] 11. Clean main.py — remove speaker CLI + claude_capabilities import

  **What to do**: Remove `refresh_capabilities` import/call, speaker imports/init, namer task, all speaker CLI functions (_cmd_enroll_owner, _cmd_test_recognizer, _cmd_speakers_*), argparse entries, command routing.
  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 2 (with T9-T10, T12-T15)
  **QA**: `uv run python -m src.main --help 2>&1 | grep -c 'enroll-owner\|speakers\|test-recognizer'` → 0
  **Commit**: YES

- [x] 12. Clean storage.py + create DB migration

  **What to do**: Remove `speaker_id`, `speaker_confidence`, `audio_event_label`, `audio_event_score` from CREATE TABLE, log_transcript signature, INSERT, and all SELECT queries. Bump SCHEMA_VERSION to 7. Create `migrations/007_drop_speaker_audioevent.sql` with table recreation.
  **Recommended Agent Profile**: `deep`
  **Parallelization**: Wave 2
  **QA**: `grep -c 'speaker_id\|audio_event' src/store/storage.py` → 0; migration file exists
  **Commit**: YES

- [x] 13. Clean context.py + context_injector.py

  **What to do**: Remove SpeakerGallery import, speaker_gallery field, _render_rule_block, _resolve_label, _audio_event_suffix from context.py. Remove current_audio_event injection from context_injector.py.
  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 2
  **QA**: `grep -c 'SpeakerGallery\|audio_event\|current_audio' src/store/context.py src/agent/llm/context_injector.py` → 0
  **Commit**: YES

- [x] 14. Clean indication/core.py + skills/installer.py

  **What to do**: Remove 10 speaker IndicationKinds + _enrollment_active flag. Remove is_owner_enrolled() from installer.py.
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 2
  **QA**: `grep -c 'REENROLL\|is_owner_enrolled' src/voice/indication/core.py src/skills/installer.py` → 0
  **Commit**: YES

- [x] 15. Clean onboarding.py + workspace.py

  **What to do**: Remove claude_installed/claude_configured steps, refresh_capabilities import. Rewrite capabilities_cached to use local enumeration. Remove ~/.claude.json seeding from workspace.py.
  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 2
  **QA**: `grep -c 'claude' src/daemon/onboarding.py src/daemon/workspace.py` → 0
  **Commit**: YES

### Wave 3: Dashboard + Stages + Docs (4 tasks)

- [x] 16. Clean watch/ dashboard files

  **What to do**: Remove AudioEventData, read_audio_event, load_speaker_labels, speaker_style from data.py. Remove audio_event from widgets.py VoiceStateBar. Update app.py call.
  **Recommended Agent Profile**: `unspecified-high`
  **QA**: `uv run python -c "from src.watch.app import HeareDashboard; print('OK')"`
  **Commit**: YES

- [x] 17. Clean pipeline/stages/ files

  **What to do**: Remove audio_event constants and _latest_audio_event() from transcription_gate.py. Remove speaker_id/audio_event from assistant_response_logger.py. Update cancel_flag_gate.py comment.
  **Recommended Agent Profile**: `quick`
  **QA**: `grep -c 'speaker_id\|audio_event' src/pipeline/stages/transcription_gate.py src/pipeline/stages/assistant_response_logger.py` → 0
  **Commit**: YES

- [x] 18. Apply DB migration and verify

  **What to do**: Create test DB with v6 schema + sample data. Run migration 007. Verify 4 columns removed, data preserved, version=7.
  **Recommended Agent Profile**: `deep`
  **QA**: Migration applies cleanly, recent_transcripts() works
  **Commit**: YES

- [x] 19. Update all documentation

  **What to do**: README: remove overlay, memory MCP, YAMNet, speaker sections, Claude CLI prerequisite. ARCHITECTURE_*: remove relevant sections. heare.env.example: remove HEARE_CLAUDE_CLI.
  **Recommended Agent Profile**: `quick`
  **QA**: `grep -c 'yamnet\|overlay\|speaker recognition\|claude CLI' README.md || echo "Clean"`
  **Commit**: YES

### Wave 4: Test Cleanup + Verification (3 tasks)

- [x] 20. Delete dedicated test files + fix cross-references

  **What to do**: Verify all dedicated test files deleted from Wave 1. Fix ~15 remaining test files that reference speaker/audio_event/claude/overlay. Remove FakeClaudeCLI, speaker settings overrides, audio_event fixtures.
  **Recommended Agent Profile**: `unspecified-high`
  **QA**: `uv run pytest tests/ --collect-only -q 2>&1 | tail -1`
  **Commit**: YES

- [x] 21. Run full test suite

  **What to do**: `uv run pytest tests/ -v -x --tb=short 2>&1 | tee .sisyphus/evidence/task-21-test-run.txt`. Fix any failures.
  **Recommended Agent Profile**: `deep`
  **QA**: 0 failures, 0 errors
  **Commit**: NO (verification)

- [x] 22. Import smoke test + forbidden dep check

  **What to do**: Import main, pipeline, tools, storage. Run forbidden import grep. Verify no onnxruntime, pyaudio, pywebview, fastmcp, shutil.which("claude"), execution="claude", or dead code.
  **Recommended Agent Profile**: `deep`
  **QA**: All imports succeed, all greps clean
  **Commit**: NO (verification)

---

## Final Verification Wave

- [x] F1. **Plan Compliance Audit** — `oracle`
  Verify all "Must Have" present, all "Must NOT Have" absent. Check evidence files. Output verdict.

- [x] F2. **Code Quality Review** — `unspecified-high`
  Lint + test run. Check dead imports, unused vars, broken docstrings.

- [x] F3. **Real Manual QA** — `unspecified-high`
  Start clean. Verify daemon help, pipeline assembly, tool registry, DB migration, all imports.

- [x] F4. **Scope Fidelity Check** — `deep`
  Per-task diff review. Verify 1:1 spec-to-code. Check "Must NOT do" compliance.

---

## Commit Strategy

| Wave | Tasks | Pattern |
|------|-------|---------|
| 1 | T1-T8 | `chore: remove {feature}` (separate commits) |
| 2 | T9-T15 | `refactor: {description}` |
| 3 | T16-T19 | `refactor:` / `feat:` |
| 4 | T20 | `test: remove {feature} tests` |

---

## Success Criteria

```bash
# Import smoke test
uv run python -c "import src.main; print('OK')"

# Pipeline
uv run python -c "from src.pipeline.build import _assemble_native_stages; print('OK')"

# Tools
uv run python -c "from src.agent.tools.registry import TOOLS; print(len(TOOLS))"

# Full test suite
uv run pytest tests/ -v -x --tb=short

# Forbidden imports
grep -r 'onnxruntime\|pyaudio\|pywebview\|fastmcp' src/ --include='*.py' && echo "FAIL" || echo "CLEAN"
grep -r 'shutil.which..claude' src/ --include='*.py' && echo "FAIL" || echo "CLEAN"
grep -r 'get_claude_tools\|COMPLEX_TOOLS\|execution.*claude' src/ --include='*.py' && echo "FAIL" || echo "CLEAN"
```
