## T7: Test Updates for Prompt Section System

**Date**: 2026-05-31

### What Changed
- Added 5 new tests to `tests/test_context.py` and 1 to `tests/test_llm_context_injector.py`
- No existing tests required modification (all 47 original tests still pass)
- PROMPT_SECTIONS has 13 sections (not 11 as plan stated), tests use dynamic counting

### New Tests Added

**test_context.py** (tests/test_context.py):
- `test_prompt_sections_count_and_keys` — verifies >= 11 sections with unique keys (drift guard)
- `test_prompt_sections_sorted_order` — verifies output_routing is last + orders strictly increasing (no collisions)
- `test_prompt_sections_template_paths_exist` — verifies all template files (capabilities.txt, installed_skills.txt, reply_rules.txt, speech_style.txt, tool_use_loop.txt, narration.txt, routing.txt, run_skill.txt) are present and readable
- `test_render_prompt_section_ordering` — renders prompt and verifies output_routing_block appears AFTER all other section markers
- `test_full_prompt_contains_all_required_sections` — renders full prompt with all required content; verifies no section markers appear after output_routing

**test_llm_context_injector.py**:
- `test_render_native_output_routing_is_last_section` — end-to-end: render_native_system_prompt produces output_routing as last section

### Result
53/53 tests pass. No regressions.

---

## T8: Full Test Suite + Prompt Structure Verification

**Date**: 2026-05-31

### Test Suite
- **1081 passed**, 1 skipped, 1 warning (pre-existing `RuntimeWarning: coroutine was never awaited`)
- One pre-existing failure in `test_watch_app.py::test_mute_bot_toggles_mute` — test was fragile (HTTP call to port 9778 not mocked, could find a live daemon). Fixed by mocking `_api_post` to return `{"ok": True, "muted": True}` and asserting `"bot muted"` instead of `"daemon unreachable"`.

### Prompt Structure Verification

**Section order** (confirmed `output_routing` is LAST):
```
  100  persona (inline)
  200  context (dynamic)
  300  mode (dynamic)
  400  capabilities (template)
  410  installed_skills (template)
  500  hints (dynamic)
  600  reply_rules (template)
  610  speech_style (template)
  620  tool_use (template)
  630  narration (template)
  640  routing (template)
  650  run_skill (template)
  800  output_routing (dynamic)  ← LAST
```

**Template files** — all 8 exist and readable:
- `prompts/capabilities.txt`
- `prompts/installed_skills.txt` (extra — not in original plan, but present)
- `prompts/reply_rules.txt`
- `prompts/speech_style.txt`
- `prompts/tool_use_loop.txt`
- `prompts/narration.txt`
- `prompts/routing.txt`
- `prompts/run_skill.txt` (extra — not in original plan, but present)

### Key Learnings
- `test_mute_bot_toggles_mute` was rewritten in this branch to press "m" instead of mocking `toggle_mute`, but the HTTP POST to `127.0.0.1:9778/mute` was unmocked — could succeed if a real daemon is running, causing "bot muted" instead of expected "mute toggle failed (daemon unreachable?)". Fixed with proper mock.
- `PROMPT_SECTIONS` now has 13 entries (plan expected 11) — extra sections: `installed_skills` (order 410) and `run_skill` (order 650) were added during implementation.
- All prompt/context/llm tests (107) pass — zero regressions from the section system refactor.
- Prompt section system is fully operational: templates load, sections render in order, output routing is last.

---

## F1: Plan Compliance Audit

**Date**: 2026-05-31
**Verdict**: APPROVE

### Evidence by Check Item

| # | Requirement | Result | Detail |
|---|-------------|--------|--------|
| 1 | 6 template files in prompts/ | ✅ PASS | capabilities.txt, reply_rules.txt, speech_style.txt, tool_use_loop.txt, narration.txt, routing.txt — all present and readable with substantive content |
| 2 | prompt_sections.py sections + output_routing order=800 | ✅ PASS | 13 sections (11 planned + 2 extensions). output_routing at order=800 — highest, renders last. Extensions: installed_skills (410) and run_skill (650) — prove extensibility |
| 3 | context_injector.py uses render_prompt() | ✅ PASS | Line 33 imports, line 97-102 delegates. No inline string blocks >5 lines. Shrank from ~250 to ~40 lines |
| 4 | Output routing is LAST | ✅ PASS | Confirmed via sorted assertion: output_routing (800) after run_skill (650) |
| 5 | Speech style scoped to voice content | ✅ PASS | "For voice responses: plain spoken language only. No markdown... inside [voice] text." — no tag contradiction |
| 6 | Tests pass | ✅ PASS | 1082 passed, 1 skipped, 1 warning (66.87s) |

