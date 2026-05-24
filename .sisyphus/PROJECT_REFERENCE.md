# Heare — Sisyphus Project Reference

> Generated: 2026-05-23. Update when architecture changes.
> Use this file to bootstrap context in new sessions.

---

## 1. WHAT IS HEARE

A **proactive ambient voice AI assistant** powered by Claude. Listens continuously via mic (VAD-gated), decides when to speak (Silent/Focus/Ambient modes), executes actions (bash/read/write/browser/MCP). Ukrainian-first persona.

**Tagline**: Not a wake-word assistant. Not a dictation tool. A voice-first Claude agent.

---

## 2. PROJECT MAP (src/ layout)

### Entry & Config
| File | Role |
|------|------|
| `src/main.py` | CLI entry point + daemon startup (`_cmd_start` + admin subcommands) |
| `src/config.py` | `Settings` dataclass (200+ fields), `load_settings()`, file-based hot-reload |
| `src/version.py` | Version string |
| `src/__init__.py` | Package init |

### Pipeline (Pipecat frame chain)
| File | Role |
|------|------|
| `src/pipeline/build.py` | `build_pipeline()` — assembles the full pipecat frame chain |
| `src/pipeline/language_state.py` | `LanguageState` — shared language tracker (en/uk/ru) |
| `src/pipeline/bot_speech_state.py` | `BotSpeechState` — is-bot-speaking flag |
| `src/pipeline/session_state.py` | `SessionState` — active mode + provider profile |
| `src/pipeline/stages/transcription_gate.py` | ★ Critical gate: debounce, cancel-word detect, lang hysteresis, feedback-loop guard, transcript logging, TTS voice swap |
| `src/pipeline/stages/mute_gate.py` | `input_mute_gate` + `mute_gate` — flag-file-based mute |
| `src/pipeline/stages/cancel_flag_gate.py` | External cancel via `~/.heare/cancel.flag` |
| `src/pipeline/stages/voice_state_observer.py` | Writes `~/.heare/voice_state.json` |
| `src/pipeline/stages/assistant_response_logger.py` | Logs bot text to transcripts table |
| `src/pipeline/stages/tts_scrub_processor.py` | Strips tool-name narration before TTS |
| `src/pipeline/stages/usage_recorder.py` | Cost ledger from usage metrics frames |
| `src/pipeline/stages/turn_aggregator.py` | Turn aggregation (focus/ambient timeout) |
| `src/pipeline/stages/text_injector.py` | `.txt` files → `TranscriptionFrame` |
| `src/pipeline/stages/text_scrub.py` | Text scrubbing utilities |

### Agent (LLM + Tools)
| File | Role |
|------|------|
| `src/agent/llm/switchable.py` | `SwitchableLLMService` — OpenRouter ↔ z.ai hot-swap |
| `src/agent/llm/context_injector.py` | `SystemPromptInjector` — per-turn system prompt rebuild |
| `src/agent/llm/pricing.py` | Cost calculation per model |
| `src/agent/tools/registry.py` | `Tool` dataclass, `TOOLS` dict, `DEFAULT_SDK_ALLOWED_TOOLS` |
| `src/agent/tools/schemas.py` | LLM-facing schema + `register_all_tools()` |
| `src/agent/tools/direct.py` | `execute_direct()` — fast path tool dispatch |
| `src/agent/tools/dynamic.py` | User-created dynamic tool CRUD |
| `src/agent/tools/capability_index.py` | Unified index (tools + skills + MCP) |
| `src/agent/identity.py` | Auto-generated persona (name, creature, vibe, emoji) |
| `src/agent/browser_bridge.py` | WebSocket server 127.0.0.1:9333 → Chrome extension RPC |
| `src/agent/mcp_bridge.py` | `connect_mcp_servers()` — stdio MCP servers |
| `src/agent/modes.py` | Mode-specific behavior logic |

