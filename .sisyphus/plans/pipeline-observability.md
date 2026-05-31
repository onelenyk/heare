# Pipeline Observability — Structured Event System

## Problem
Every module uses its own logger with no consistency. No way to trace a user utterance through the pipeline. Important events (barge-in, LLM response) buried in noise (auth failures, connection opens).

## Goal
Structured event stream from EVERY pipeline stage. Each processor emits typed events with timing and content. One format. Full traceability from mic to speaker.

## Architecture

```
Every pipeline processor:
  process_frame(frame, direction):
    await self.push_frame(frame)
    if significant(frame):
      emit_event({
        "stage": "transcription_gate",
        "event": "passed",
        "text": "Привіт",
        "lang": "uk",
        "ts": 1712345678.123
      })

emit_event() writes to:
  1. daemon.log   — human-readable line
  2. events.jsonl — machine-parseable JSON (for dashboards)
  3. State / DB   — latest N events for desktop polling
```

## Event schema

```python
@dataclass
class PipelineEvent:
    stage: str        # "stt", "gate", "llm", "tts", "mute", "vad"
    event: str        # "transcribed", "detected", "generated", "speaking", ...
    level: str        # "critical", "important", "info", "debug"
    ts: float         # unix timestamp
    data: dict        # stage-specific payload
```

## Stages to instrument

| Stage | Key events | Priority |
|-------|-----------|----------|
| VAD (Silero) | speech_start, speech_end, duration | info |
| STT (Groq) | transcribed, language_detected, latency | important |
| TranscriptionGate | passed, dropped_debounce, dropped_cancel, barge_in, lang_switch | important |
| SystemPromptInjector | context_built, tokens_count | debug |
| LLM (DeepSeek) | response_generated, tool_called, latency, token_count | important |
| TTS (EdgeTTS) | speaking_started, speaking_done, latency, char_count | info |
| MuteGate | voice_muted, voice_unmuted, frame_dropped | info |
| EchoGate | echo_detected, gate_active | debug |
| BrowserBridge | connected, disconnected, auth_failed (rate-limited) | info |

## Desktop integration

New endpoint: `GET /events` — returns last 50 events as JSON.
Desktop shows "📡 events" panel — live pipeline trace.

## Deliverables

- New: `src/daemon/events.py` — PipelineEvent + emit function
- Modified: `src/pipeline/stages/*.py` — emit events at key points
- Modified: `src/api.py` — `GET /events` endpoint
- Modified: `src/desktop/app.py` — events panel
- Cleaned: browser_bridge auth spam (rate-limit), tts_cache noise, httpx noise
