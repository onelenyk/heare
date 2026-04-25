# Stage 7: Anti-Slop — Dead Code, Duplication & Drift Audit

**Date:** 2026-04-23
**Repo:** /Users/lenyk/myprojects/heare
**Branch:** s2s-realtime

---

## BLOCKER

### [FINDING:B1] Dual `shutdown()` in GeneratorProcessor — real cleanup silently overridden

[EVIDENCE] `src/generator.py:216-220` defines:
```python
async def shutdown(self) -> None:
    """Cleanup resources including config watcher."""
    if self._config_watcher is not None:
        self._config_watcher.stop()
        self._config_watcher = None
```
Then `src/generator.py:505-507` defines a **second** method with the same name inside the same class body (both inside `_build_generator_processor_class()`):
```python
async def shutdown(self) -> None:
    """No-op: parity with DeciderProcessor.shutdown() for main teardown."""
    return
```
Python's class machinery resolves method name conflicts to the **last definition**. The no-op at line 505 overwrites the real implementation at line 216. Result: on every daemon teardown `_config_watcher.stop()` is never called; the asyncio polling task leaks and is never cancelled.

[CONFIDENCE] HIGH — confirmed by reading both definitions in the same class body within the closure.

**Fix:** Delete lines 505-507. The first `shutdown()` is correct and complete.

---

### [FINDING:B2] `DeciderProcessor` is dead code — never instantiated in any live path

[EVIDENCE] `src/pipeline.py:88-104` wires `create_generator_processor` only. There is no `create_decider_processor` call anywhere in `src/main.py` or `src/pipeline.py`. `src/generator.py:506,510` contains explicit "No-op: parity with DeciderProcessor" stubs, confirming the intent was to freeze-out the decider. `src/pipeline.py:6` docstring states `generator_mode flag retired`. Yet `src/decider.py` is ~1100 lines, fully maintained.

`create_decider_processor` at `src/decider.py:1095` is never called from production code. `src/heartbeat.py` calls `on_heartbeat_tick()` on the processor reference (confirmed `src/main.py:324`), but that reference is always `generator` (a `GeneratorProcessor`) — its `on_heartbeat_tick` is a no-op stub (`src/generator.py:509-511`).

[CONFIDENCE] HIGH.

**Fix:** Move `src/decider.py` to `archive/decider.py` or delete. Delete `src/tests/test_decider.py` (1650+ lines) once confirmed. The heartbeat no-op stubs in `generator.py` can then be removed as well.

---

## MAJOR

### [FINDING:M1] Three sources of truth for the tool allowlist — drift guaranteed

[EVIDENCE]
- `src/actions.py:38-46` — `ALLOWED_TOOLS: set[str]` with lowercase names (`bash`, `read`, `write`, `edit`, `web_fetch`, `web_search`, `workflow`)
- `src/actions.py:63-71` — `INTENT_TOOL_TO_SDK: dict[str,str]` mapping each to CamelCase
- `src/config.py:113-122` — `Settings.agent_sdk_allowed_tools: list[str]` with CamelCase (`Bash`, `Read`, `Write`, `Edit`, `WebFetch`, `WebSearch`)

`workflow` appears in `ALLOWED_TOOLS` and `INTENT_TOOL_TO_SDK` but is **absent** from `agent_sdk_allowed_tools`. Adding a new tool requires editing three places. The MCP expansion logic at `src/config.py:211-214` only modifies `agent_sdk_allowed_tools`, not `ALLOWED_TOOLS`.

[CONFIDENCE] HIGH.

**Fix:** Single source of truth in `config.py`. Derive `ALLOWED_TOOLS` and `INTENT_TOOL_TO_SDK` programmatically from `Settings.agent_sdk_allowed_tools` at import time. Eliminates `workflow` omission bug and any future three-way sync requirement.

---

### [FINDING:M2] `workflow save` is a documented lie — returns a help string, never saves

