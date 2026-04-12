# claudeclaw-voice — Feature Development Plan

> Ambient voice AI plugin for Claude Code. Passively listens, decides autonomously when to respond, speaks Ukrainian.

---

## 1. Vision

An **ambient** voice layer for Claude Code that:
- Always listens in the background (with voice activity detection)
- Uses Claude itself to decide whether any given utterance warrants a response
- Speaks Ukrainian naturally via TTS
- Lives as a Claude Code plugin — installs, starts, stops like any other plugin
- Never interrupts your flow unless it has something genuinely useful to say

**Not** a wake-word assistant ("Hey Siri"). Not a dictation tool. A *presence* — quiet by default, attentive always.

---

## 2. Technology Stack

| Layer | Choice | Why |
|---|---|---|
| STT | Groq Whisper Large v3 Turbo | Fast (~0.5s/minute audio), free tier generous, Ukrainian supported |
| VAD | Silero VAD | Lightweight, runs locally, best-in-class for speech/non-speech |
| LLM (decide) | Claude API (`claude-sonnet-4-6`) | Context-aware decision-making, multilingual |
| LLM (respond) | Same | Unified brain |
| TTS | EdgeTTS (`uk-UA-PolinaNeural` / `uk-UA-OstapNeural`) | Free, natural Ukrainian voice, no install |
| Audio I/O | `sounddevice` (Python) | Cross-platform, low latency |
| Daemon | Python asyncio | Async I/O for parallel listen + process + speak |
| State | SQLite | Transcript log, decision history |
| Plugin | Claude Code plugin manifest | Standard integration |

---

## 3. Architecture

```
┌────────────────────────────────────────────────────────┐
│                      Microphone                         │
└──────────────────────────┬─────────────────────────────┘
                           │ raw audio stream
                           ▼
┌────────────────────────────────────────────────────────┐
│  VAD (Silero) — gates only real speech, drops silence   │
└──────────────────────────┬─────────────────────────────┘
                           │ speech segments
                           ▼
┌────────────────────────────────────────────────────────┐
│  STT (Groq Whisper) — transcribes UA → text             │
└──────────────────────────┬─────────────────────────────┘
                           │ transcript + metadata
                           ▼
┌────────────────────────────────────────────────────────┐
│  Context Builder — assembles state for decision         │
│  • current time, open Claude Code session info          │
│  • last N transcripts, recent decisions                 │
│  • user-configurable "modes" (coding/idle/etc)          │
└──────────────────────────┬─────────────────────────────┘
                           │ context + transcript
                           ▼
┌────────────────────────────────────────────────────────┐
│  Decider (Claude) — "Should I respond to this?"         │
│  Returns: {respond: bool, reason, reply, confidence}    │
└──────────────────────────┬─────────────────────────────┘
                           │ decision
                           ▼
                   ┌───────┴───────┐
                   │ respond=false │────► log only, continue
                   │ respond=true  │────┐
                   └───────────────┘    │
                                        ▼
┌────────────────────────────────────────────────────────┐
│  TTS (EdgeTTS) — generates UA audio                     │
└──────────────────────────┬─────────────────────────────┘
                           │ mp3 stream
                           ▼
┌────────────────────────────────────────────────────────┐
│                       Speaker                           │
└────────────────────────────────────────────────────────┘
```

---

## 4. Project Structure

```
claudeclaw-voice/
├── plugin.json              # Claude Code plugin manifest
├── PLAN.md                  # this file
├── README.md                # user-facing docs
├── pyproject.toml           # Python deps (uv/poetry)
├── .env.example             # GROQ_API_KEY, ANTHROPIC_API_KEY
├── src/
│   ├── __init__.py
│   ├── daemon.py            # main process, orchestrates pipeline
│   ├── audio/
│   │   ├── capture.py       # mic input, ring buffer
│   │   ├── vad.py           # Silero voice activity detection
│   │   └── playback.py      # speaker output queue
│   ├── stt/
│   │   └── groq_whisper.py  # Groq Whisper client
│   ├── llm/
│   │   ├── decider.py       # "respond or not" Claude call
│   │   └── responder.py     # full response generation
│   ├── tts/
│   │   └── edge_tts.py      # EdgeTTS wrapper
│   ├── context/
│   │   ├── builder.py       # assemble context for decider
│   │   └── session.py       # Claude Code session awareness
│   ├── storage/
│   │   └── transcript_log.py # SQLite-backed history
│   └── config.py            # settings, prompts
├── skills/
│   ├── voice-start.md       # /voice-start — start listening
│   ├── voice-stop.md        # /voice-stop — stop
│   ├── voice-mode.md        # /voice-mode — change context mode
│   └── voice-status.md      # /voice-status — health check
├── prompts/
│   ├── decider.txt          # the "should I respond" prompt
│   └── responder.txt        # the response persona prompt
└── tests/
    ├── test_vad.py
    ├── test_stt.py
    ├── test_decider.py
    └── fixtures/            # sample audio for tests
```

