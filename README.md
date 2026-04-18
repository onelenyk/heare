# heare

**heare** is a proactive, ambient, agentic voice AI assistant powered by
Claude Code. It lives in your headphones, listens continuously, and decides
autonomously when to speak or act.

- **Listens continuously** via your microphone
- **Decides autonomously** whether each utterance warrants a response
- **Speaks Ukrainian** via free edge-tts voices
- **Can take actions** — it's not just a chatbot; it has Read/Write/Edit/Bash
  via Claude Code, gated by verbal confirmation
- **Remembers everything** across sessions via a persistent Claude Code session
- **Self-names on first run** — auto-generates its own persona via `claude -p`

Not a wake-word assistant. Not a dictation tool. A voice-first Claude Code
agent.

## Architecture

```
Mic ──► SileroVAD ──► GroqSTT ──► SmartTurnV3
                                      │
                                      ▼
                         ┌────────────────────────┐
                         │  DeciderProcessor      │
                         │  state: LISTENING /    │
                         │  AWAITING_CONFIRMATION │
                         │  / EXECUTING           │
                         │  shells out to         │
                         │  claude -p --resume    │
                         └─────────┬──────────────┘
                                   ▼ TextFrame
                         ┌────────────────────────┐
                         │  EdgeTTSService        │
                         └─────────┬──────────────┘
                                   ▼ AudioFrame
                                Speaker

Parallel: HeartbeatTask every N minutes fires on_heartbeat_tick,
so heare can initiate speech on its own.
```