### Voice (STT → TTS)
| File | Role |
|------|------|
| `src/voice/tts/edge.py` | Edge TTS service (free, websocket) |
| `src/voice/tts/cache.py` | LRU TTS cache keyed by (text, voice) |
| `src/voice/tts/phrases.py` | `FIXED_PHRASES` for cache warmup |
| `src/voice/language/core.py` | Language mapping → TTS voice |
| `src/voice/language/detector.py` | Language detection utilities |
| `src/voice/speaker/` | ECAPA speaker recognition module |
| `src/voice/speaker/id.py` | `load_model()`, `embed()` — ECAPA embedding |
| `src/voice/speaker/gallery.py` | `SpeakerGallery` — JSON-backed enrollment store |
| `src/voice/speaker/namer.py` | LLM-driven speaker naming task |
| `src/voice/speaker/processor.py` | Pipeline stages (buffer + tagger) |
| `src/voice/indication/core.py` | `Indication` — multi-backend event router |
| `src/voice/indication/assets.py` | Audio cue assets |
| `src/voice/indication/backends/` | SoundBackend, VisualBackend, NotificationBackend |

### Store (Persistence)
| File | Role |
|------|------|
| `src/store/storage.py` | `TranscriptStore` — SQLite DAO (aiosqlite), schema v6 |
| `src/store/context.py` | `ContextBuilder` — assembles LLM context from DB |
| `src/store/conversation.py` | `ConversationManager` — topics, entities, action log |
| `src/store/user_profile.py` | User profile management |
| `src/store/memory/` | FTS5 memory store |

### Watch Dashboard
| File | Role |
|------|------|
| `src/watch/app.py` | Textual TUI entry point. Hotkey `c` copies full dashboard to clipboard |
| `src/watch/screens.py` | Screen definitions |
| `src/watch/widgets.py` | Custom Textual widgets |
| `src/watch/data.py` | Watch dashboard data layer |
| `src/watch/models.py` | Dashboard data models |
| `src/watch/dashboard.tcss` | Textual CSS |
| `src/watch/_legacy.py` | Legacy compatibility |

### Other
| File | Role |
|------|------|
| `src/overlay/` | Pywebview frameless overlay UI |
| `src/audio_event/observer.py` | YAMNet non-speech classifier |
| `src/daemon/onboarding.py` | Setup flow steps |
| `src/daemon/workspace.py` | MCP seeding from ~/.claude.json |
| `src/daemon/heartbeat.py` | Periodic TTS keep-alive |
| `src/daemon/claude_capabilities.py` | Capability refresh |
| `src/daemon/browser.py` | Browser debugger integration |
| `src/daemon/control.py` | Daemon control helpers |
| `src/daemon/watch_controls.py` | Watch dashboard daemon control |
| `src/skills/agent_skills.py` | Agent skills loader |
| `src/skills/discovery.py` | Capability discovery |
| `src/skills/installer.py` | Skill/MCP installer |
| `src/skills/marketplace.py` | Remote marketplace client |
| `src/skills/mcp_utils.py` | MCP utility functions |

### External
| Path | Role |
|------|------|
| `extensions/heare-bridge/` | Chrome extension (MV3) — offscreen document owns WebSocket |
| `tests/` | 86 test files |
| `migrations/` | Raw SQL migrations (01 + 02 done) |
| `prompts/` | System prompt templates (decider.txt, persona.txt, identity-bootstrap.txt) |
| `scripts/` | Benchmarks, model fetch utilities |
| `docs/` | Architecture docs |

---

## 3. PIPELINE STAGE ORDER (downstream)

```
Mic → transport.input
    → input_mute_gate
    → cancel_flag_gate
    → [audio_event_observer (YAMNet)]
    → [speaker_buffer]
    → stt (GroqSTTService)
    → stt_error_observer
    → [speaker_tagger (ECAPA)]
    → voice_state_observer
    → ★ transcription_gate    ← CRITICAL CHOKEPOINT
    → system_prompt_injector  → DB-heavy context rebuild
    → user_aggregator
    → ★ llm_service (SwitchableLLMService)
    → assistant_response_logger
    → tts_scrub
    → tts (EdgeTTSService + cache)
    → usage_recorder
    → tts_fade_observer
    → [sound_cue_processor]
    → mute_gate
    → transport.output → speaker
    → assistant_aggregator
```

### Frame Types
- **DataFrame**: TranscriptionFrame, LLMTextFrame, TTSAudioRawFrame → per-stage queue
- **SystemFrame**: CancelFrame, InterruptionFrame, ErrorFrame → IMMEDIATE via all stages
- **ControlFrame**: BotStartedSpeaking, MetricsFrame → higher-priority queue

---

## 4. KEY DATA FLOWS

