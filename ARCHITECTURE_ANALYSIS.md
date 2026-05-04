# Heare Architecture Analysis & Data Flow

## System Overview

**Heare** is a proactive, ambient, agentic voice AI assistant powered by Claude Code. It lives in your headphones, listens continuously, and decides autonomously when to speak or act.

### Core Characteristics
- **Continuous Listening** via microphone with Silero VAD
- **Autonomous Decision Making** about when to respond
- **Ukrainian Voice Output** via Edge TTS
- **Action Capabilities** through Claude Code tools (Read/Write/Edit/Bash)
- **Persistent Memory** across sessions
- **Self-Generated Persona** on first run

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           USER AUDIO INPUT                              │
│                              (Microphone)                               │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      AUDIO TRANSPORT LAYER                              │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  LocalAudioTransport                                             │  │
│  │  • SileroVADAnalyzer (Voice Activity Detection)                  │  │
│  │  • LocalSmartTurnAnalyzerV3 (Turn-taking logic)                  │  │
│  │  • 16kHz input / 24kHz output sample rates                       │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    INPUT PROCESSING STAGE                               │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Input Mute Gate                                                  │  │
│  │  • Drops audio when ~/.heare/mute_input exists                   │  │
│  │  • Toggled from watch dashboard                                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Speaker Buffer (Optional)                                       │  │
│  │  • Buffers audio for speaker identification                      │  │
│  │  • Enabled when speaker_id_enabled=True                         │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  GroqSTTService (Speech-to-Text)                                 │  │
│  │  • Whisper-large-v3-turbo via Groq API                           │  │
│  │  • Language detection (auto/uk/en/ru)                           │  │
│  │  • Emits TranscriptionFrame                                      │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  STT Error Observer                                              │  │
│  │  • Catches ErrorFrame → indication notification                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Speaker Tagger (Optional)                                       │  │
│  │  • Tags transcriptions with speaker_id                           │  │
│  │  • Uses ECAPA embeddings for recognition                        │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                 TRANSCRIPTION GATE (PH2-01)                            │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  TranscriptionGateProcessor                                      │  │
│  │  • Feedback-loop guard (drop while bot speaking)                 │  │
│  │  • STT debounce (coalesce events within 0.3s)                   │  │
│  │  • Language hysteresis (2-turn confirmation)                     │  │
│  │  • Cancel keyword detection ("stop", "відміни", etc.)            │  │
│  │  • Transcript logging to SQLite                                  │  │
│  │  • TTS voice swap on language change                             │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    CONTEXT INJECTION STAGE                             │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  System Prompt Injector (PH2-07)                                 │  │
│  │  • Rebuilds system message per turn                              │  │
│  │  • Includes: persona, conversation history, action log,          │  │
│  │    MCP server descriptions, top-K relevant capabilities          │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  User Aggregator (LLMContextAggregatorPair.user)                │  │
│  │  • Appends user transcription to LLM context                    │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       LLM PROCESSING STAGE                              │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  SwitchableLLMService                                           │  │
│  │  • Hot-swaps between OpenRouter & z.ai                           │  │
│  │  • Active provider read from ~/.heare/provider                  │  │
│  │  • Sticky-turn gate (locks provider for current turn)            │  │
│  │  • Tool registration via register_function                       │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Tool Registry & Execution                                      │  │
│  │  Direct Tools (fast path):                                      │  │
│  │  • bash - Execute shell commands                                │  │
│  │  • read - Read file contents                                    │  │
│  │  • write - Write content to file                                │  │
│  │  • web_fetch - Fetch URL via HTTP                               │  │
│  │  • web_search - Search via WebSearch API                        │  │
│  │  • re_enroll - Re-enroll speaker profile                        │  │
│  │  • list_profiles - List speaker profiles                        │  │
│  │                                                                   │  │
│  │  Claude Tools (reasoning path):                                 │  │
│  │  • edit - Edit files (needs Claude reasoning)                    │  │
│  │  • MCP tools - Via Model Context Protocol servers                │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    OUTPUT PROCESSING STAGE                             │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Assistant Response Logger (BOTLOG-02)                           │  │
│  │  • Captures LLM text upstream of TTS                            │  │
│  │  • Logs bot responses to transcripts table                      │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  TTS Scrub Processor                                            │  │
│  │  • Strips tool-name narration before TTS                        │  │
│  │  • Prevents "list_tools" etc. from being spoken                 │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  EdgeTTSService (Text-to-Speech)                                │  │
│  │  • Microsoft Edge TTS (free, no API key needed)                 │  │
│  │  • Ukrainian voices by default                                  │  │
│  │  • Caching via TTSCache                                         │  │
│  │  • Emits TTSAudioRawFrame                                       │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  TTS Fade Observer (PH2-05)                                     │  │
│  │  • On InterruptionFrame → 50ms fade-out                         │  │
│  │  • Clean audio stop on cancel                                   │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Sound Cue Processor (Optional)                                 │  │
│  │  • Plays audio cues for indication events                       │  │
│  │  • Attention chimes, error sounds, etc.                         │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Mute Gate                                                       │  │
│  │  • Drops TTSAudioRawFrame when ~/.heare/mute exists             │  │
│  │  • Toggled from watch dashboard                                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           AUDIO OUTPUT                                 │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Transport Output → Speaker                                      │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      ASSISTANT AGGREGATOR                              │
│  • Appends assistant response to LLM context for next turn            │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Component Deep Dive

