# Heare System Architecture - Visual Overview

## Component Relationship Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                              HEARE SYSTEM ARCHITECTURE                              │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ EXTERNAL INTERFACES                                                                  │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│   Microphone     │  │  Watch Dashboard │  │   CLI Control    │  │  Systemd Service │
│   (Audio Input)  │  │   (Textual TUI)  │  │   (hearectl)     │  │   (heare.service)│
└────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
         │                     │                     │                     │
         │ Audio               │ Control             │ Commands            │ Signals
         │                     │                     │                     │
         ▼                     ▼                     ▼                     ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                           MAIN ENTRY POINT (src/main.py)                              │
│                                                                                      │
│  • _cmd_start()   - Initialize pipeline, start daemon                                │
│  • _cmd_stop()    - Graceful shutdown via SIGTERM                                     │
│  • _cmd_mode()    - Hot-reload behavior mode                                         │
│  • _cmd_provider()- Hot-reload LLM provider                                          │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │ Initialize
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                        AUDIO PROCESSING PIPELINE (src/pipeline.py)                   │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 1: INPUT PROCESSING                                                            │
└──────────────────────────────────────────────────────────────────────────────────────┘

  Microphone
      │
      ▼
┌─────────────────┐
│ LocalAudioTransport │
│ • SileroVAD        │ ← Voice Activity Detection (stop: 0.5s, start: 0.3s)
│ • SmartTurnV3      │ ← Turn-taking logic (stop: 1.0s)
│ • 16kHz in/24kHz out│
└────────┬─────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 2: OPTIONAL INPUT FILTERS                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘

         │
         ├──► [Input Mute Gate] ◄──── ~/.heare/mute_input (toggle from dashboard)
         │      • Drops audio when muted
         │
         ├──► [Speaker Buffer] ◄──── Optional (if speaker_id_enabled)
         │      • Buffers for speaker recognition
         │
         ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 3: SPEECH-TO-TEXT                                                              │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐
│  GroqSTTService  │
│ • Whisper-large │ ← Groq API (fast STT)
│ • Language det  │ ← Auto/UK/EN/RU
│ • Prob metrics  │ ← Confidence scores
└────────┬─────────┘
         │ TranscriptionFrame
         ▼
┌─────────────────┐
│ STT Error Observer│
│ • Catches errors│ → Indication system
└────────┬─────────┘
         │
         ├──► [Speaker Tagger] ◄──── Optional (if speaker_id_enabled)
         │      • ECAPA embedding
         │      • Speaker recognition
         │
         ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 4: TRANSCRIPTION GATE (src/transcription_gate.py)                             │
