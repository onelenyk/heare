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


## MANUAL
<!-- User content. Never auto-pruned. -->