---

## 5. Development Phases

### Phase 0 — Research & Validation (1 day)

**Goal:** de-risk the critical unknowns before coding anything.

- [ ] Test Groq Whisper with 5-10 Ukrainian audio samples
  - Measure accuracy vs. your accent
  - Measure end-to-end latency
- [ ] Test EdgeTTS voices — pick best Ukrainian one
  - `uk-UA-PolinaNeural` (female) vs `uk-UA-OstapNeural` (male)
  - Quality, pronunciation of technical terms
- [ ] Verify Silero VAD works on Mac with `sounddevice`
- [ ] Research Claude Code plugin daemon support
  - Can a plugin run a long-lived background process?
  - If not, daemon runs independently and plugin just provides skills
- [ ] Check Groq Whisper streaming vs batch — latency tradeoffs

**Output:** `RESEARCH.md` with findings, go/no-go on each component.

### Phase 1 — Manual MVP (2 days)

**Goal:** prove the pipeline end-to-end with zero autonomy.

- [ ] Python project scaffolding, deps installed
- [ ] Press-to-talk CLI: `python main.py` → Enter → speak → Enter → see/hear response
- [ ] STT module: raw audio → Groq → text
- [ ] LLM module: text → Claude API → response text
- [ ] TTS module: text → EdgeTTS → play audio
- [ ] End-to-end latency measurement (<3s target)
- [ ] Basic error handling (API down, no mic, network)

**Success criterion:** You can hold a Ukrainian conversation with it by pressing Enter.

### Phase 2 — Background Daemon (2 days)

**Goal:** make it listen continuously without interrupting you.

- [ ] Silero VAD integration — only process actual speech
- [ ] Ring buffer for rolling audio context
- [ ] Async pipeline: capture → VAD → STT queue
- [ ] SQLite transcript log (everything transcribed, nothing dropped)
- [ ] `/voice-start` and `/voice-stop` commands
- [ ] Daemon process management (start/stop/status)
- [ ] Still requires manual trigger — not yet deciding on its own

**Success criterion:** Daemon runs for hours, logs everything you say, doesn't hallucinate or crash.

### Phase 3 — Autonomous Decision (3 days) ⭐ core feature

**Goal:** the plugin decides on its own when to speak.