[EVIDENCE] `src/actions.py:351-354`:
```python
elif cmd == "save":
    # For now, save requires CLI - workflow files are JSON
    summary = "Щоб зберегти робочий поток, створіть файл у ~/.heare/workflows/<name>.json"
    await self._safe_call_result(intent, summary)
```
The user says "save workflow X" and hears "to save a workflow, create a file manually." There is no `WorkflowStore.save()` implementation invoked. The `workflow` tool is advertised as supporting `list`, `run`, `save` — `save` is hollow.

[CONFIDENCE] HIGH.

**Fix:** Either implement `WorkflowStore.save(name, steps)` parsing the args string, or remove `save` from the command list and the tool description, and stop advertising it.

---

### [FINDING:M3] `turn_aggregation_enabled` defaults False, path gated on `turn_aggregator is not None` — effectively dead in production

[EVIDENCE] `src/config.py:89`: `turn_aggregation_enabled: bool = False`. `src/decider.py:602`: `if self.turn_aggregator is not None:` — `TurnAggregator` is only constructed if `turn_aggregation_enabled` is True. `src/pipeline.py` never constructs a `TurnAggregator` (pipeline is now generator-only). Since `DeciderProcessor` is already dead (B2), the entire `src/turn_aggregator.py` module and the config field are unreachable.

[CONFIDENCE] HIGH.

**Fix:** Delete `src/turn_aggregator.py`, `tests/test_turn_aggregator.py`, and remove `turn_aggregation_enabled`, `focus_mode_turn_timeout`, `ambient_mode_turn_timeout`, `max_turn_duration` from `Settings`.

---

### [FINDING:M4] `claude_decider_model` config field used only in dead code path

[EVIDENCE] `src/config.py:57`: `claude_decider_model: str = "haiku"`. Used at `src/claude_cli.py:232,267` inside `call_decider()` only. `call_decider()` is invoked by `DeciderProcessor` which is dead (B2). In `AgentSDKCLI.call_decider()` at `src/agent_sdk_cli.py:353-355` the field is not used at all. The field survives purely as scaffolding.

[CONFIDENCE] HIGH.

**Fix:** Remove `claude_decider_model` from `Settings` when `decider.py` is deleted.

---

### [FINDING:M5] `max_conversation_age_hours` defined in `Settings` but never read anywhere in `src/`

[EVIDENCE] `src/config.py:94`: `max_conversation_age_hours: float = 24.0`. Grepping all `src/*.py` finds zero additional references. `src/storage.py` uses `transcript_retention_days` (a different field) for purge. `src/conversation.py` does not read `max_conversation_age_hours`.

[CONFIDENCE] HIGH.

**Fix:** Remove the field, or wire it to `ConversationManager` purge logic if the feature is intended.

---

### [FINDING:M6] Two separate mode-file hot-reload paths with no consolidation

[EVIDENCE]
- `src/config.py:225-313` — `ConfigWatcher` polls `config.toml` for `enable_mcp_servers` changes, every 2s.
- `src/decider.py:489-497` — on every `TranscriptionFrame`, the decider reads `settings.mode_file` inline (blocking I/O on the event loop): `raw = mode_file.read_text().strip()`.
- `src/watch.py:63-64` — dashboard also reads `mode_file` on render.

Three separate callers each independently poll or read the mode file. `ConfigWatcher` covers `config.toml` but not `mode_file`. There is no unified hot-reload manager.

[CONFIDENCE] HIGH.

**Fix:** Extend `ConfigWatcher` to also track `mode_file` mtime and push a `Mode` callback. Remove inline `mode_file.read_text()` from `decider.py` (already dead) and consolidate dashboard read into the same watcher.

---

### [FINDING:M7] `identity.py::render_persona` uses `str.format()` — inconsistent with rest of codebase