### 1. Audio Transport Layer

**Purpose**: Manages audio I/O with intelligent voice activity detection and turn-taking.

**Key Components**:
- **SileroVADAnalyzer**: Detects when user starts/stops speaking
  - `stop_secs=0.5` - silence before turn end
  - `start_secs=0.3` - speech before turn start
  - `confidence=0.7` - VAD threshold
  - `min_volume=0.6` - minimum audio level

- **LocalSmartTurnAnalyzerV3**: Determines when to yield turn back to user
  - `stop_secs=1.0` - silence before bot can respond

### 2. Transcription Gate (PH2-01)

**Purpose**: Pre-LLM orchestration layer that makes all non-LLM decisions.

**Responsibilities**:
- **Feedback-loop guard**: Drops transcripts while bot is speaking
- **STT debounce**: Coalesces TranscriptionFrame events within 0.3s window
- **Language hysteresis**: Requires 2-turn confirmation before swapping languages
- **Cancel keyword detection**: Fast-path for "stop", "відміни", "отмени", "стоп", "cancel", "halt"
- **Transcript logging**: Persists to SQLite for watch dashboard
- **TTS voice swap**: Changes voice when language changes (uk ↔ en ↔ ru)

**Language State Management**:
```
LanguageState (shared state)
  ├── language: str ("uk" | "en" | "ru")
  ├── change_listener: Callable
  └── set_change_listener()
```

### 3. Context Builder

**Purpose**: Builds rich context for each LLM turn from multiple sources.

**Context Sources**:
1. **Persona**: Auto-generated identity (name, creature, vibe, emoji)
2. **Recent transcripts**: Last N transcripts (configurable, default 20)
3. **Conversation memory**: Summary, active topics, entities, recent turns
4. **Action log**: Recent tool executions (ConversationManager._action_log, maxlen=16)
5. **MCP server descriptions**: Available MCP servers and their tools
6. **Capabilities index**: Top-K relevant capabilities (skills + MCP + tools)
7. **Speaker gallery**: Recognized speakers and their labels
8. **Current mode**: silent/focus/ambient
9. **Time & timezone**: For temporal reasoning

**System Prompt Structure**:
```
You are {name} the {creature} ({vibe}) {emoji}

{tagline}

## Language
You MUST speak in {language}.

## Mode
Current mode: {mode}
- silent: Never speak, never act
- focus: Speak only when addressed
- ambient: Also speak on stuck-user heuristics

## Conversation Summary
{conversation_summary}

## Active Topics
{topics}

## Recent Turns
{recent_turns}

## Recent Action Log
{action_log}

## Available Capabilities
{capabilities}

## MCP Servers
{mcp_descriptions}
```

### 4. Switchable LLM Service

**Purpose**: Hot-swappable LLM backend between OpenRouter and z.ai.

**Architecture**: Composition over inheritance
- Holds two fully-formed Pipecat delegates
- Wrapper relays frames through active delegate
- Provider file (`~/.heare/provider`) is source of truth
- Lazy re-read on mtime change (only at turn-start frames)
- Sticky-turn gate locks provider for current turn

**Failure Mode**:
- z.ai auth/5xx errors → fallback to OpenRouter
- Single ERROR log per 60-second window (log spam prevention)

**Tool Registration**:
- `register_function`/`unregister_function` fan out to BOTH delegates
- Active provider always has handlers regardless of boot state