│ ┌────────────────────────────────────────────────────────────────────────────────┐   │
│ │  TranscriptionGateProcessor                                                    │   │
│ │  ┌─────────────────────────────────────────────────────────────────────────┐  │   │
│ │  │ Decision Filters:                                                       │  │   │
│ │  │  ✓ Feedback-loop guard (drop while bot speaking)                        │  │   │
│ │  │  ✓ STT debounce (0.3s window)                                           │  │   │
│ │  │  ✓ Language hysteresis (2-turn confirmation)                            │  │   │
│ │  │  ✓ Cancel keyword detection ("stop", "відміни", "стоп", etc.)           │  │   │
│ │  │  ✓ Transcript logging to SQLite                                         │  │   │
│ │  │  ✓ TTS voice swap on language change                                    │  │   │
│ │  └─────────────────────────────────────────────────────────────────────────┘  │   │
│ │                                                                                │   │
│ │  Language State Management:                                                    │   │
│ │    ┌─────────────────┐    ┌─────────────────┐                                 │   │
│ │    │ Current Language│◄──►│ Language Change │                                 │   │
│ │    │ (uk/en/ru)      │    │ Listener         │                                 │   │
│ │    └─────────────────┘    └─────────────────┘                                 │   │
│ │           │                                                                  │   │
│ │           ├────►►► Updates system prompt when changes                        │   │
│ │           ├────►►► Swaps TTS voice                                          │   │
│ │           └────►►► Notifies LLM service                                     │   │
│ └────────────────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────┬───────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 5: CONTEXT BUILDING (src/context.py, src/llm_context_injector.py)            │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│  System Prompt Injector                                                            │
│  ┌──────────────────────────────────────────────────────────────────────────────┐  │
│  │ Builds System Message:                                                      │  │
│  │  • Persona (name, creature, vibe, emoji, tagline)                            │  │
│  │  • Language (current language from LanguageState)                            │  │
│  │  • Mode (silent/focus/ambient)                                               │  │
│  │  • Conversation summary (ConversationManager)                                │  │
│  │  • Active topics & entities                                                 │  │
│  │  • Recent action log (last 16 actions)                                       │  │
│  │  • Recent transcripts (last 20, configurable)                                │  │
│  │  • MCP server descriptions                                                  │  │
│  │  • Top-K relevant capabilities (CapabilityIndex)                             │  │
│  └──────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬───────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  User Aggregator (LLMContextAggregatorPair.user)                                  │
│  • Appends user transcription to LLM context                                        │
└──────────────────────────────┬───────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 6: LLM PROCESSING (src/switchable_llm.py)                                    │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│  SwitchableLLMService                                                             │
│  ┌──────────────────────────────────────────────────────────────────────────────┐  │
│  │ Provider Switching:                                                         │  │
│  │  ~/.heare/provider ─────► "openrouter" or "zai"                              │  │
│  │        │                                                                      │  │
│  │        ├──► OpenRouter (OpenAI-compatible)                                   │  │
│  │        │    └──► Model: google/gemini-2.0-flash-exp                          │  │
│  │        │                                                                      │  │
│  │        └──► z.ai (Anthropic-compatible)                                      │  │
│  │             └──► Model: claude-sonnet-4-20250514                             │  │
│  │                                                                             │  │
│  │  Features:                                                                  │  │
│  │  • Hot-swappable at runtime (sticky-turn gate)                              │  │
│  │  • Lazy provider reload (mtime-based)                                       │  │
│  │  • Tool registration (fans out to both providers)                           │  │
│  │  • Fallback on z.ai errors → OpenRouter                                     │  │
│  └──────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                      │
│  ┌──────────────────────────────────────────────────────────────────────────────┐  │
│  │ Tool System (src/tool_registry.py, src/llm_tools.py, src/direct_tools.py)   │  │
│  │                                                                              │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐   │  │
│  │  │ Direct Tools (fast path, no Claude):                                 │   │  │
│  │  │  • bash   - Execute shell commands                                   │   │  │
│  │  │  • read   - Read file contents                                       │   │  │
│  │  │  │ write  - Write content to file                                   │   │  │
│  │  │  • web_fetch  - Fetch URL via HTTP                                   │   │  │
│  │  │  • web_search - Search via WebSearch API                            │   │  │
│  │  │  • re_enroll   - Re-enroll speaker profile                          │   │  │
│  │  │  • list_profiles - List speaker profiles                            │   │  │
│  │  └──────────────────────────────────────────────────────────────────────┘   │  │
│  │                                                                              │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐   │  │
│  │  │ Claude Tools (needs reasoning):                                      │   │  │
│  │  │  • edit   - Edit files (complex)                                     │   │  │
│  │  │  • MCP tools - Via Model Context Protocol                            │   │  │
│  │  └──────────────────────────────────────────────────────────────────────┘   │  │
│  │                                                                              │  │
│  │  Tool Execution Flow:                                                       │  │
│  │    LLM emits tool call → register_function handler → execute_direct()       │  │
│  │           │                                                                  │  │
│  │           └──► Returns: {success, output, error, exit_code, spoken}          │  │
│  └──────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬───────────────────────────────────────────────────────┘
                               │ LLMFullResponseEndFrame
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 7: OUTPUT PROCESSING                                                          │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Assistant Response Logger (BOTLOG-02)                                             │
│  • Captures LLM text before TTS                                                    │
│  • Logs to transcripts table                                                       │
└──────────────────────────────┬───────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  TTS Scrub Processor                                                               │
│  • Strips tool-name narration ("list_tools", "bash:", etc.)                        │
│  • Prevents tool names from being spoken                                           │
└──────────────────────────────┬───────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  EdgeTTSService (src/tts_edge.py)                                                  │
│  • Microsoft Edge TTS (free, no API key)                                           │
│  • Ukrainian voices: Polina, Ostap                                                 │
│  • English voices: Jenny, Guy                                                       │
│  • Russian voices: Svetlana, Dmitry                                                 │
│  • Caching via TTSCache                                                             │
│  • Emits TTSAudioRawFrame                                                           │
└──────────────────────────────┬───────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  TTS Fade Observer (PH2-05)                                                         │
│  • On InterruptionFrame → 50ms fade-out                                             │
│  • Clean audio stop on cancel                                                       │
└──────────────────────────────┬───────────────────────────────────────────────────────┘
                               │
                               ├──► [Sound Cue Processor] ◄──── Optional (indication)
                               │      • Plays audio cues
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Mute Gate                                                                          │
│  • Drops audio when ~/.heare/mute exists                                            │
│  • Toggled from watch dashboard                                                     │
└──────────────────────────────┬───────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Transport Output → Speaker                                                         │
└──────────────────────────────┬───────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Assistant Aggregator (LLMContextAggregatorPair.assistant)                         │
│  • Appends response to context for next turn                                        │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ SUPPORTING SYSTEMS                                                                  │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ STORAGE LAYER (src/storage.py)                                                      │
│ ┌────────────────────────────────────────────────────────────────────────────────┐   │
│ │ SQLite Database (~/.heare/heare.db)                                          │   │
│ │                                                                                │   │
│ │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │   │
│ │  │ transcripts  │  │  decisions   │  │   actions    │  │ heartbeats   │       │   │
│ │  │              │  │              │  │              │  │              │       │   │
│ │  │ • text       │  │ • type       │  │ • tool       │  │ • timestamp  │       │   │
│ │  │ • speaker_id │  │ • confidence │  │ • args       │  │ • reply      │       │   │
│ │  │ • language   │  │ • intent     │  │ • result     │  │              │       │   │
│ │  │ • timestamp  │  │ • reply      │  │ • status     │  │              │       │   │
│ │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘       │   │
│ │                                                                                │   │
│ │  ┌──────────────┐                                                              │   │
│ │  │    events    │ ← Fine-grained event log                                     │   │
│ │  │              │   (decider.start, action.executing, etc.)                    │   │
│ │  └──────────────┘                                                              │   │
│ └────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                      │
│  • Async operations (aiosqlite) - never blocks audio pipeline                        │
│  • Indexed timestamps for fast recent queries                                        │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ CONVERSATION MANAGER (src/conversation.py)                                          │
│ ┌────────────────────────────────────────────────────────────────────────────────┐   │
│ │ In-Memory State:                                                               │   │
│ │  • Action log (deque, maxlen=16) - Recent tool executions                      │   │
│ │  • Conversation summary - LLM-generated summary                                │   │
│ │  • Active topics - Currently discussed topics                                   │   │
│ │  • Entities - Recognized entities (people, places, etc.)                        │   │
│ │  • Recent turns - Last N user/bot exchanges                                     │   │
│ └────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                      │
│  • Write-through projection: Actions → SQLite UPSERTs (async, fire-and-forget)       │
│  • Loaded by context builder for each LLM turn                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ INDICATION SYSTEM (src/indication.py)                                               │
│ ┌────────────────────────────────────────────────────────────────────────────────┐   │
│ │ Indication Types:                                                              │   │
│ │  • attention     - Bot wants attention                                         │   │
│ │  • error         - Error occurred                                              │   │
│ │  • long_running  - Long-running action                                         │   │
│ │  • success       - Action completed                                            │   │
│ │  • info          - Informational                                              │   │
│ │  • input_waiting - Waiting for user input (confirmation)                       │   │
│ │  • countdown     - Countdown timer active                                      │   │
│ └────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                      │
│ ┌────────────────────────────────────────────────────────────────────────────────┐   │
│ │ Backends:                                                                      │   │
│ │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐                      │   │
│ │  │   Sound      │  │   Visual     │  │  Notification    │                      │   │
│ │  │              │  │              │  │   Center         │                      │   │
│ │  │ Audio cues   │  │ JSONL log    │  │  macOS notify   │                      │   │
│ │  │ from assets/ │  │ indication.  │  │                  │                      │   │
│ │  │              │  │ jsonl        │  │                  │                      │   │
│ │  └──────────────┘  └──────────────┘  └──────────────────┘                      │   │
│ └────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                      │
│  • Cooldown system (1.5s default) prevents spam                                     │
│  • Quiet hours support (22:00-07:00 default)                                        │
│  • Per-type toggles (sound/visual/notification)                                     │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ IDENTITY SYSTEM (src/identity.py)                                                    │
│ ┌────────────────────────────────────────────────────────────────────────────────┐   │
│ │ Persona Components:                                                            │   │
│ │  • name      - Chosen name (Alex, Mara, Zorya, etc.)                          │   │
│ │  • creature  - Creature type (fox, owl, star, etc.)                           │   │
│ │  • vibe      - Personality vibe (playful, wise, energetic)                    │   │
│ │  • emoji     - Visual representation (🦊 🦉 ⭐)                               │   │
│ │  • tagline   - Short tagline                                                   │   │
│ └────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                      │
│  • Auto-generated on first run via OpenRouter API                                   │
│  • Stored in ~/.heare/identity.json                                                  │
│  • Reset via `heare reset-identity`                                                 │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ WATCH DASHBOARD (src/watch/)                                                        │
│ ┌────────────────────────────────────────────────────────────────────────────────┐   │
│ │ UI Components (Textual TUI):                                                   │   │
│ │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │   │
│ │  │ HeaderBar    │  │ActivityTable │  │  LogTail     │  │ControlsBar   │       │   │
│ │  │              │  │              │  │              │  │              │       │   │
│ │  │ Status, mode │  │ Recent events│  │ Live logs    │  │ Mute buttons │       │   │
│ │  │ provider,    │  │ transcripts, │  │ last 40 lines│  │ daemon ctrl  │       │   │
│ │  │ model, lang  │  │ decisions,   │  │              │  │ text inject  │       │   │
│ │  │              │  │ actions      │  │              │  │              │       │   │
│ │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘       │   │
│ │                                                                                │   │
│ │  ┌──────────────┐                                                              │   │
│ │  │    AIBar     │ ← Provider toggle, model selector                            │   │
│ │  └──────────────┘                                                              │   │
│ └────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                      │
│  Hotkeys: s=start, x=stop, r=restart, m=mute, M=mute_mic, t=text_inject, p=provider │
│  Data source: SQLite + daemon log                                                   │
│  Refresh interval: 0.5s (configurable)                                               │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ CONFIGURATION & STATE                                                               │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ Configuration Files:                                                                │
│                                                                                      │
│  ~/.heare/config.toml           - Main settings (API keys, voices, timeouts)       │
│  ~/.heare/workspace/.mcp.json   - MCP server definitions                            │
│  ~/.heare/heare.env             - Environment for systemd                           │
│  .env                           - Environment for dev                               │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ Runtime State Files:                                                                │
│                                                                                      │
│  ~/.heare/mode                  - Current mode (silent/focus/ambient)              │
│  ~/.heare/provider              - Current LLM provider (openrouter/zai)            │
│  ~/.heare/mute                  - Bot mute flag (exists=muted)                     │
│  ~/.heare/mute_input            - Mic mute flag (exists=muted)                     │
│  ~/.heare/heare.pid             - Running daemon PID                                │
│  ~/.heare/session.json          - Persistent Claude Code session ID                 │
│  ~/.heare/identity.json         - Auto-generated persona                            │
│  ~/.heare/speakers.json         - Speaker recognition profiles                      │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL FLOWS                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ 1. Hot-Reload Operations (No daemon restart):                                      │
│    heare mode <silent|focus|ambient>  → Writes ~/.heare/mode                        │
│    heare provider <openrouter|zai>   → Writes ~/.heare/provider                    │
│    Watch dashboard M/M buttons     → Create/remove ~/.heare/mute* files            │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ 2. Graceful Shutdown:                                                               │
│    SIGTERM/SIGINT → run_until_stopped() → Pipeline cancellation → Cleanup → Exit   │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ 3. Interruption Flow:                                                               │
│    Cancel keyword → TranscriptionGate pushes InterruptionFrame →                   │
│      TTS fade-out (50ms) + Cancel in-flight tool calls → Bot stops                 │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ KEY RELATIONSHIPS                                                                   │
└──────────────────────────────────────────────────────────────────────────────────────┘

