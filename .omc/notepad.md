# Notepad
<!-- Auto-managed by OMC. Manual edits preserved in MANUAL section. -->

## Priority Context
<!-- ALWAYS loaded. Keep under 500 chars. Critical discoveries only. -->

## Working Memory
<!-- Session notes. Auto-pruned after 7 days. -->
### 2026-05-30 22:30
Task 9: Cleaned `src/pipeline/build.py` — removed speaker and YAMNet stages from pipeline assembly.

**What**: Removed 3 params from `_assemble_native_stages()`: `speaker_buffer`, `speaker_tagger`, `audio_event_observer`. Removed speaker chain creation block (where `create_speaker_processors` was called). Removed YAMNet observer creation block (where `create_audio_event_observer` was called). Removed `speaker_gallery`, `speaker_model`, `namer_enqueue` from `build_pipeline()` signature. Updated call site and pipeline diagram comments.

**Where**: `src/pipeline/build.py` — 6 edits, clean grep verification (no leftover references).

**Verification**: inspect.signature assertions passed; all 9 todos completed.
### 2026-05-31 09:55
T9 completed: Replaced hardcoded provider enum/validation with `all_keys()` / `PROVIDERS` from providers registry.
- schemas.py: `"enum": ["deepseek", "zai", "opencode"]` → `"enum": all_keys()`
- direct.py: `if provider not in ("deepseek", "zai", "opencode")` → `if provider not in PROVIDERS`
- Both files import from `src.agent.llm.providers`
- Verified: python syntax compiles, imports resolve correctly, `all_keys()` == `['deepseek', 'zai', 'opencode']`
### 2026-05-31 10:03
## T17: Dead Code Audit Report

### CLEAN ✅

1. **Hardcoded provider strings**: Zero instances of `"deepseek"`, `"zai"`, or `"opencode"` hardcoded outside `src/agent/llm/providers.py` in either `src/` or `tests/`. All provider string usage goes through the centralized definitions.

2. **Stale OpenRouter references in source code**: Zero references to `openrouter`, `_or_service`, `_or_model`, or `build_openrouter_bootstrap` in `src/` or `tests/` Python files. The OpenRouter migration is complete — no dead code remains.

3. **Stale naming patterns**: Zero uses of the `_or_service` / `_or_model` naming convention remain. OpenRouter-specific field names have been fully migrated.

4. **Removed module imports**: Zero stale imports of removed modules. `src/agent/llm/switchable.py` is still legitimately imported by `src/pipeline/build.py` and remains active — it now delegates to the provider modules correctly.

5. **Groq references**: All legitimate — Groq is the STT provider for Whisper transcription.

### NEEDS ATTENTION ⚠️

1. **Stale `.env.example` entries**: Lines 8-10 still reference OpenRouter:
   - `# OpenRouter API key — used for LLM generation (default provider).`
   - `# Optional (required if using openrouter provider). Get a free key at https://openrouter.ai`
   - `OPENROUTER_API_KEY=`
   
   OpenRouter has been fully removed from the codebase. These lines are misleading to new developers. Should be removed or updated to reflect current providers (deepseek/zai/opencode).

### SUMMARY
- Source code: fully clean, no dead code
- `.env.example`: stale OpenRouter entries remain (low priority, configuration-only)



## 2026-05-30 22:30
Task 9: Cleaned `src/pipeline/build.py` — removed speaker and YAMNet stages from pipeline assembly.

**What**: Removed 3 params from `_assemble_native_stages()`: `speaker_buffer`, `speaker_tagger`, `audio_event_observer`. Removed speaker chain creation block (where `create_speaker_processors` was called). Removed YAMNet observer creation block (where `create_audio_event_observer` was called). Removed `speaker_gallery`, `speaker_model`, `namer_enqueue` from `build_pipeline()` signature. Updated call site and pipeline diagram comments.

**Where**: `src/pipeline/build.py` — 6 edits, clean grep verification (no leftover references).

**Verification**: inspect.signature assertions passed; all 9 todos completed.
### 2026-05-31 09:55
T9 completed: Replaced hardcoded provider enum/validation with `all_keys()` / `PROVIDERS` from providers registry.
- schemas.py: `"enum": ["deepseek", "zai", "opencode"]` → `"enum": all_keys()`
- direct.py: `if provider not in ("deepseek", "zai", "opencode")` → `if provider not in PROVIDERS`
- Both files import from `src.agent.llm.providers`
- Verified: python syntax compiles, imports resolve correctly, `all_keys()` == `['deepseek', 'zai', 'opencode']`


## 2026-05-30 22:30
Task 9: Cleaned `src/pipeline/build.py` — removed speaker and YAMNet stages from pipeline assembly.

**What**: Removed 3 params from `_assemble_native_stages()`: `speaker_buffer`, `speaker_tagger`, `audio_event_observer`. Removed speaker chain creation block (where `create_speaker_processors` was called). Removed YAMNet observer creation block (where `create_audio_event_observer` was called). Removed `speaker_gallery`, `speaker_model`, `namer_enqueue` from `build_pipeline()` signature. Updated call site and pipeline diagram comments.

**Where**: `src/pipeline/build.py` — 6 edits, clean grep verification (no leftover references).

**Verification**: inspect.signature assertions passed; all 9 todos completed.


## MANUAL
<!-- User content. Never auto-pruned. -->