### 5. Tool System

**Purpose**: Execute actions on behalf of the user.

**Tool Registry** (`src/tool_registry.py`):
```python
@dataclass(frozen=True)
class Tool:
    name: str              # lowercase: "bash", "read"
    sdk_name: str          # CamelCase for SDK: "Bash", "Read"
    execution: Literal["direct", "claude", "workflow", "mcp"]
    description: str       # what it does
    enabled: bool          # can be toggled
```

**Execution Types**:
1. **direct**: Fast path, no Claude reasoning needed
   - bash, read, write, web_fetch, web_search
   - Executed by `execute_direct()` in same process

2. **claude**: Needs Claude reasoning layer
   - edit (complex file editing)
   - MCP tools (via Model Context Protocol)

3. **workflow**: Special-purpose workflows
   - discover (discover/install capabilities)
   - revoke (revoke capability)

4. **mcp**: Model Context Protocol servers
   - User-defined in `~/.heare/workspace/.mcp.json`
   - Auto-seeded from global `~/.claude.json` on first run

**Tool Execution Flow**:
```
LLM emits tool call
  ↓
register_function handler (llm_tools.py)
  ↓
execute_direct() for simple tools
  ↓
Returns dict with:
  - success: bool
  - output: str
  - error: str | None
  - exit_code: int | None (bash)
  - spoken: dict[str, str] | str | None  # Voice-friendly summary
```

**Spoken Contract**:
Tools MAY include a `spoken` key for voice-friendly summaries:
```python
return {
    "success": True,
    "output": "...",
    "spoken": {
        "en": "File created successfully",
        "uk": "Файл успішно створено",
        "ru": "Файл успешно создан"
    }
}
```

### 6. Storage Layer

**Purpose**: SQLite persistence for all heare state (transcripts, decisions, actions, heartbeats).

**Schema** (version 5):
```sql
-- Metadata (schema version, etc.)
meta (key, value)

-- User speech transcripts
transcripts (
    id, ts, text, mode, speaker_id, speaker_confidence
)

-- LLM decisions (to speak or not)
decisions (
    id, ts, transcript_id, type, confidence,
    reason, reply, intent, action_json
)

-- Action executions
actions (
    id, ts, decision_id, status, result_summary,
    tool, args, result_json, intent_id
)

-- Periodic heartbeats (30s interval)
heartbeats (id, ts, decided_to_speak, reply)

-- Fine-grained events (decider.start, action.executing, etc.)
events (
    id, ts, kind, transcript_id, decision_id, payload_json
)
```

**Indexes**: All timestamp columns indexed DESC for fast recent queries.

**Async Operations**: Uses aiosqlite so audio pipeline never blocks on disk I/O.

### 7. Conversation Manager

**Purpose**: Maintains conversation state (topics, entities, summary, action log).

**Components**:
- **Action Log**: In-memory deque (maxlen=16) for recent actions
- **Conversation Summary**: LLM-generated summary of conversation
- **Active Topics**: Currently discussed topics
- **Entities**: Recognized entities (people, places, etc.)
- **Recent Turns**: Last N user/bot exchanges

**Write-Through Projection**:
- Action log mutations trigger async SQLite UPSERTs
- DB failures log warning but don't raise (in-memory deque is source of truth)
- Ensures action log survives restarts

### 8. Indication System

**Purpose**: Multi-modal feedback (sound, visual, notification center) for system events.

**Indication Types**:
- `attention`: Bot wants user's attention
- `error`: Error occurred (STT, TTS, action, etc.)
- `long_running`: Long-running action in progress
- `success`: Action completed successfully
- `info`: Informational event
- `input_waiting`: Bot waiting for user input (confirmation)
- `countdown`: Countdown timer active

**Backends**:
1. **SoundBackend**: Audio cues from `indication_assets/`
2. **VisualBackend`: JSONL log at `~/.heare/logs/indication.jsonl`
3. **NotificationBackend**: macOS Notification Center

**Configuration** (`~/.heare/config.toml`):
```toml
[indication]
enabled = true
sound_enabled = true
visual_enabled = true
notification_center_enabled = true
cooldown_seconds = 1.5
quiet_hours = ["22:00-07:00"]