LanguageState ←→ TranscriptionGate (language detection)
     │
     ├──►►► SystemPromptInjector (update system prompt)
     ├──►►► EdgeTTSService (swap voice)
     └──►►► SwitchableLLMService (notify provider)

ConversationManager ←→ ContextBuilder (conversation state)
     │
     ├─── Tool calls → record_action_pending()
     └─── Tool results → record_action_result()

CapabilityIndex ←→ SystemPromptInjector (relevant capabilities)
     │
     ├──► Skills (agent_skills.py)
     ├──► MCP servers (mcp_utils.py)
     └──► Direct tools (tool_registry.py)

Indication ←→ All components (event notifications)
     │
     ├──► STT errors
     ├──► Action events
     ├──► Bot speaking status
     └────► System events (startup, shutdown)

Watch Dashboard ←→ SQLite + Daemon Log
     │
     ├──► Polls transcripts/decisions/actions tables
     └───► Tails daemon.log

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ DATA FLOW SUMMARY                                                                   │
└──────────────────────────────────────────────────────────────────────────────────────┘

INPUT:  Mic → VAD → STT → TranscriptionGate → ContextBuilder → LLM
OUTPUT: LLM → TTS Scrub → TTS → MuteGate → Speaker
STATE:   All events → SQLite ← Watch Dashboard
CONTROL: CLI/Dashboard → Runtime state files → Pipeline behavior

┌──────────────────────────────────────────────────────────────────────────────────────┐
│ END OF ARCHITECTURE DIAGRAM                                                         │
└──────────────────────────────────────────────────────────────────────────────────────┘
