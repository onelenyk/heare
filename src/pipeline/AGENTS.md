# src/pipeline/

Pipecat audio pipeline assembly + 17 custom stages. All stages run in a single asyncio loop.

## STRUCTURE

```
pipeline/
├── build.py             # build_pipeline() — composition root (1153 lines)
├── session_state.py     # Mode + language + flush hook compositor
├── language_state.py    # Observable language tracking
├── bot_speech_state.py  # Shared bot-speaking flag
├── echo_state.py        # Ring buffer for bot audio (echo cancellation)
├── transport_fix.py     # FixedLocalAudioTransport for .app stability
└── stages/              # 18 files (17 stages + __init__)
    ├── transcription_gate.py   # Central orchestration hub (890 lines)
    ├── echo_gate.py            # Acoustic cross-correlation echo suppress
    ├── webrtc_aec_filter.py    # WebRTC AEC3 adaptive filter
    ├── echo_classifier.py      # LLM-based ECHO vs INTERRUPTION
    ├── mute_gate.py            # Input/output mute via flag files
    ├── gain_control.py         # Mic gain + speaker volume
    ├── interrupt_toggle_gate.py# Barge-in enable/disable
    ├── cancel_flag_gate.py     # External interrupt trigger
    ├── voice_state_observer.py # VAD state → dashboard
    ├── agent_state_observer.py # Bot state → dashboard
    ├── assistant_response_logger.py # Captures LLM text to DB
    ├── tts_scrub_processor.py  # Strips tool narration before TTS
    ├── usage_recorder.py       # Cost tracking
    ├── audio_monitor.py        # Sidetone processor
    ├── turn_aggregator.py      # VAD-driven turn capture
    ├── text_injector.py        # Dashboard text injection
    └── text_scrub.py           # Text cleanup
```

## ORDER (from `_assemble_native_stages()`)

```
Mic → mute_gate(input) → gain(input) → interrupt_toggle → cancel_flag
  → echo_gate → aec_filter → sidetone → GroqSTT
  → voice_state_observer → echo_classifier → transcription_gate
  → system_prompt_injector → user_aggregator
  → SwitchableLLMService
  → assistant_response_logger → tts_scrub → EdgeTTS
  → usage_recorder → agent_state_observer → sound_cue_processor
  → mute_gate(output) → echo_collector → gain(output) → Speaker
```

## STAGE FACTORY PATTERN

Every stage uses a `create_<name>(settings, ...)` factory function. Pipecat imports are deferred inside the factory body — never at module level. Use `global _cls` caching for the class to avoid redefining on every call:

```python
def create_foo_processor(settings):
    from pipecat.processors.frame_processor import FrameProcessor
    class FooProcessor(FrameProcessor):
        ...
    return FooProcessor(...)
```

## GOTCHAS

- `transcription_gate.py` dynamically builds its class via `_build_transcription_gate_class()` — test through the factory, don't import the class directly
- `TranscriptionFrame` vs `LLMMessagesAppendFrame` — see root ANTI-PATTERNS
- `echo_gate` + `aec_filter` = defense-in-depth echo cancel. Disable per config.
- Pipeline teardown MUST use `asyncio.shield()` — cancellation skips cleanup