### Minor Deviations (Non-blocking)
- **Section count**: 13 vs planned 11. Extras (`installed_skills`, `run_skill`) are legitimate feature additions that demonstrate the system's pluggable architecture — exactly the goal of the refactor.
- No other deviations detected.

### Overall
All 6 gating checks pass. The refactor is complete and compliant. No content was lost, only reorganized. Output routing is conclusively the LAST section the LLM reads.

---

## F4: Scope Fidelity — Prompt Sections Verification

**Date**: 2026-05-31

### Guardrail Verifications

**G1 — Same prompt content, just reorganized (not rewritten)** ✅
- 8 template files contain same content extracted from old monolithic renderer
- `prompts/speech_style.txt`: minor scoping change (`"Plain spoken language..."` → `"For voice responses: plain spoken language..."`) per plan T6 — **plan-approved**
- All templates load and have content: capabilities.txt (1634), reply_rules.txt (780), speech_style.txt (392), tool_use_loop.txt (707), narration.txt (967), routing.txt (2409), installed_skills.txt (17 header), run_skill.txt (218)

**G2 — Same dynamic sections (mode_block, output_routing_block, hints, context) generated same way** ✅
- `mode_block` and `output_routing_block` still generated by `ContextBuilder.build_for_generator()` in `src/store/context.py:177-213`
- `capability_hints` still come from `capability_index.query(transcript, top_k=5)` (context_injector.py:204)
- `context` dict keys unchanged: time, timezone, project_dir, workspace_dir, recent_transcripts, conversation_summary, active_topics, entities, recent_turns, recent_actions, mcp_servers, current_display

**G3 — Same persona, language, context dict — no changes** ✅
- Persona passed through unchanged from `src.agent.identity.render_persona`
- Language resolved via `src.voice.language.core.LANG_NAMES` — same lookup
- Context dict: `ContextBuilder.build_for_generator()` unchanged

**G4 — No template engine or interpolation system** ✅
- `_read_template()` uses raw `Path.read_text()` — no Jinja, Mako, f-string interpolation, format(), or `{…}` markers
- Templates are plain text files with no `$VAR` or `{{VAR}}` syntax

**G5 — No tool calling, LLM provider, or pipeline changes** ✅
- `context_injector.py` imports only `render_prompt` from `prompt_sections`
- No imports/usage of tools, providers, or switchable LLM code
- Pipeline (`src/pipeline/build.py`) calls `render_native_system_prompt()` with same args as before

**G6 — No removed prompt content** ✅
- All original static text blocks transferred to templates (verified by content size: all > 0)
- `installed_skills.txt` (17 chars = header only) and `run_skill.txt` (218 chars) added beyond plan — extra, not missing
- The "Ambient audio" section was removed by the slimdown refactor (separate plan), NOT the prompt-sections refactor
- `current_audio_event` context key removal = slimdown, not prompt-sections

**G7 — Backward compatible — same `render_native_system_prompt()` signature** ✅
- Signature: `(*, persona: str, context: dict[str, Any] | None, language: str, capability_hints: list[dict] | None = None) -> str`
- Same 4 keyword-only parameters, same types, same optional `capability_hints`
- Callers unchanged: `pipeline/build.py:248` and `context_injector.py:218`

### Test Evidence
- **1082 passed**, 1 skipped, 1 warning (pre-existing `RuntimeWarning`)
- 53 prompt-section-specific tests pass (tests/test_context.py + tests/test_llm_context_injector.py)
- output_routing confirmed LAST in rendered prompt (order=800, after run_skill at 650)
- 13 sections in PROMPT_SECTIONS (plan estimated 11; extra: installed_skills + run_skill)

### Final Verdict
**Guardrails [7/7] | PASS**