[EVIDENCE] `src/identity.py:72-78`:
```python
def render_persona(template: str, identity: Identity) -> str:
    return template.format(
        name=identity["name"], creature=identity["creature"],
        vibe=identity["vibe"], emoji=identity["emoji"],
    )
```
All other template rendering uses `_safe_substitute` from `src/context.py:237` (regex-based, safe against JSON braces). The `prompts/generator.txt` and `prompts/decider.txt` files must avoid `{…}` literals when loaded through `str.format()`. If a user's identity fields contain braces, this raises `KeyError`.

`src/context.py:3`: "Uses regex-based placeholder substitution rather than str.format() so the JSON example literal in prompts/decider.txt doesn't get parsed as format specifiers."

[CONFIDENCE] HIGH.

**Fix:** Replace `template.format(…)` in `identity.py:73` with `_safe_substitute(template, {…})` imported from `context.py`.

---

### [FINDING:M8] `_SCRUB_PATTERNS` in `generator.py` is a growing leak-patch list — root cause is parser gap

[EVIDENCE] `src/generator.py:43-58` — 7 patterns patching TTS output post-hoc: bash word boundary, "Bash completed with no output", JSON key fragments `"tool":`, `"args":`, `</intent>` tags, double-space cleanup.

The comment at line 46 says "Must run BEFORE the standalone bash word-boundary rule." Order dependency is a code smell. `src/intent_parser.py` already correctly separates `emittable_text` from intent bodies. The leakage only happens when the LLM emits partial intent JSON outside of tags, or when Claude Code's bash status line bleeds through. The parser cannot suppress those because they arrive as plain text (not in `<intent>` tags).

The proper fix is in the prompt (instruct the LLM never to emit tool names as prose) or in structured output, not regex scrubbing. Each new Claude SDK tool invocation pattern risks adding another regex.

[CONFIDENCE] HIGH.

**Fix:** Add a prompt constraint: "Never mention tool names or JSON fragments in your spoken response." Remove individual JSON-key scrub patterns (lines 52-55); keep only the `</intent>` tag scrubber and the `Bash completed` phrase scrubber as true defensive patterns.

---

## MINOR

### [FINDING:N1] `test_workflow_manual.py` at repo root is orphaned — not under `tests/`, not in CI

[EVIDENCE] File exists at `/Users/lenyk/myprojects/heare/test_workflow_manual.py` (root). `ls tests/` shows no corresponding `test_workflow.py` shadow — `tests/test_workflow.py` exists but is separate. The root file has a `sys.path.insert` hack and a `uv run python` run comment — it was a one-off manual test. It is not discovered by pytest (no `tests/` prefix, not in `pytest.ini` paths).

[CONFIDENCE] HIGH.

**Fix:** Delete `test_workflow_manual.py`. If the test scenarios are valuable, migrate assertions to `tests/test_workflow.py`.

---

### [FINDING:N2] `FIXED_PHRASES` re-export in `decider.py` is a legacy stub marked for deletion

[EVIDENCE] `src/decider.py:67`:
```python
from .tts_phrases import FIXED_PHRASES  # noqa: F401, E402
```
Comment: "Relocated to src/tts_phrases.py in Phase 2.1 (US-P2.1-07b). Re-exported here for any legacy importers; flagged for deletion in 2.7."

No file in `src/` or `tests/` imports `FIXED_PHRASES` from `decider.py` — only from `tts_phrases.py`. So this re-export is already dead.

[CONFIDENCE] HIGH.

**Fix:** Remove line 67 from `src/decider.py` (or remove the whole file per B2).

---

### [FINDING:N3] `direct_tools.py::_is_simple_tool` has a redundant double-`False` return

[EVIDENCE] `src/direct_tools.py:37-44`:
```python
def _is_simple_tool(tool: str) -> bool:
    if tool in SIMPLE_TOOLS:
        return True
    if tool.startswith("mcp__"):
        return False
    return False  # <-- redundant
```
The second `return False` is unreachable logic-equivalent to the first (MCP early-return). Both non-SIMPLE_TOOLS paths return `False`. The comment `# MCP tools are complex (need Claude)` makes it appear intentional, but the final `return False` adds no information.

[CONFIDENCE] HIGH.