See `.omc/plans/heare-scaffold.md` for the full plan and decisions.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [Claude Code CLI](https://docs.claude.com/claude-code) installed and
  authenticated (`claude --version` should print a version)
- A [Groq API key](https://console.groq.com/keys) (free tier works)
- macOS microphone + speaker
- **For the actual pipeline** (Phase B+): `brew install portaudio` and
  `uv sync --extra local` — pyaudio needs portaudio's headers

## Install

```bash
cd /Users/lenyk/myprojects/heare
uv sync
cp .env.example .env
$EDITOR .env   # set GROQ_API_KEY=
```

For the full voice pipeline you also need portaudio + pyaudio:

```bash
brew install portaudio
uv sync --extra local
```

## Configuration

Optional settings in `~/.heare/config.toml`:

```toml
# Use the persistent claude-agent-sdk backend instead of spawning
# a new `claude -p` subprocess per call (~500-800 ms saved per tick).
# Requires: pip install claude-agent-sdk (included in uv sync)
use_agent_sdk = true

# Override the Claude Code CLI path used by the SDK backend.
# Defaults to whatever `claude` resolves to on $PATH.
# claude_sdk_cli_path = "/usr/local/bin/claude"
```

All other settings (mode, voice, timeouts, speaker recognition, etc.) are
documented as inline comments in `src/config.py`.

## Run

First start bootstraps both the Claude Code session and heare's persona.
The first thing heare says will be its own self-chosen name.

```bash
uv run python -m src.main start
```

Subcommands:

```bash
uv run python -m src.main status         # is the daemon running?
uv run python -m src.main stop            # SIGTERM via pid file
uv run python -m src.main mode silent     # hot-reload mode
uv run python -m src.main mode focus
uv run python -m src.main mode ambient
uv run python -m src.main reset-session   # backup session.json
uv run python -m src.main reset-identity  # backup identity.json
```

## Modes

| Mode      | Decider behavior                                                                                  | Actions allowed      |
| --------- | ------------------------------------------------------------------------------------------------- | -------------------- |
| `silent`  | Never speak, never act. Log only.                                                                 | None                 |
| `focus`   | Speak only when directly addressed ("Heare, ...") or on a clear question into silence.           | Yes, with confirmation |
| `ambient` | Also speaks on stuck-user heuristics and heartbeat check-ins.                                      | Yes, with confirmation |

## State layout

```
~/.heare/
├── session.json          # Claude Code session id (persistent)
├── identity.json         # Auto-generated persona — name, creature, vibe
├── heare.db              # SQLite: transcripts, decisions, actions, heartbeats
├── heare.pid             # Running daemon pid
├── mode                  # Current mode (hot-reloadable)
├── workspace/            # cwd for claude -p — heare writes here by default
└── logs/
    ├── daemon.log
    └── claude-<ts>.log   # One file per claude -p invocation
```

## Verbal confirmation flow

heare never runs a risky action without verbally asking first.

1. You: _"створи файл test.txt в scratch"_
2. heare: _"Хочу create test.txt, можна?"_
3. You: _"так"_ → heare runs the action, speaks a summary
4. You: _"ні"_ → heare cancels, speaks "okay"
5. Silence for 30s → heare auto-cancels and speaks "nevermind, cancelled"

Heartbeat ticks are suppressed while heare is waiting for your
confirmation, so it never interrupts its own prompt.

## Tests

```bash
uv run pytest tests/
```

Unit tests cover the yes/no parser, SQLite store, context builder, and
DeciderProcessor state machine. Tests that require pipecat internals are
auto-skipped when pipecat isn't available.

## Troubleshooting

- **`claude --version` fails** — install the Claude Code CLI first, heare's
  brain is the CLI, not the API.
- **macOS mic permission denied** — the detached daemon might not inherit
  mic permission. Try `nohup` in the foreground first, then background.
- **Groq rate limit** — the free tier has per-minute caps. heare logs a
  warning as you approach them.
- **Session corruption** — run `uv run python -m src.main reset-session`.
- **Persona feels wrong** — run `uv run python -m src.main reset-identity`
  and restart. heare will pick a new name.

## Status

- **Phase A (scaffold):** complete — 63 tests passing.
- **Phase B-E code readiness:** complete in code. Portaudio + pyaudio
  installed via `uv sync --extra local`. Bugs flagged in the first
  architect review (mode hot-reload, silent-timeout, SIGTERM cancel,
  edge-tts general errors, log rotation, rate limiter) are fixed and
  covered by new unit tests (`test_mode_hot_reload.py`, `test_silent_timeout.py`,
  `test_shutdown.py`, `test_log_rotation.py`, `test_edge_tts_errors.py`,
  `test_rate_limit.py`).
- **Live-hardware verification remaining (only thing left):**
  1. Copy `.env.example` to `.env` and set `GROQ_API_KEY`.
  2. Grant mic permission to your terminal / Python in System Settings.
  3. `uv run python -m src.main start` and speak Ukrainian into the mic.
  4. Phase B/C/D/E criteria that need a live voice loop (§6 of
     `.omc/plans/heare-scaffold.md`) are verifiable by the user at that
     point; everything else is already green.

### Rate-limit caveat

heare caps its own `claude -p` decider + action calls at
`claude_max_calls_per_minute` (default 30) via `src/rate_limit.py`.
GroqSTTService runs inside Pipecat and is NOT rate-limited by heare —
if you hit the Groq free tier ceiling, pipecat surfaces the error and
heare logs it via the daemon log. This is deliberate: the biggest cost
center is our own claude calls, not Groq STT.

### Experimental generator mode (Phase 1 s2s-realtime)

`settings.generator_mode` flips the pipeline from the legacy
decider/aggregator stack to a stripped always-replies generator backed
by OpenRouter streaming. Enable with:

```toml
# ~/.heare/config.toml
generator_mode = true
openrouter_api_key = "…"          # OR set OPENROUTER_API_KEY in .env
openrouter_model = "google/gemini-3.1-flash-lite-preview-20260303"
openrouter_timeout_seconds = 5.0
```

When enabled:
- pipeline becomes `transport → STT → GeneratorProcessor → TTS → transport`
- decider / speaker_id / turn_aggregator / confirmation / conversation_memory
  are **not instantiated**; they return in Phase 2 via individual PRDs
- target time-to-first-audio ≤2s (vs 7-13s on the legacy path)

**This flag is emergency-rollback only.** It will be removed by the end
of Phase 2.1 when the intent queue + action worker land. Don't build new
features behind it — build on the generator path directly.
