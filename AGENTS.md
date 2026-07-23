# heare

macOS desktop voice AI assistant — Pipecat audio pipeline, LLM agent tools, SQLite persistence, React dashboard, Chrome extension bridge.

## STRUCTURE

```
./
├── src/               # Python application (30k lines, 93 .py files)
│   ├── main.py        # CLI entry + daemon lifecycle
│   ├── config.py      # Settings dataclass (canonical config)
│   ├── state.py       # SQLite-backed k/v state store
│   ├── api.py         # aiohttp REST API (1.8k lines, 50+ routes)
│   ├── pipeline/      # Pipecat audio pipeline assembly + stages
│   ├── agent/         # LLM services, tools, modes, bridges
│   ├── memory/        # Pluggable FTS5 memory backend
│   ├── store/         # SQLite DAO, conversation, context builder
│   ├── voice/         # TTS (Edge), STT (Groq), indication, language
│   ├── skills/        # Skills marketplace, installer, MCP utils
│   ├── daemon/        # Lifecycle helpers (workspace, heartbeat, events)
│   └── frontend/      # React + Vite SPA dashboard
├── tests/             # 87 pytest files, no conftest.py
├── prompts/           # 10 LLM prompt templates (plain text)
└── extensions/
    └── heare-bridge/  # MV3 Chrome extension (WebSocket bridge)
```

## WHERE TO LOOK

| Task | Location |
|------|----------|
| Pipeline data flow | `src/pipeline/build.py` — `build_pipeline()` + `_assemble_native_stages()` |
| Add a pipeline stage | `src/pipeline/stages/` — copy factory pattern from existing stage |
| Tool registration | `src/agent/tools/registry.py` — `TOOLS` dict + `register_dynamic_tool()` |
| Tool execution | `src/agent/tools/direct.py` — `execute_direct()` dispatcher (4k lines) |
| LLM provider config | `src/agent/llm/providers.py` — `PROVIDERS` dict |
| LLM hot-swap | `src/agent/llm/switchable.py` — `SwitchableLLMService` |
| System prompt rebuild | `src/agent/llm/context_injector.py` — `SystemPromptInjector` |
| Mode profiles | `src/agent/modes.py` — `MODE_PROFILES` dict |
| Memory backend | `src/memory/base.py` (ABC) + `sqlite_backend.py` (FTS5 impl) |
| Config | `src/config.py` — `Settings` dataclass, `load_settings()` |
| HTTP API routes | `src/api.py` — `API` class, `register_routes()` |
| Dashboard frontend | `src/frontend/src/components/Dashboard.jsx` |
| Tests | `tests/test_*.py` — one file per module |
| Browser bridge | `extensions/heare-bridge/` — Chrome extension |
| MCP servers | `src/agent/mcp_bridge.py` — stdio spawn + tool registration |
| Sub-agents | `src/agent/subagent_manager.py` — OpenCode child processes |
| Event system | `src/daemon/events.py` — `emit()`, `recent()`, ring buffer |

## CONVENTIONS

- `from __future__ import annotations` — **mandatory** in every Python file
- Absolute imports only: `from src.<pkg>.<module> import ...`
- Ruff defaults only (no custom config) — `ruff check src/ tests/`
- Mypy installed but **NOT enforced** — types are documentation only
- `pytest` with `asyncio_mode = auto`, no global conftest.py (fixtures inline)
- `uv` is the mandatory package manager — no pip/poetry
- `snake_case` for Python, `PascalCase` for components (JSX)
- `logger = logging.getLogger("heare.<subsystem>")` per module
- Settings via `@dataclass` in `config.py` — not env vars directly
- State via flag files in `~/.heare/` — `mode`, `mute.flag`, `cancel.flag`
- `safe_task()` for fire-and-forget async — never bare `create_task()`
- Pipecat imports deferred to function bodies (CLI paths must not need portaudio)

## ANTI-PATTERNS

1. **Never break the turn** — wrap risky code in `except Exception` + `logger.exception()`. Do NOT catch `BaseException` (swallows `KeyboardInterrupt`).
2. **`safe_task()` not `create_task()`** — bare `create_task` exists in `conversation.py:232` and `usage_recorder.py:138/191/227` but is a known bug. Do NOT add more.
3. **LLM hot-swap loses tool handlers** — commit `9463e35` fixed this. Any new tool registration must replay on delegate rebuild.
4. **Pipeline teardown in `try/finally`** — commit `a94dd6c`: cancellation skipped cleanup. Always use `finally` + `asyncio.shield()`.
5. **`TranscriptionFrame` vs `LLMMessagesAppendFrame`** — using `TranscriptionFrame` without audio silently buffers text to next utterance. Use `LLMMessagesAppendFrame(run_llm=True)` for injected text.
6. **`# type: ignore[misc,valid-type]` on `FrameProcessor`** — systemic Pipecat gap. Don't add more than necessary.
7. **Deferred pipecat imports required** — importing pipecat at module level breaks `heare status`, `heare stop`.

## COMMANDS

```bash
uv sync --group dev        # Install deps + dev
uv run pytest -q            # Tests (quick)
uv run pytest -v            # Tests (verbose)
uv run pytest --cov=src     # Coverage
uv run ruff check src/ tests/   # Lint
uv run ruff format src/ tests/  # Format
uv run python -m src.main start # Dev daemon
make frontend               # Build React dashboard
make build                  # PyInstaller .app
make dmg                    # Create Heare.dmg
make check                  # Lint + test
```

## NOTES

- Portaudio required for audio — `brew install portaudio`
- API keys: `.env` file (Groq, OpenRouter/z.ai, Serper)
- Frontend poll-based (no WebSocket) — polls `/state`, `/activity`, `/display` every 1s
- Dashboard at `http://127.0.0.1:9780`
- Browser bridge at `ws://127.0.0.1:9333`
- Daemon data in `~/.heare/`
- Pipeline is a single asyncio loop — no threading (except menubar GUI thread)