[indication.kinds.attention]
sound = true
visual = true
notification = true
```

### 9. Watch Dashboard

**Purpose**: Real-time monitoring and control of heare daemon.

**UI Components** (Textual TUI):
- **HeaderBar**: Status, mode, provider, model, language
- **ActivityTable**: Recent transcripts, decisions, actions
- **LogTail**: Live daemon log (last 40 lines)
- **ControlsBar**: Mute buttons, daemon control, text injection
- **AIBar**: Provider toggle, model selector

**Hotkeys**:
- `s`: Start daemon
- `x`: Stop daemon
- `r`: Restart daemon
- `m`: Mute bot audio
- `M`: Mute microphone
- `t`: Inject text (simulates speech)
- `p`: Toggle provider (openrouter ↔ zai)
- `o`: Pick model
- `q`: Quit

**Data Source**: Reads from SQLite (`~/.heare/heare.db`) + daemon log.

### 10. Identity System

**Purpose**: Auto-generate heare's persona on first run.

**Identity Components**:
- `name`: Chosen name (e.g., "Алекс", "Мара", "Зоря")
- `creature`: Creature type (e.g., "fox", "owl", "star")
- `vibe`: Personality vibe (e.g., "playful", "wise", "energetic")
- `emoji`: Visual representation (e.g., "🦊", "🦉", "⭐")
- `tagline`: Short tagline

**Bootstrap Process**:
1. Check if `~/.heare/identity.json` exists
2. If not, call OpenRouter API with bootstrap prompt
3. LLM invents a unique persona
4. Save to `identity.json`

**Reset**: `heare reset-identity` backs up and regenerates.

### 11. Mode System

**Purpose**: Hot-reloadable behavior modes.

**Modes**:
1. **silent**: Never speak, never act. Log only.
2. **focus**: Speak only when directly addressed ("Heare, ...") or on clear questions.
3. **ambient**: Also speaks on stuck-user heuristics.

**Implementation**:
- Mode stored in `~/.heare/mode` (plain text file)
- Read on each transcript processing (lazy reload)
- No daemon restart required to change mode

**Command**: `heare mode <silent|focus|ambient>`

### 12. Provider System

**Purpose**: Runtime-switchable LLM provider.

**Providers**:
1. **OpenRouter**: OpenAI-compatible API (default)
   - Model: `google/gemini-2.0-flash-exp` or similar
   - Timeout: 5.0s

2. **z.ai**: Anthropic-compatible API
   - Model: `claude-sonnet-4-20250514` or similar
   - Timeout: 120.0s

**Implementation**:
- Provider stored in `~/.heare/provider` (plain text file)
- Lazy reload on mtime change (only at turn-start frames)
- Sticky-turn gate prevents mid-turn switches

**Command**: `heare provider <openrouter|zai>`

---

## Data Flow Examples

### Example 1: Simple Query

```
User: "Яка погода?"
  ↓
[SileroVAD] Detects speech end
  ↓
[SmartTurnV3] Waits 1.0s for silence
  ↓
[GroqSTT] Transcribes: "Яка погода?"
  ↓
[TranscriptionGate]
  ├── Not in feedback loop ✓
  ├── Language detected: uk
  ├── No cancel keyword ✓
  ├── Log to transcripts table
  └── Pass downstream
  ↓
[SystemPromptInjector]
  └── Rebuild system message with persona, context, etc.
  ↓
[UserAggregator] Appends to LLM context
  ↓
[SwitchableLLMService]
  └── Provider: openrouter
      └── Model: gemini-2.0-flash-exp
  ↓
[AssistantResponseLogger] Captures response text
  ↓
[TTSScrub] Strips tool names
  ↓
[EdgeTTSService] Synthesizes audio (Ukrainian voice)
  ↓
[MuteGate] Not muted ✓
  ↓
[TransportOutput] → Speaker
  ↓
[AssistantAggregator] Appends to context for next turn
```

### Example 2: Action Execution

```
User: "створити файл test.txt"
  ↓
[TranscriptionGate] Passes "створити файл test.txt"
  ↓
[SystemPromptInjector] Includes action log, capabilities
  ↓
[SwitchableLLMService]
  ├── Detects intent: write tool
  └── Emits tool call: write("test.txt: <content>")
  ↓
[LLM Tools Handler]
  ├── ConversationManager.record_action_pending()
  └── execute_direct("write", "test.txt: ...")
  ↓
[Direct Tools]
  ├── Writes file to workspace
  ├── Returns result summary
  └── Spoken: "Файл успішно створено"
  ↓
[ConversationManager] record_action_result()
  ↓
[AssistantResponseLogger] Logs action response
  ↓