- [ ] Context builder: time, last 5 transcripts, active mode, session info
- [ ] Decider prompt design (most important work of this project)
- [ ] JSON response parsing with validation
- [ ] "Respond or not" logic with confidence thresholds
- [ ] Debouncing: don't respond to yourself, don't respond twice in a row
- [ ] Decision log (why responded, why didn't) for debugging
- [ ] User-facing modes:
  - `focus` — only respond if directly addressed
  - `pair` — chime in with help during coding
  - `idle` — casual chat, jokes, reminders
  - `silent` — listen but never speak (log-only)
- [ ] Edge case handling:
  - talking to someone else on the phone
  - background music/TV
  - thinking out loud vs asking a question
  - repeating yourself

**Success criterion:** Runs for a day, responds 5-10 times, zero false positives in `focus` mode.

### Phase 4 — Polish & Delight (2 days)

- [ ] Wake word: "Lil Pear" / "Гей Lil Pear" → bypasses decider, direct response
- [ ] Barge-in: user can interrupt TTS by speaking
- [ ] Multi-speaker hint (don't respond to other voices on video calls)
- [ ] Status indicator (menubar icon? terminal tail? Telegram notification?)
- [ ] Configurable voice/persona
- [ ] README + install instructions
- [ ] Demo video/audio

### Phase 5 — Advanced (future)

- [ ] Proactive behaviors — "you've been stuck on this for 10 min, want help?"
- [ ] Multi-device (distributed microphones around apartment)
- [ ] Integration with claudeclaw — shared session memory, cron jobs
- [ ] Emotion detection from voice tone
- [ ] Multilingual auto-switch (EN/UA/RU)

---

## 6. The Decider Prompt (draft)

This is the heart of the project. Draft v0:

```
You are the listening brain of an ambient voice assistant named Lil Pear.
You hear everything said near the user's microphone.
Your job: decide whether to respond at all.

CONTEXT:
- User: Nazar, Ukrainian, based in UTC+3
- Current time: {time}
- Mode: {mode}  # focus | pair | idle | silent
- Claude Code session: {session_state}
- Last 5 transcripts:
{recent_transcripts}

NEW TRANSCRIPT:
"{transcript}"

DECISION RULES:
1. In `silent` mode: ALWAYS respond=false, just log.
2. In `focus` mode: respond ONLY if user clearly addresses you by name
   ("Lil Pear", "Гей", direct "ти" reference) OR asks a question into silence.
3. In `pair` mode: also respond if you see the user is stuck — repeated
   swearing, "блін", "не працює", "чому", etc. — and you can help.
4. In `idle` mode: you can chime in on casual topics but ONLY if it's
   clearly directed at nobody specific and you have something useful.

NEVER RESPOND WHEN:
- User is talking to someone else (phone, video call, family)
- Background noise (TV, music, kids)
- User is thinking out loud mid-task (no question mark, trailing off)
- You already responded to a nearly-identical utterance <30s ago
- Confidence <0.7

OUTPUT (strict JSON):
{
  "respond": true|false,
  "confidence": 0.0-1.0,
  "reason": "1 sentence — why/why not",
  "reply": "your Ukrainian response if respond=true, else null"
}
```

This prompt is v0 and will evolve heavily during Phase 3.

---

## 7. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Groq Whisper Ukrainian accuracy is poor | Core STT breaks | Phase 0 validation; fallback to local faster-whisper |
| Decider hallucinates / responds to nothing | Annoying false positives | Strict JSON schema + confidence threshold + user feedback loop |
| Latency > 3s | Doesn't feel real-time | Streaming STT, async TTS, smaller model for decider |
| Claude Code doesn't allow long-running daemons | Architecture broken | Daemon runs independently; plugin is thin wrapper |
| API costs explode | Budget concern | Groq is free/cheap; Claude only called on VAD speech events; cap daily spend |
| Privacy — all speech transcribed | Sensitive data leak | All STT via Groq API (documented). SQLite log local-only. Opt-in cloud storage. |
| VAD triggers on TV/music | Wastes API calls | Silero is good at this; add energy threshold; `silent` mode pauses decider |
| TTS mispronounces technical terms | Annoying | EdgeTTS doesn't support SSML phonemes well; pre-process common terms |

---

## 8. Open Questions

1. **Daemon lifecycle** — does Claude Code plugin spec support background processes? If not, daemon is external and plugin only provides skills to talk to it.
2. **Permissions** — does macOS microphone permission need to be granted to terminal/Claude Code process, or the daemon process?
3. **Multi-session** — if two Claude Code sessions run, does the voice plugin merge them or pick one?
4. **Credentials** — share `.env` with claudeclaw, or separate?
5. **Transcript retention** — how long to keep the log? 30 days default, user-configurable?
6. **Model for decider** — Sonnet is overkill for binary decisions. Haiku would be 10x cheaper and faster. Test both.

---

## 9. Success Metrics

**Phase 1 MVP:**
- End-to-end latency <3s
- STT accuracy >90% on clear Ukrainian

**Phase 3 autonomous:**
- False positive rate <5% in `focus` mode (respond when shouldn't)
- False negative rate <20% in `pair` mode (miss when should)
- Runs 8+ hours without crash
- User judgment: "feels helpful, not annoying"

---

## 10. Timeline Estimate

| Phase | Effort |
|---|---|
| Phase 0: Research | 1 day |
| Phase 1: MVP | 2 days |
| Phase 2: Daemon | 2 days |
| Phase 3: Autonomous | 3 days |
| Phase 4: Polish | 2 days |
| **Total to v1.0** | **~10 days** of focused work |

Phase 5 (advanced) is open-ended.

---

## 11. Next Actions

1. **Confirm plan** — does this match the vision?
2. **Phase 0 kickoff** — validate Groq Whisper UA quality, EdgeTTS voices, VAD on Mac
3. **Create repo** — scaffold project structure, `.env.example`, pyproject.toml
4. **First commit** — README + PLAN.md + empty module stubs

Ready to start Phase 0 when you are 🍐
