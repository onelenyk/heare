# T8: Wire output processors into build.py

## What was done
- Inserted OutputRouter after assistant_response_logger in _assemble_native_stages()
- Added voice_output, text_output, canvas_output as sequential stages
- Pipeline stays LINEAR
- New params: output_router, voice_output, text_output, canvas_output
- Processor creation in build_pipeline() with correct dependency ordering

## Key learnings
- Frame types MUST be actual classes (not factory functions) for cross-module isinstance checks
- VoiceContentFrame/CanvasContentFrame were defined in multiple files — centralized in output_router.py
- voice_output creation depends on tts_scrub and mute_gate → must be created AFTER them
- text_output and canvas_output don't depend on TTS components → can be created earlier
- The edit tool can silently fail if oldString doesn't match exactly — verify changes after each edit

## Pipeline stage order (after assistant_response_logger)
assistant_response_logger → output_router → voice_output → tts_scrub → tts → usage_recorder → tts_fade_observer → sound_cue_processor → mute_gate → echo_collector → text_output → canvas_output → transport_output → assistant_aggregator

## Files modified
- src/pipeline/build.py (imports + _assemble_native_stages + build_pipeline)
- src/pipeline/stages/output_router.py (frame types as proper Frame subclasses)
- src/pipeline/stages/voice_output.py (import VoiceContentFrame from output_router)
- src/pipeline/stages/canvas_output.py (import CanvasContentFrame, use .text field)

---

# T10: Mode-aware prompt injection — output_routing_block

## What was done
- Verified `src/store/context.py` `build_for_generator()` builds `output_routing_block` from `profile.outputs`
- Verified `src/agent/llm/context_injector.py` `render_native_system_prompt()` renders `output_routing_block` if present
- Added 5 explicit tests in `tests/test_context.py`

## Key learnings
- `output_routing_block` is only present when `session_state` is wired (guarded by `if self._session_state is not None`)
- `ModeProfile.outputs` controls which tags ([voice], [text], [canvas]) are marked available vs UNAVAILABLE
- Silent mode: no `voice` → `[voice] tag is UNAVAILABLE in silent mode`
- Meeting mode: only `text` → both voice and canvas UNAVAILABLE, no "Choose" hint (only 1 available tag)
- Ambient/focus/assistant: all 3 outputs → no UNAVAILABLE lines, "Choose" hint present
- Tests use `_FakeSessionState` with a `ModeProfile` to exercise the path without real pipeline objects
- Test `test_context_builder_keys_accounted_for` must be updated if `build()` adds new keys

## Tests added
- `test_output_routing_block_silent_mode_disables_voice`
- `test_output_routing_block_meeting_mode_only_text`
- `test_output_routing_block_ambient_mode_all_channels`
- `test_output_routing_block_absent_without_session_state`
- `test_output_routing_block_injected_into_render`

## Files modified
- tests/test_context.py (5 new tests)

## Verification
- All 31 tests in test_context.py pass (26 existing + 5 new)
- Quick sanity check: `uv run python -c "from src.agent.modes import MODE_PROFILES; p = MODE_PROFILES['silent']; assert 'voice' not in p.outputs; assert 'text' in p.outputs; print('OK')"` → OK

# T15: Full test suite + regression check

## What was done
- Ran full test suite: `uv run pytest tests/ -v --tb=short`
- 1064 passed, 1 skipped, 1 warning — all green
- Import smoke test initially failed because `OutputRouter` is defined inside `_build_processor_class()` (closure pattern for deferred Pipecat imports)
- Fixed by adding `__getattr__` module-level hook that triggers lazy class build on first access
- Import smoke test passes: all new modules (`OutputRouter`, `VoiceContentFrame`, `TextContentFrame`, `CanvasContentFrame`, `create_voice_output`, `create_text_output`, `create_canvas_output`) import cleanly
- No `mute_output` references remain in `src/pipeline/`

## Key learnings
- `OutputRouter` uses a closure pattern — class defined inside `_build_processor_class()` to defer Pipecat imports. Direct `from ... import OutputRouter` fails without a module-level `__getattr__` hook
- `__getattr__` pattern is Python's standard way to make lazily-created classes importable at module level
- New tests: `test_output_router.py` (12 tests), `test_context.py` (5 new tests) all pass
- Warnings: only `RuntimeWarning: coroutine was never awaited` in test_indication_notification — pre-existing and harmless

## Files modified
- src/pipeline/stages/output_router.py (added `__getattr__` + `_get_output_router_class` helper)

## F1 Plan Compliance Audit — 2026-05-31

**Result: ALL CHECKS PASS**

### Evidence per check:

1. **3 output channels** — `voice_output.py`, `text_output.py`, `canvas_output.py` all exist under `src/pipeline/stages/` and are wired via `create_voice_output()`, `create_text_output()`, `create_canvas_output()` in `build.py` (lines 848-865).

2. **ModeProfile.outputs** — `modes.py:51` defines `outputs: frozenset[str] = frozenset({"voice", "text", "canvas"})`. Per-mode: ambient/focus/assistant = all three, silent = {text, canvas}, meeting = {text} only.

3. **voice_muted rename** — `modes.py:61` has `voice_muted: bool = False`. `modes.py:64-66` has backward-compat `mute_output` property. All consumers (mute_gate.py:120, assistant_response_logger.py:162, voice_output.py:103) use `voice_muted`. Only deprecated alias remains.

4. **Mute gate only gates voice** — `mute_gate.py:126` only drops `TTSAudioRawFrame`. Explicit comment at lines 85-90 states text/canvas bypass. Pipeline places text_output and canvas_output AFTER mute_gate; they process different frame types.

5. **Tagged text validated** — T1 evidence shows 100% compliance (19/19 prompts with opening+closing tags, 0 silent-mode [voice] violations). Threshold was 80%.

6. **Pipeline linear** — `_assemble_native_stages()` builds a single flat `list`, no conditional routing branches. OutputRouter is a single inline FrameProcessor.

7. **Canvas DB sink** — `canvas_output.py:119` calls `store.insert_display(content_type="canvas/html", content=html)` → `storage.py:402-407` writes to `displays` table. No WebSocket code.

8. **System prompt routing** — `context.py:194-213` dynamically builds `output_routing_block` per mode profile, including unavailable-tag warnings. `context_injector.py:149-150` injects it into the system prompt.

9. **Tests pass** — 1075 passed, 1 skipped, 0 failures.

### Pipeline order (linear):
```
... → assistant_response_logger → output_router → voice_output →
tts_scrub → tts → usage_recorder → tts_fade → sound_cue →
mute_gate → echo_collector → text_output → canvas_output →
transport_output → assistant_aggregator
```
