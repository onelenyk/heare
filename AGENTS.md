# heare

macOS voice assistant. The engine is `src/spine/` — a framework-free
voice loop. There is no other one: `src/pipeline/` and `src/core/` were
the pipecat engine, deleted on 17 August, and the `engine = "pipecat"`
rollback they were kept for stopped existing with them. Nothing reads
that setting; `Settings` has no such field.

**The prose description is being rewritten.** Every document was moved to
[docs/archive/2026-09-16/](docs/archive/2026-09-16/) on 16 September,
because it described the assistant of a month ago. Read it for the
reasoning, never as a statement about today; the plan for the
replacement is [docs/README.md](docs/README.md).

Until it lands, the truthful sources are the code itself, `make doctor`
(what is actually wired) and the tests. The archive's
[architecture.md](docs/archive/2026-09-16/architecture.md) is still the
best picture of the layout, and
[findings/](docs/archive/2026-09-16/findings/) is still the best account
of why things are the shape they are.

## WHERE TO LOOK

| Task | Location |
|------|----------|
| A spoken sentence becomes a reply | `src/spine/loop.py` — `Loop.respond()` |
| Add a tool (voice-model-callable) | `src/agent/tools/system.py` — `TOOLS`, used by `src/agent/hands.py` |
| Add a role | a markdown file in `roles/` — see `roles/README.md` |
| Add/change an adapter (STT/LLM/TTS) | `src/spine/stt.py`, `src/spine/llm.py`, `src/spine/tts.py` |
| The daemon shell (State, pollers, HTTP glue) | `src/daemon/spine_engine.py` |
| Turn a subsystem on/off | `src/spine/features.py` — `FEATURES` |
| Find out what is actually connected | `make doctor` |

## COMMANDS

```bash
uv sync --group dev              # install deps + dev
make test                        # uv run pytest -q
make lint                        # uv run ruff check src/ tests/
make format                      # uv run ruff format src/ tests/
make check                       # lint + test
make doctor                      # what is wired, asked of the live objects
uv run python -m src.main start  # run the daemon in the foreground
```

`from __future__ import annotations` and absolute `from src....` imports
are mandatory throughout.
