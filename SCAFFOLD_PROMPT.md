# Heare — Scaffolding Prompt

Paste this into a new Claude Code session to scaffold the full project.

---

Scaffold a Python voice assistant project called **"heare"**.

## Project Vision
An ambient voice AI that passively listens via microphone, decides autonomously whether to respond based on context, and speaks Ukrainian. NOT a wake-word assistant — it uses an AI decision layer to determine relevance.

## Tech Stack
- **Framework**: Pipecat (pipecat-ai) — open source voice AI framework
- **STT**: Groq Whisper Large v3 Turbo via GroqSTTService (native Pipecat service)
- **VAD**: Silero VAD (built into Pipecat)
- **Turn detection**: Smart Turn v3 (LocalSmartTurnAnalyzerV3) — local, 12ms, Ukrainian supported
- **LLM**: Claude API via anthropic SDK (claude-sonnet-4-6)
- **TTS**: edge-tts library with voice "uk-UA-PolinaNeural" (Ukrainian, female)
- **Audio**: sounddevice for mic/speaker I/O

## Core Architecture

The pipeline has ONE custom component — DeciderProcessor — that sits between STT output and LLM:

```
Mic → Silero VAD → Groq Whisper STT
                          ↓ TranscriptionFrame
                   Smart Turn v3 (end-of-turn detection)
                          ↓ UserSpeechEndFrame
             [custom] DeciderProcessor
                          ↓ passes frame forward ONLY if should_respond=true
                   Claude API (LLM)
                          ↓ TextFrame
                   EdgeTTS (Ukrainian TTS)
                          ↓ AudioFrame
                        Speaker
```

## DeciderProcessor

Custom Pipecat FrameProcessor. On every UserSpeechEndFrame:
1. Reads transcript from frame
2. Builds context: current time, mode, last 5 transcripts from rolling buffer, session state
3. Calls Claude API with decider prompt
4. Parses JSON response: {respond: bool, confidence: float, reason: string, reply: string}
5. If respond=true AND confidence >= 0.7: passes frame downstream with reply injected
6. If respond=false: logs to SQLite and drops frame

## Modes (user configurable)
- `focus` — respond ONLY if directly addressed by name or explicit question into silence
- `pair` — also respond if user seems stuck (repeated frustration words, unanswered questions)
- `idle` — respond to casual topics and questions
- `silent` — log only, never speak

## Project Structure
```
heare/
├── plugin.json              # Claude Code plugin manifest
├── pyproject.toml           # Python 3.11+, deps: pipecat-ai[groq,silero], anthropic, edge-tts, sounddevice
├── .env.example             # GROQ_API_KEY, ANTHROPIC_API_KEY
├── README.md
├── PLAN.md
├── src/
│   ├── main.py              # entry point, builds Pipecat pipeline, starts daemon
│   ├── pipeline.py          # assembles full pipeline with all services
│   ├── decider.py           # DeciderProcessor (custom FrameProcessor)
│   ├── tts_edge.py          # EdgeTTS wrapper as Pipecat TTS service
│   ├── context.py           # ContextBuilder — assembles state for decider
│   ├── storage.py           # SQLite transcript + decision log
│   └── config.py            # settings, mode enum, prompts
├── prompts/
│   ├── decider.txt          # prompt for autonomous respond/don't-respond decision
│   └── persona.txt          # assistant personality/system prompt
├── skills/
│   ├── voice-start.md       # Claude Code skill: start daemon
│   ├── voice-stop.md        # Claude Code skill: stop daemon
│   └── voice-mode.md        # Claude Code skill: change mode (focus/pair/idle/silent)
└── tests/
    ├── test_decider.py
    └── fixtures/
```

## Decider Prompt (save to prompts/decider.txt)
```
You are the listening brain of an ambient voice assistant called Heare.
You hear everything said near the user's microphone.
Your job: decide whether to respond at all.

CONTEXT:
- Current time: {time}
- Mode: {mode}
- Last 5 transcripts:
{recent_transcripts}

NEW TRANSCRIPT: "{transcript}"

RULES:
- silent mode: ALWAYS respond=false
- focus mode: respond ONLY if user says your name or asks a direct question into silence
- pair mode: also respond if user seems stuck (frustration words, repeated questions)
- idle mode: respond to casual questions and topics if clearly not directed at someone else

NEVER respond when:
- User is talking to someone else (phone call, family)
- Background noise (TV, music)
- User thinking out loud mid-task without a question
- Already responded to same content < 30 seconds ago
- confidence < 0.7

Return ONLY valid JSON:
{
  "respond": true|false,
  "confidence": 0.0-1.0,
  "reason": "one sentence",
  "reply": "Ukrainian response text if respond=true, else null"
}
```

## Persona Prompt (save to prompts/persona.txt)
```
You are Heare — a warm, sharp ambient assistant.
Speak Ukrainian. Be brief. Never repeat what the user said back to them.
Max 2-3 sentences per reply unless asked to explain something complex.
```

## plugin.json skeleton
```json
{
  "name": "heare",
  "version": "0.1.0",
  "description": "Ambient voice AI — listens, decides, speaks Ukrainian",
  "skills": [
    {"name": "voice-start", "file": "skills/voice-start.md"},
    {"name": "voice-stop", "file": "skills/voice-stop.md"},
    {"name": "voice-mode", "file": "skills/voice-mode.md"}
  ]
}
```

## Requirements
- Python 3.11+
- Use `uv` for dependency management
- All async (asyncio)
- Daemon runs as background process
- Graceful shutdown on SIGTERM/SIGINT
- Retry logic on API failures (Groq, Anthropic)
- Never crash — log errors and continue listening

## What to generate
1. Full pyproject.toml with all dependencies
2. All Python files with real implementation (not stubs)
3. Both prompts
4. plugin.json
5. .env.example
6. README with setup instructions and usage
7. Basic test stubs in tests/

Use Pipecat's official patterns from pipecat-ai docs.
For EdgeTTS, use `edge-tts` Python library with async API.
For Smart Turn, use `LocalSmartTurnAnalyzerV3` from pipecat.
