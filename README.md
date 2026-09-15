# heare

[![CI](https://github.com/lenyk/heare/actions/workflows/ci.yml/badge.svg)](https://github.com/lenyk/heare/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-63%25-orange)](https://github.com/lenyk/heare)

**heare** is a macOS voice assistant. It listens continuously, decides
when to answer, and can act — bash, file edits, web search, skills, MCP
tools — through a second worker (**Hands**) that runs off the
conversational path, so the voice never blocks on a tool call.

The live engine is `src/spine/`, a framework-free voice loop: DeepSeek
for conversation and tool-calling, Groq Whisper for speech-to-text, Edge
TTS for speech. The prose description is being rewritten: everything moved to
[docs/archive/2026-09-16/](docs/archive/2026-09-16/) on 16 September
because it described last month's assistant. Read it for the reasoning,
not as a statement about today; the plan for the replacement is
[docs/README.md](docs/README.md). This file only covers getting it
running.

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- macOS with a microphone and speaker; `brew install portaudio`
- [Groq API key](https://console.groq.com/keys) (speech-to-text, free tier works)
- [DeepSeek API key](https://platform.deepseek.com/) (conversation + tool-calling)

## Quick start

```bash
git clone <repo>
cd heare
uv sync                       # installs everything, including audio deps
```

Start the daemon in the foreground:

```bash
uv run python -m src.main start
```

Open the dashboard at http://127.0.0.1:9780 and paste your keys in
Setup → Keys (Groq for hearing, and one of DeepSeek / OpenRouter / … for
answering). On macOS they go into the Keychain, service `heare`; on any
other platform into `~/.heare/.env` (mode 0600). A key exported in the
shell wins over both, for development. A missing key doesn't crash the
daemon — it waits, and picks the key up within a second of you saving it.
`make doctor` says which key came from where.

Speak into the microphone; heare transcribes, decides whether to answer,
and replies out loud.

Admin commands (`./hearectl` wraps the same daemon for background use —
`./hearectl start|stop|status|logs`):

```bash
uv run python -m src.main stop            # graceful shutdown
uv run python -m src.main status          # check if running
uv run python -m src.main logs -f         # tail the daemon log
uv run python -m src.main reset-identity  # regenerate the persona
```

## Dashboard

`http://127.0.0.1:9780`, served by the same daemon. First visit opens a
setup modal (identity, language, API keys). The dashboard polls `/state`,
`/activity` and `/display` every second — no WebSocket. Every control on it reaches the live
engine; `make doctor` is the instrument that says what is wired.

## Roles

A role is a markdown file in [`roles/`](roles/) — frontmatter
(`name`, `channel`, `deny_tools`, `artifact`, `triggers`) plus a short
behavior description, switched on by a trigger phrase mid-conversation.
See [`roles/README.md`](roles/README.md) to add one; copy an existing
file and change the name, triggers and body.

## MCP servers

Edit `~/.heare/mcp/.mcp.json` directly:

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

Changes are picked up automatically — no restart. New tools reach the
assistant through **Hands** (the `delegate` verb), not the voice model
directly, which only ever sees three tools of its own: delegate, remember,
recall.

## Tests

```bash
make test          # uv run pytest -q
make check         # lint + test
```

`make e2e` and tests marked `spine_live` hit real Groq/DeepSeek/Edge
endpoints and cost money — excluded from the default run, see
`pyproject.toml`'s `addopts`.

## Troubleshooting

**Mic permission denied** — grant microphone access in System Settings →
Privacy & Security; try `start` in the foreground first.

**Nothing happens on `start`** — run it in the foreground and read the
`features:` line it logs at boot. Every optional subsystem can be off,
and off means not wired at all; `HEARE_SAFE_MODE=1` turns all of them
off at once, `HEARE_WITHOUT=roles,mcp` turns off named ones.

There is no engine to select. `engine = "spine"` in `config.toml` did
something until 17 August and is inert now — the pipecat pipeline it
switched away from was deleted, `Settings` has no such field, and a test
asserts it never comes back.

**STT hanging or slow** — Groq Whisper is the bottleneck, not heare;
check your network and rate limits.

**Persona feels wrong** — `uv run python -m src.main reset-identity`
(backs up `identity.json`, regenerates).

## State layout

```
~/.heare/
├── heare.db          # SQLite: transcripts, memories, usage_events, jobs, actions
├── heare.pid          # running daemon PID
├── config.toml        # user settings — spine_features, wake window, voice
├── .env                # API keys — only where there is no Keychain (Linux); macOS keeps them in the Keychain
├── api_token           # dashboard auth token
├── identity.json       # auto-generated persona
├── roles/               # optional user-added roles (see roles/README.md)
├── mcp/.mcp.json         # MCP server configs, hot-reloaded
├── workspace/            # where bash runs and file tools resolve paths
│   └── artifacts/          # role session summaries
└── logs/
    ├── daemon.log
    └── turns.jsonl          # one line per conversational turn
```
