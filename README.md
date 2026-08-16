# heare

[![CI](https://github.com/lenyk/heare/actions/workflows/ci.yml/badge.svg)](https://github.com/lenyk/heare/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-63%25-orange)](https://github.com/lenyk/heare)

**heare** is a proactive ambient voice AI assistant powered by Claude. It listens continuously via your microphone, decides when to speak, and can execute actions via browser automation, bash, and file manipulation — all gated by verbal confirmation.

Not a wake-word assistant. Not a dictation tool. A voice-first Claude agent.

## Features

- **Continuous listening** — VAD-gated mic input, no wake word needed
- **Autonomous decision-making** — silent/focus/ambient modes determine when to respond
- **Multilingual** — detects and switches TTS voice per utterance (English, Ukrainian, Russian default; Groq's Whisper detects others)
- **Ukrainian voice persona** — auto-generates name and personality on first run
- **Browser automation** — via sideloaded Chrome extension (list tabs, read page, click, fill, navigate, extract, open/activate tabs)
- **Agent tools** — bash, read, write, edit files; web search/fetch; dynamic tool creation; skill execution
- **Persistent memory** — SQLite database (`~/.heare/heare.db`) with transcripts, decisions, and usage events
- **Hot-reload settings** — switch mode/LLM provider without restarting daemon

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- macOS with microphone + speaker (Linux support in progress)
- [Groq API key](https://console.groq.com/keys) (free tier works; for STT)
- OpenRouter OR z.ai API key (for LLM)
- `brew install portaudio` + `uv sync --extra local` for audio pipeline

## Installation

```bash
git clone <repo>
cd heare
uv sync
uv sync --extra local  # For full audio pipeline (portaudio required)

cp .env.example .env
$EDITOR .env  # Set GROQ_API_KEY, OPENROUTER_API_KEY (or ZAI_API_KEY)
```

Run onboarding:

```bash
uv run python -m src.main setup
```

This walks through MCP server setup, workspace seeding, confirmation passphrase, and browser extension pairing.

## Quick start

Start the daemon in the foreground:

```bash
uv run python -m src.main start
```

Speak into the microphone. heare will transcribe, decide whether to respond, and speak back.

Admin commands:

```bash
uv run python -m src.main status              # Check if running
uv run python -m src.main stop                # Graceful shutdown
uv run python -m src.main mode silent         # Hot-reload mode
uv run python -m src.main mode focus
uv run python -m src.main mode ambient
uv run python -m src.main provider zai        # Hot-reload LLM provider
uv run python -m src.main provider openrouter
uv run python -m src.main logs -f             # Tail daemon log
```

## Modes

| Mode | Behavior | Actions |
|------|----------|---------|
| **silent** | Transcribes only; never speaks or acts | None |
| **focus** | Responds when directly addressed ("heare...") or to clear questions in silence | Yes, with verbal confirmation |
| **ambient** | Also responds to stuck-user heuristics; more proactive | Yes, with verbal confirmation |

## Architecture

```
──────────────────────────────────────────────────────────────────

Mic ──► input_mute_gate ──► GroqSTT
                               │
                               ▼
                       voice_state_observer
                       (~/.heare/voice_state.json)
                               │
                               ▼
                        transcription_gate
                        (debounce, cancel-word detect,
                         language switch, bot-speaking drop)
                                │
                                ▼
                        system_prompt_injector
                        (per-turn context rebuild)
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
                user_aggregator      SwitchableLLMService
                (VAD + turn           (OpenRouter or z.ai)
                 analyzer)                 │
                    │                      │
                    └──────────┬───────────┘
                               ▼
                       assistant_response_logger
                       (log LLM text to transcripts)
                               │
                               ▼
                         tts_scrub_processor
                         (strip tool narration)
                               │
                               ▼
                         EdgeTTSService + cache
                               │
                      ┌────────┴────────┐
                      ▼                 ▼
                  usage_recorder    speaker output
                  (cost ledger)

The daemon runs all stages in a single asyncio event loop. The web
dashboard at http://127.0.0.1:9780 reads the SQLite database + daemon log.
The Chrome extension runs in the user's browser, connected via WebSocket
to the daemon's browser bridge on 127.0.0.1:9333.

```

**Key points:**

- **Single daemon process** — one asyncio loop, no thread pool
- **Web dashboard** — browser UI at http://127.0.0.1:9780
- **Browser bridge** — MV3 extension on 127.0.0.1:9333 (WebSocket + token auth, single client)
- **Pipeline stages** — see `src/pipeline/build.py` for exact order and optional conditionals
- **LLM backend** — Pipecat-native `SwitchableLLMService` with hot-reload support via `src/config.py:provider_file`

## Browser bridge (Chrome extension)

The sideloaded extension at `extensions/heare-bridge/` (MV3, Chrome 109+) exposes 8 browser tools to the LLM:

- `list_browser_tabs` — list open tabs
- `read_browser_page` — read current tab's text content
- `click_in_browser` — click element by CSS selector
- `fill_in_browser` — fill form field by CSS selector
- `navigate_browser` — load URL in tab
- `extract_in_browser` — extract DOM elements by CSS selector
- `open_browser_tab` — open new tab
- `activate_browser_tab` — bring tab to foreground

**Install:**

1. Navigate to `chrome://extensions`
2. Enable "Developer mode" (top right)
3. Click "Load unpacked"
4. Select `extensions/heare-bridge/`
5. Enter pair code from `~/.heare/browser_bridge.status` or dashboard hotkey

The extension runs an offscreen document that owns the persistent WebSocket. The daemon accepts one client at a time; second connections are rejected with close code 4002.

## MCP servers

heare automatically seeds `~/.heare/workspace/.mcp.json` from `~/.claude.json` on first run. Every server listed in that file is callable by the agent.

Edit the file directly to add servers, then restart the daemon:

```json
{
  "mcpServers": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["@modelcontextprotocol/server-filesystem", "/Users/you/Documents"]
    }
  }
}
```

## Tools

Built-in tools available to the LLM:

- **bash** — execute shell commands
- **read** / **write** / **edit** — file operations
- **web_search** / **web_fetch** — web access (Serper or DuckDuckGo)
- **workflow** — multi-step action sequences
- **list_skills** / **run_skill** / **create_skill** — Agent Skills (agentskills.io format)
- **list_capabilities** / **discover_capability** / **install_skill_tool** / **install_mcp_server_tool** / **revoke_capability** — capability discovery + install
- **create_tool** / **update_tool** / **delete_tool** / **list_tools** — dynamic tool CRUD
- **create_archive** / **extract_archive** / **batch_operation** — file batch ops
- **set_provider** — switch LLM provider (openrouter ↔ zai)
- **stop_daemon** / **restart_daemon** — daemon control

All actions require verbal confirmation via `heare set-passphrase <phrase>` or default yes/no flow.

## Configuration

Most settings live in `~/.heare/config.toml`. See `src/config.py:Settings` for the canonical reference with defaults and descriptions.

Most-used keys:

```toml
mode = "ambient"                # silent | focus | ambient
tts_voice = "en-US-AriaNeural"  # or any supported Edge TTS voice
groq_language = "uk"            # STT language hint (Groq detects + may override)
browser_bridge_enabled = true   # Enable Chrome extension bridge

openrouter_api_key = "…"        # OR set OPENROUTER_API_KEY env var
openrouter_model = "google/gemini-3.1-flash-lite-preview-20260303"

[indication]
enabled = true
sound_enabled = true
quiet_hours = ["22:00-07:00"]

[browser_bridge]
port = 9333
token = "…"  # Auto-generated; rotate with `heare rotate-browser-token`
```

## State layout

```
~/.heare/
├── heare.db                    # SQLite: transcripts, decisions, tools, usage_events
├── heare.pid                   # Running daemon PID (single-instance lock)
├── daemon.log                  # Daemon output (rotating, 10MB max, 3 backups)
├── config.toml                 # User settings (optional; defaults in code)
├── mode                        # Current mode: silent | focus | ambient (hot-reloadable)
├── provider                    # Current LLM provider: openrouter | zai (hot-reloadable)
├── session.json                # Claude Code session ID (persistent)
├── identity.json               # Auto-generated persona: {name, emoji, voice_type, …}
├── voice_state.json            # Current VAD state: {state, since_ts, last_*}
├── capabilities.json           # Capability index cache (auto-refreshed)
├── onboarding.json             # Setup progress
├── heare.log                   # Tail via `heare logs -f`
├── browser_bridge.status       # Pair-code + server status (for dashboard)
├── browser_bridge.token        # Token convenience file (chmod 600)
├── mute.flag                   # Touch to mute speaker; rm to unmute
├── mute_input.flag             # Touch to mute mic; rm to unmute
├── inject/                     # Text injection: drop .txt files → TranscriptionFrame
├── logs/
│   ├── daemon.log              # Main daemon log
│   └── indication.jsonl        # Visual+sound cue events (JSON lines)
└── workspace/
    ├── .mcp.json               # MCP server configs (seeded from ~/.claude.json)
    └── …                       # Working directory for file operations
```

## Verbal confirmation flow

Actions that modify state (file write, bash execute) require verbal confirmation:

1. User: _"create a file called test.txt"_
2. heare: _"I want to write test.txt, okay?"_
3. User: _"yes"_ → executes, speaks result
4. User: _"no"_ → cancels, speaks "okay"
5. Silence 30s → auto-cancels, speaks "nevermind"

Set a custom passphrase to confirm without yes/no:

```bash
uv run python -m src.main set-passphrase "авторизую"  # Restart daemon
```

Then: User: _"create file test.txt авторизую"_ → executes immediately.

## Development

### Tests

```bash
uv run pytest tests/ -v
```

Unit tests cover:
- Mode hot-reload
- Transcription debounce + cancellation
- TTSCache warmup
- Usage ledger
- Dynamic tool CRUD
- Browser bridge token rotation

### Project layout

```
src/
├── main.py                     # CLI entry point + daemon startup
├── config.py                   # Settings dataclass (canonical config reference)
├── pipeline/
│   ├── build.py                # Pipecat pipeline assembly
│   ├── stages/                 # Custom processors (gate, observer, logger, etc.)
│   └── language_state.py       # Detected language tracking
├── agent/
│   ├── browser_bridge.py       # WebSocket server + RPC dispatch
│   ├── tools/
│   │   ├── registry.py         # Tool definitions
│   │   ├── schemas.py          # JSON schemas + handlers
│   │   ├── direct.py           # Direct tool execution (bash, read, write, etc.)
│   │   ├── dynamic.py          # User-created tools
│   │   └── capability_index.py # Skill/MCP discovery
│   └── llm/
│       ├── switchable.py       # OpenRouter ↔ z.ai hot-reload
│       └── context_injector.py # Per-turn system prompt rebuild
├── voice/
│   ├── stt/                    # Groq Whisper service
│   ├── tts/
│   │   ├── edge.py             # Edge TTS service
│   │   └── cache.py            # TTSCache with warmup
│   └── indication/             # Sound + visual + notification backends
├── store/
│   ├── storage.py              # SQLite DAO (transcripts, tools, usage)
│   └── context.py              # Context builder (recent transcripts, etc.)
├── spine/                      # THE VOICE ENGINE (no framework)
│   ├── audio_io.py             # sounddevice in/out, gain, mute
│   ├── aec.py / far_end.py     # WebRTC AEC3, full duplex
│   ├── vad.py / turn.py        # energy VAD, one clock per turn
│   ├── stt.py / llm.py / tts.py# Groq, DeepSeek streaming, EdgeTTS
│   ├── loop.py                 # the conductor (imports no sibling)
│   ├── roles.py / role_session.py  # the role platform
│   ├── hallucinations.py       # what Whisper says to an empty room
│   └── telemetry.py            # one JSON line per turn
├── pipeline/                   # legacy pipecat engine (rollback only)
└── daemon/
    ├── spine_engine.py         # runs the spine inside the daemon shell
    ├── onboarding.py           # Setup flow
    └── workspace.py            # MCP seeding
```

Architecture: [docs/architecture.md](docs/architecture.md).

## Troubleshooting

**Mic permission denied**
- Grant microphone access in System Settings → Privacy & Security
- Try `heare start` in foreground first (not backgrounded)

**Groq rate limit**
- Free tier has per-minute caps. Set `groq_api_key` via `.env` or env var
- heare logs warnings as you approach limits

**STT hanging or slow**
- Groq Whisper is the bottleneck, not heare. Check your network.

**Browser extension not connecting**
- Verify `chrome://extensions` shows "Heare Bridge" as enabled
- Check daemon log: `heare logs -f | grep browser`
- Restart daemon: `heare stop && heare start`

**Cost is too high**
- Reduce `context_recent_transcripts_count` (fewer history tokens)
- Switch to a cheaper model: `heare provider zai` + adjust `zai_model`
- Use cheaper TTS provider (Edge TTS is free)

**Session corruption**
```bash
uv run python -m src.main reset-session  # Backs up session.json
```

**Persona feels wrong**
```bash
uv run python -m src.main reset-identity  # Backs up identity.json, regenerates
```
