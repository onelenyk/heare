# src/voice/

Voice subsystems — STT, TTS, language detection, and non-speech indication cues.

## STRUCTURE

```
voice/
├── stt/                # GroqSTTService (Pipecat built-in) — no custom code
├── tts/
│   ├── edge.py         # Edge TTS Pipecat service (MP3 → ffmpeg → PCM)
│   ├── cache.py        # In-memory PCM cache + warmup
│   └── normalize.py    # Cyrillic spacing normalizer
├── language/
│   ├── core.py         # Language maps, cancel-word detectors, voice routing
│   └── detector.py     # LLM-based language detection (Claude)
└── indication/
    ├── core.py         # Indication facade (mode-gated, quiet-hours, cooldown)
    ├── assets.py       # Numpy PCM generators for cue sounds
    └── backends/
        ├── sound.py          # PCM cue injection into pipeline
        ├── visual.py         # JSONL log at ~/.heare/logs/indication.jsonl
        └── notification.py   # macOS Notification Center via AppleScript
```

## TTS

Edge TTS + ffmpeg conversion. TTSCache caches frequent phrases. `normalize_cyrillic_spacing()` fixes run-on Cyrillic words (DeepSeek streaming bug). 50ms fade-out on interruption.

## INDICATION

Non-speech cues for events (attention, error, success, input_waiting, etc.). Thread-safe `notify()` with mode gating, quiet hours, cooldown coalescing. Dispatches to up to 3 backends.

## GOTCHAS

- `language/detector.py` uses Claude for language detection — adds latency. The `core.py` cancel-word detectors run first (fast path)
- Indication is mode-aware: silent mode suppresses most cues
- TTS voice auto-switches on language change (2-turn hysteresis in `transcription_gate.py`)
- `edge.py` uses `asyncio.create_task()` internally for MP3 feed — edge case crash risk
- STT is pure Groq Whisper — no custom service. Configured in `pipeline/build.py`