### 4a. Transcription Gate (PH2-01) — per TranscriptionFrame:
1. Debounce-coalesce (0.6s window via `transcript_debounce_seconds`)
2. Drop if bot_speaking | indication_speaking | enrollment_active | cooldown
3. Detect language → 2-turn hysteresis → update `LanguageState`
4. Call `tts.set_voice(language)` to swap TTS voice
5. Log to DB: `store.log_transcript(text, mode, speaker_id, ...)`
6. Check `is_standalone_cancel_imperative()` → push `InterruptionFrame` UPSTREAM
7. Forward `TranscriptionFrame` downstream

### 4b. System Prompt Injector — per user turn:
1. Read `LanguageState.language`
2. Get/create active conversation ID via `ConversationManager`
3. Build context via `ContextBuilder.build_for_generator()` (DB-heavy)
4. Query `CapabilityIndex.query(transcript, top_k=5)`
5. `render_native_system_prompt()` → assemble persona + conversation + action_log + MCP + capabilities
6. `_replace_system_message(llm_context, new_prompt)`
7. Forward `TranscriptionFrame`

### 4c. LLM Service — hot-swappable:
- Composition: `SwitchableLLMService` wraps `OpenAILLMService` + `AnthropicLLMService`
- Provider read from `~/.heare/provider`, mtime-gated at turn-start frames
- Sticky-turn lock: provider change deferred until turn complete
- Tool registration fans out to both delegates
- z.ai failure → permanent process-level fallback to OpenRouter

### 4d. Cancel Flow:
1. User speaks "stop" / "відміни" / "отмени" / "стоп" / "cancel" / "halt"
2. `TranscriptionGate.is_standalone_cancel_imperative()` → True
3. Push `InterruptionFrame` upstream
4. `_TtsFadeOnInterruption` → `tts.cancel_pending()` (50ms fade)
5. Pipecat native `cancel_on_interruption` → cancels in-flight `register_function`
6. ⚠ Known defect: cancel-keyword check runs AFTER bot_speaking drop filter, so cancel words during bot speech are silently swallowed

---

## 5. DATABASE SCHEMA (version 6)

Tables: `meta`, `transcripts`, `displays`, `decisions`, `actions`, `heartbeats`, `events`, `usage_events`, `conversations`, `action_log`, `conversation_messages`, `dynamic_tools`

Key fields:
- `transcripts`: id, ts, text, mode, speaker_id, speaker_confidence, audio_event_label/score, agent_mode, agent_spoken
- `decisions`: id, ts, type, confidence, reason, reply, intent, action_json
- `actions`: id, ts, tool, args, status, result_summary, intent_id, decision_id
- `usage_events`: id, ts, provider, kind, model, tokens_in/out, cost
- `dynamic_tools`: name, sdk_name, description, definition_json, enabled
- `conversations`: id, created, updated, summary, topics_json, entities_json
- `action_log`: conversation_id, tool, status, query, result_summary

---

## 6. KEY CONFIGURATION (src/config.py:Settings)

| Setting | Default | Notes |
|---------|---------|-------|
| `mode` | `ambient` | Hot-reloadable via `~/.heare/mode` |
| `wake_word` | `гава` | Also accepts "heare", "гей" via pattern |
| `groq_language` | `uk` | STT hint; Groq may override from audio |
| `tts_voice` | `en-US-AriaNeural` | Swapped per detected language |
| `transcript_debounce_seconds` | `0.6` | Coalesce STT frames |
| `barge_in_enabled` | `true` | Open-mic interruption |
| `conversation_memory_enabled` | `false` | Phase 2 opt-in |
| `speaker_id_enabled` | `true` | ECAPA recognition |
| `llm_provider` | `openrouter` | Hot-swap via `~/.heare/provider` |
| `provider_file` | `~/.heare/provider` | Content: "openrouter" or "zai" |
| `openrouter_model` | `google/gemini-3.1-flash-lite-preview-20260303` | |
| `confirmation_passphrase` | `null` | Verbal confirmation shortcut |
| `cancel_stop_words` | `[stop, cancel, halt, відміни, отмени, стоп]` | Env override via `HEARE_CANCEL_STOP_WORDS` |

---

## 7. STATE LAYOUT (~/.heare/)

