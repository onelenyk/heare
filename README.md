# heare

[![CI](https://github.com/lenyk/heare/actions/workflows/ci.yml/badge.svg)](https://github.com/lenyk/heare/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-63%25-orange)](https://github.com/lenyk/heare)

**heare** is a macOS voice assistant. It listens continuously, decides
when to answer, and can act — bash, file edits, web search, skills, MCP
tools — through a second worker (**Hands**) that runs off the
conversational path, so the voice never blocks on a tool call.

The live engine is `src/spine/`, a framework-free voice loop: DeepSeek
for conversation and tool-calling, Groq Whisper for speech-to-text, Edge
TTS for speech. See [docs/architecture.md](docs/architecture.md) for the
full picture — this file only covers getting it running.

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

cp .env.example .env
$EDITOR .env                  # set GROQ_API_KEY and DEEPSEEK_API_KEY
```

Select the live engine — the default is the old pipecat one:

```bash
mkdir -p ~/.heare
echo 'engine = "spine"' >> ~/.heare/config.toml
```

Start the daemon in the foreground:

```bash
uv run python -m src.main start
```

Speak into the microphone; heare transcribes, decides whether to answer,
and replies out loud. A missing key doesn't crash the daemon — it waits,
and picks the key up within a second of you saving it to `.env`.

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
`/activity` and `/display` every second — no WebSocket. Which dashboard
controls actually reach the live engine (several still don't) is tracked
in [docs/findings/spine-controls.md](docs/findings/spine-controls.md).

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

**Nothing happens on `start`** — check `engine` in `~/.heare/config.toml`;
if it's absent or `"pipecat"` you're on the rollback engine, which needs
different keys (OpenRouter or z.ai) and `uv sync --extra local` no longer
exists as a separate step, everything installs with plain `uv sync`.

**STT hanging or slow** — Groq Whisper is the bottleneck, not heare;
check your network and rate limits.

**Session corruption** — `uv run python -m src.main reset-session`
(backs up `session.json` first).

**Persona feels wrong** — `uv run python -m src.main reset-identity`
(backs up `identity.json`, regenerates).

## State layout

```
~/.heare/
├── heare.db          # SQLite: transcripts, memories, usage_events, jobs, actions
├── heare.pid          # running daemon PID
├── config.toml        # user settings — engine = "spine" lives here
├── .env                # API keys (or use process env vars)
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