[EdgeTTSService] Speaks: "Файл успішно створено"
  ↓
User hears confirmation
```

### Example 3: Cancel During Bot Speech

```
User: "Розкажи про乌克兰"
  ↓
[Bot starts speaking long response]
  ↓
User: "відміни" (cancel keyword)
  ↓
[TranscriptionGate]
  ├── Detects cancel keyword (uk: "відміни")
  ├── Pushes InterruptionFrame upstream
  └── Drops transcript
  ↓
[InterruptionFrame] propagates to:
  ├── [TTSFadeObserver] → TTS.cancel_pending() → 50ms fade-out
  ├── [SwitchableLLMService] → Cancels in-flight register_function calls
  └── [Bot stops speaking]
```

### Example 4: Language Switch

```
User: "Hello!" (English)
  ↓
[GroqSTT] Detects language: en
  ↓
[TranscriptionGate]
  ├── Current language: uk
  ├── First turn in en → set candidate language
  └── Pass downstream
  ↓
[User]: "How are you?" (English, second turn)
  ↓
[TranscriptionGate]
  ├── Candidate language: en (2 consecutive turns)
  ├── Confirms language switch: uk → en
  ├── LanguageState.set_language("en")
  ├── Updates LLM system message to English
  └── TTS.set_voice("en-US-JennyNeural")
```

---

## Key Design Patterns

### 1. Pipeline Pattern (Pipecat)
- Linear chain of frame processors
- Each stage processes frames and pushes downstream
- Interruption via special frame types (InterruptionFrame, CancelFrame)

### 2. Provider Pattern (LLM)
- Abstract LLM service interface
- Multiple implementations (OpenRouter, z.ai)
- Runtime switching without pipeline rebuild

### 3. Strategy Pattern (Tools)
- Abstract tool interface
- Multiple execution strategies (direct, claude, workflow, mcp)
- Tool registry as single source of truth

### 4. Observer Pattern (Indication)
- Multiple backends observe indication events
- Each backend handles events differently (sound, visual, notification)
- Cooldown prevents spam

### 5. Repository Pattern (Storage)
- Abstract database operations
- Async SQLite implementation
- Clean separation of data access logic

### 6. Builder Pattern (Context)
- Complex context construction from multiple sources
- Template-based prompt building
- Safe placeholder substitution (regex-based)

### 7. State Pattern (Mode)
- Behavior changes based on mode (silent/focus/ambient)
- Hot-reloadable without restart
- Mode-specific decision logic

---

## Configuration Files

### ~/.heare/config.toml
```toml
# LLM Providers
openrouter_api_key = "sk-..."
openrouter_model = "google/gemini-2.0-flash-exp"
openrouter_timeout_seconds = 5.0
zai_api_key = "sk-ant-..."
zai_model = "claude-sonnet-4-20250514"
zai_base_url = "https://api.z.ai/v1"

# STT/TTS
groq_api_key = "gsk_..."
groq_language = "auto"  # auto | uk | en | ru
tts_voice = "uk-UA-PolinaNeural"
tts_sample_rate = 24000

# Behavior
mode = "ambient"  # silent | focus | ambient
conversation_memory_enabled = true
conversation_idle_seconds = 3600

# Speaker Recognition
speaker_id_enabled = true
speakers_file = "~/.heare/speakers.json"

# Indication
[indication]
enabled = true
sound_enabled = true
visual_enabled = true
notification_center_enabled = true
cooldown_seconds = 1.5
quiet_hours = ["22:00-07:00"]