```
~/.heare/
├── heare.db                    # SQLite WAL (transcripts, decisions, tools, usage)
├── heare.pid                   # Single-instance lock
├── daemon.log                  # Rotating log (10MB, 3 backups)
├── config.toml                 # User settings
├── mode                        # Hot-reloadable mode
├── provider                    # Hot-reloadable provider
├── session.json                # Claude Code session ID
├── identity.json               # Persona: name, creature, vibe, emoji
├── voice_state.json            # Current VAD state (idle/listening/stt/result)
├── audio_event.json            # YAMNet detections
├── speakers.json               # ECAPA embeddings + labels
├── mute.flag / mute_input.flag / cancel.flag
├── inject/                     # .txt → TranscriptionFrame
├── logs/daemon.log + indication.jsonl
├── models/yamnet.onnx          # Audio event model
└── workspace/.mcp.json         # MCP server configs
```

---

## 8. TOOL REGISTRY (src/agent/tools/registry.py)

**Execution types**: `direct` (fast path) | `claude` (needs reasoning) | `workflow` | `mcp`

Direct tools: bash, read, write, edit, web_search, web_fetch, re_enroll, list_profiles, create_profile, delete_profile, rename_profile, create_tool, update_tool, delete_tool, list_tools, create_archive, extract_archive, batch_operation, set_provider, stop_daemon, restart_daemon, list_skills, run_skill, create_skill, list_capabilities, discover_capability, install_skill_tool, install_mcp_server_tool, revoke_capability, set_mode, set_proactivity

Browser tools (via bridge): list_browser_tabs, read_browser_page, click_in_browser, fill_in_browser, extract_in_browser, navigate_browser, open_browser_tab, activate_browser_tab

All actions require verbal confirmation (yes/no or passphrase). Confirmation via passphrase is additive — existing yes/no + speaker-id flow still works.

---

## 9. KNOWN RISKS & ISSUES

1. ~~**Cancel during bot speech**~~ **[FIXED 2026-05-25]**: Cancel detection moved before bot_active guard. Cancel words now interrupt regardless of barge-in mode or bot speaking state.
2. **System prompt injector is synchronous**: DB-heavy `context_builder.build_for_generator()` runs before user_aggregator, stalling every turn.
3. **z.ai disable is one-way**: A z.ai auth error permanently disables it for the process lifetime — no recovery without restart.
4. **~~Cancel words during bot speech silently swallowed~~ FIXED**: Cancel detection now runs before the bot_active guard in `transcription_gate._handle_transcription()`. "stop", "стоп", "відміни", etc. interrupt the bot regardless of barge-in mode or speaking state.
4. **No tool timeout enforcement at SwitchableLLMService layer**. Timeout only applies to OpenRouter HTTP requests.
5. **Edge TTS has no fallback**. If it fails, the reply is silent.
6. **No speech-to-text coverage for speaker-recognition enrollment audio** — the `enroll-owner` flow records raw PCM but STT never transcribes it.
7. **Single LLMContext shared across pipeline lifetime** — never recreated, only mutated.

---

## 10. COMPLETED PHASES (per .omc/ PRDs)

All major phases marked complete: Phase A, Phase B, Phase 2.2, Browser bridge stability, CCS, Indication, Latency, Speaker Phase 1+2, YAMNet, Capability discovery, Topic extraction, Owner auto-enroll, BCDE, Enrollment gate, VFA, WS, etc.

Active/in-progress plans: `plan-dynamic-mcp-search.md`, `plan-overlay-ui.md`

---

## 11. DEVELOPER COMMANDS

```bash
uv run python -m src.main start          # Foreground daemon
uv run python -m src.main watch          # Dashboard (separate terminal)
uv run python -m src.main stop           # Graceful stop
uv run python -m src.main status         # Check running
uv run python -m src.main mode silent    # Hot-reload mode
uv run python -m src.main provider zai   # Hot-reload LLM provider
uv run python -m src.main setup          # Run onboarding
uv run python -m src.main logs -f        # Tail daemon log
uv run pytest tests/ -v                  # Run tests (86 test files)
uv run ruff check src/ tests/            # Lint
uv run ruff format src/ tests/           # Format
```

---

## 12. EXTERNAL DEPENDENCIES

| Service | Purpose | Key |
|---------|---------|-----|
| Groq Whisper | STT | `GROQ_API_KEY` (free tier works) |
| OpenRouter | LLM (default) | `OPENROUTER_API_KEY` |
| z.ai | LLM (alternative) | `ZAI_API_KEY` |
| Edge TTS | TTS | Free, no key |
| Serper.dev | Web search (opt) | `SERPER_API_KEY` |
| DuckDuckGo | Web search (fallback) | Free, no key |

---

*End of reference. Reload this file at start of each session for full context.*