**Fix:** Remove the `if tool.startswith("mcp__"): return False` branch; the final `return False` covers all non-simple tools.

---

### [FINDING:N4] `DeciderState` enum in `config.py` is only used by dead `decider.py`

[EVIDENCE] `src/config.py:26-29` defines `DeciderState(LISTENING, AWAITING_CONFIRMATION, EXECUTING)`. Grep: only `src/decider.py` imports and uses it. When `decider.py` is deleted, `DeciderState` becomes unused.

[CONFIDENCE] HIGH.

**Fix:** Remove `DeciderState` from `src/config.py` when `decider.py` is deleted.

---

### [FINDING:N5] `.omc/` has 14 completed-phase PRD/progress file pairs — unbounded accumulation

[EVIDENCE] `ls .omc/` shows: `prd-bcde-completed.json`, `prd-daemon-polish-completed.json`, `prd-latency-phase-B-completed.json`, `prd-phase-A-completed.json`, `prd-phase-A-observable-actions-completed.json`, `prd-phase-AH2-action-polish-completed.json`, `prd-phase-b-0-3-col-dashboard-completed.json`, `prd-phase-B-polish-completed.json`, `prd-phase-B-prompt-allowlist-completed.json`, `prd-phase-B-tools-completed.json`, `prd-phase2.2-completed.json`, `prd-realtime-progress-watch-completed.json`, `prd-speaker-phase-1-completed.json`, `prd-speaker-phase-2-track-b-completed.json`, plus matching `progress-*-completed.txt` files. Every phase leaves permanent files.

[CONFIDENCE] HIGH.

**Fix:** Move all `*-completed.*` files to `.omc/archive/`. Keep only `prd.json` and `progress.txt` at the top level. Add a one-liner cleanup script: `mv .omc/*-completed.* .omc/archive/`.

---

### [FINDING:N6] `src/logs.py` hard-codes `~/.heare/logs/daemon.log` path independently of `Settings`

[EVIDENCE] `src/logs.py:59`: `log_file = Path.home() / ".heare" / "logs" / "daemon.log"`. This duplicates `settings.log_dir / "daemon.log"` from `src/main.py:86-88`. If a user sets a custom `log_dir` in `config.toml`, `src/logs.py` will still read the default path.

[CONFIDENCE] HIGH.

**Fix:** Pass `settings` to `logs.py` read function and use `settings.log_dir / "daemon.log"`.

---

### [FINDING:N7] `confirmation_passphrase` enforcement lives only in dead `DeciderProcessor`

[EVIDENCE] `src/config.py:77`: `confirmation_passphrase: str | None = None`. Validation at `src/config.py:170-183`. Usage: `src/decider.py:905,915-916` — passphrase check in `DeciderProcessor._handle_awaiting_confirmation()`. Since `DeciderProcessor` is never instantiated (B2), setting `confirmation_passphrase` in config has **zero effect** at runtime.

[CONFIDENCE] HIGH.

**Fix:** Either re-implement passphrase gating in `GeneratorProcessor._handle_transcription` (cancel-gate already exists there) or remove the field and document that passphrase confirmation is not implemented.

---

### [FINDING:N8] `tests/test_direct_tools.py` mocks the function under test at the wrong layer

[EVIDENCE] `tests/test_direct_tools.py:74-75`:
```python
with patch("src.direct_tools._execute_bash", new_callable=AsyncMock) as mock_bash:
    mock_bash.return_value = {"success": True, "output": "hello", "error": None}
```
This test patches `_execute_bash` and then calls `execute_direct("bash", ...)` — which calls `_execute_bash`. The mock intercepts the real function, so the test asserts only the routing logic, not the bash execution itself. A bug inside `_execute_bash` would not be caught. The tests for `_execute_bash` proper (lines 100-161) do use real `asyncio.create_subprocess_shell` mocked at the subprocess level, which is correct.

[CONFIDENCE] MEDIUM — the routing tests are useful but mislabeled as integration coverage.

