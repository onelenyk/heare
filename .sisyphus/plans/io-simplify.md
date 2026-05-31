# I/O Pipeline Simplification — Remove Output Routing

## TL;DR
Roll back the over-engineered output routing system. Pipeline returns to: LLM text → TTS (voice default). Canvas via existing `show_display()` tool. Delete ~800 lines of code, ~30 prompt lines, 5 files.

## Architecture: Before vs After

```
BEFORE (complex):                    AFTER (simple):
───────────────                      ───────────────
LLM text                              LLM text
  │                                     │
  ▼                                     ├──► TTS → speaker (default voice)
OutputRouter (parses tags)              │
  │                                     └──► show_display(html) → canvas
  ├──► VoiceOutput → TTS → speaker      
  ├──► TextOutput → transcript log       
  └──► CanvasOutput → displays table     
```

```
Pipeline stages:
  assistant_logger → output_router → voice → tts_scrub → tts → mute → text → canvas → speaker
  ↓
  assistant_logger → tts_scrub → tts → tts_fade → mute_gate → speaker
```

## TODOs

### Wave 1: Delete routing infrastructure (4 tasks, parallel)
- [ ] Delete `src/pipeline/stages/output_router.py`
- [ ] Delete `src/pipeline/stages/voice_output.py`
- [ ] Delete `src/pipeline/stages/text_output.py`
- [ ] Delete `src/pipeline/stages/canvas_output.py` (show_display handles canvas)

### Wave 2: Clean pipeline + prompt (4 tasks, parallel)
- [ ] `build.py` — remove output router + output processors from pipeline stages
- [ ] `context.py` — remove output_routing_block generation (the [voice]/[text]/[canvas] tag instructions)
- [ ] `context_injector.py` — remove output_routing_block injection
- [ ] `prompt_sections.py` — remove output_routing section, remove voice_output/text_output/canvas_output sections

### Wave 3: Tests + cleanup (3 tasks, parallel)
- [ ] Delete `tests/test_output_router.py`
- [ ] Delete `tests/test_canvas_output.py`
- [ ] Update tests that reference output routing

### Final
- [ ] Run `uv run pytest -q` — all pass
- [ ] Commit