# Advanced
confirmation_passphrase = "авторизую"
action_timeout_seconds = 120.0
intent_queue_max_pending = 32
```

### ~/.heare/workspace/.mcp.json
```json
{
  "mcpServers": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/you/Documents"]
    },
    "chrome-devtools": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest"]
    }
  }
}
```

---

## Performance Characteristics

### Latency Budget
- **Target**: Time-to-first-audio ≤ 2.0s
- **STT**: ~200-500ms (Groq Whisper)
- **LLM**: ~500-1500ms (depends on provider/model)
- **TTS**: ~200-400ms (Edge TTS, cached)
- **Total**: ~900-2400ms (within budget most of the time)

### Memory Usage
- **Idle**: ~150-200MB
- **During speech**: ~200-250MB
- **With speaker recognition**: +50MB (ECAPA model)

### CPU Usage
- **Idle**: <5%
- **During speech**: 20-30%
- **STT/LLM/TTS spikes**: 50-80% (brief)

### Disk Usage
- **Logs**: ~10MB/day (with rotation)
- **Database**: ~1-5MB/day (depends on usage)
- **Total**: ~300MB/year with log rotation

---

## Security Considerations

### 1. API Keys
- Stored in `~/.heare/heare.env` (for systemd) or `.env` (for dev)
- Never logged
- Auto-seeded from environment on first run

### 2. MCP Servers
- All servers in `.mcp.json` are auto-authorized
- Inherits from global `~/.claude.json` on first run
- User must manually review and remove unwanted entries

### 3. File Access
- `bash` tool runs in `~/.heare/workspace` by default
- `read`/`write` tools restricted to workspace
- MCP filesystem servers can access additional paths (user-defined)

### 4. Confirmation Passphrase
- Required for risky actions (bash, write, edit)
- Stored in `~/.heare/config.toml` (plaintext)
- Redacted from logs
- Default: "авторизую"

### 5. Speaker Profiles
- Stored in `~/.heare/speakers.json`
- Contains ECAPA embeddings (vectors, not audio)
- Owner flag grants special privileges

---

## Troubleshooting Guide

### Problem: Bot doesn't respond
**Check**:
1. `heare status` - is daemon running?
2. `heare logs` - any errors?
3. Mode: `heare mode focus` (if in silent mode)
4. API keys: Check `.env` file
5. Microphone: System Settings → Privacy → Microphone

### Problem: Audio glitches
**Check**:
1. VAD settings: `start_secs`, `stop_secs`, `confidence` in config
2. Sample rates: 16kHz in, 24kHz out
3. Background noise: Try higher `min_volume`
4. Feedback: Move speakers away from mic

### Problem: Wrong language
**Check**:
1. `groq_language` setting (auto/uk/en/ru)
2. Language hysteresis: Requires 2 consecutive turns
3. TTS voice mapping: Check `voice_for_language()` in `language.py`

### Problem: Actions not executing
**Check**:
1. Confirmation passphrase: Did you say it?
2. Timeout: `action_timeout_seconds` (default 120s)
3. Workspace: Does `~/.heare/workspace` exist?
4. Permissions: Does user have write access?

### Problem: High memory usage
**Check**:
1. Transcript retention: `transcript_retention_days`
2. Action log: Maxlen is 16 (hardcoded)
3. Cache: Restart daemon to clear TTS cache

---

## Future Extensions

### Potential Enhancements
1. **In-flight action cancellation**: Cancel running actions
2. **Multi-user support**: Separate speaker profiles for different users
3. **Custom tools**: User-defined tools via LLM-generated schemas
4. **Voice activity learning**: Adapt VAD to environment
5. **Conversation threading**: Multiple parallel conversations
6. **Memory compression**: Summarize old conversations to save tokens
7. **Streaming actions**: Show progress during long-running actions
8. **Audio input normalization**: Auto-gain control for noisy environments

### API Stability
- **Stable**: Pipeline core, tool registry, storage schema
- **Evolving**: Indication system, conversation manager, capability index
- **Experimental**: Speaker recognition, MCP integration, custom workflows

---

## Conclusion

Heare is a sophisticated voice AI system built on:
- **Pipecat** for audio pipeline orchestration
- **Groq** for fast STT
- **Edge TTS** for free Ukrainian TTS
- **Switchable LLM** (OpenRouter/z.ai) for reasoning
- **Claude Code tools** for action execution
- **SQLite** for persistent storage
- **Textual** for dashboard UI

The architecture is modular, extensible, and designed for continuous operation as a system service. The hot-reloadable modes and providers allow runtime experimentation without restarts, while the comprehensive logging and dashboard provide full observability.

**Key Strengths**:
- Fast response time (<2s latency target)
- Autonomous decision making
- Multi-modal feedback (sound, visual, notifications)
- Rich context building for intelligent responses
- Extensible tool system
- Speaker recognition for personalization
- Bilingual support (Ukrainian/English/Russian)

**Design Philosophy**:
- **Proactive**: Acts without explicit wake word
- **Ambient**: Lives in the background, notifies when needed
- **Agentic**: Can execute actions, not just chat
- **Safe**: Confirmation for risky actions, redacted logs
- **Observable**: Comprehensive logging and dashboard

This architecture analysis covers the complete data flow from microphone input to speaker output, with detailed descriptions of all major components and their relationships.