**Fix:** Rename patching tests to `test_execute_direct_routes_to_*` and add a note that they test dispatch only, not execution.

---

### [FINDING:N9] `src/agent_sdk_cli.py` writes per-call `claude-<ts>.log` files with no rotation or cleanup

[EVIDENCE] `src/agent_sdk_cli.py:386-387`:
```python
self.settings.log_dir.mkdir(parents=True, exist_ok=True)
log_file = self.settings.log_dir / f"claude-{int(time.time() * 1000)}.log"
```
Same pattern at `src/claude_cli.py:295-296`. Every Claude CLI call creates a new timestamped file. No max-file-count or purge logic exists for these files. The daemon log uses `RotatingFileHandler` (10MB, 3 backups) but the per-call logs accumulate indefinitely.

[CONFIDENCE] HIGH.

**Fix:** Cap per-call log accumulation: keep only the last N (e.g. 50) `claude-*.log` files. Add a purge step in `_cmd_start` alongside the existing `store.purge_older_than()` call.

---

### [FINDING:N10] `src/decider.py` mode-file read is synchronous blocking I/O inside async handler

[EVIDENCE] `src/decider.py:489-497` (inside `async def _build_prompt`):
```python
mode_file = self.settings.mode_file
if not mode_file.exists():
    ...
raw = mode_file.read_text().strip()
```
`Path.read_text()` is synchronous. Called on every transcription in the async event loop. Even though this is in dead code (B2), it's a pattern to not repeat in `generator.py` or `watch.py`.

[CONFIDENCE] HIGH (as anti-pattern to document).

**Fix:** Use `asyncio.get_event_loop().run_in_executor(None, mode_file.read_text)` or maintain an in-memory mode cache updated by a watcher callback.

---

## PRIORITIZED CLEANUP TODO (impact / effort)

| # | Action | Severity | Files | Effort |
|---|--------|----------|-------|--------|
| 1 | **Delete second `shutdown()` no-op** (B1) — fixes real resource leak | BLOCKER | `src/generator.py:505-507` | 1 min |
| 2 | **Delete `src/decider.py` + `tests/test_decider.py`** (B2) — removes ~2750 lines of dead code | BLOCKER | `src/decider.py`, `tests/test_decider.py`, `src/turn_aggregator.py`, `tests/test_turn_aggregator.py` | 1 hr |
| 3 | **Remove `DeciderState`, `claude_decider_model`, `turn_aggregation_enabled`, `focus_mode_turn_timeout`, `ambient_mode_turn_timeout`, `max_turn_duration`, `max_conversation_age_hours`** from `Settings` (M3,M4,M5,N4) | MAJOR | `src/config.py` | 30 min |
| 4 | **Single tool allowlist source of truth** (M1) — derive `ALLOWED_TOOLS` + `INTENT_TOOL_TO_SDK` from `Settings` | MAJOR | `src/actions.py`, `src/config.py` | 2 hr |
| 5 | **Implement or remove `workflow save`** (M2) | MAJOR | `src/actions.py:351-354` | 1 hr |
| 6 | **Fix `identity.py::render_persona` to use `_safe_substitute`** (M7) | MAJOR | `src/identity.py:73` | 10 min |
| 7 | **Trim `_SCRUB_PATTERNS`** — add prompt constraint, remove JSON-key patterns (M8) | MAJOR | `src/generator.py:43-58`, `prompts/generator.txt` | 30 min |
| 8 | **Archive `.omc/*-completed.*` files** (N5) | MINOR | `.omc/` | 5 min |
| 9 | **Fix `src/logs.py` hardcoded path** (N6) + **cap per-call `claude-*.log` accumulation** (N9) | MINOR | `src/logs.py:59`, `src/agent_sdk_cli.py:386`, `src/claude_cli.py:295` | 30 min |
| 10 | **Delete `test_workflow_manual.py`** at repo root (N1) | MINOR | `test_workflow_manual.py` | 2 min |

---

[STAGE_COMPLETE:7]
