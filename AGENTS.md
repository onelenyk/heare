# heare

macOS voice assistant. The engine is `src/spine/` — a framework-free
voice loop. There is no other one: `src/pipeline/` and `src/core/` were
the pipecat engine, deleted on 17 August, and the `engine = "pipecat"`
rollback they were kept for stopped existing with them. Nothing reads
that setting; `Settings` has no such field.

Read [docs/architecture.md](docs/architecture.md) first — it is the
source of truth for how the process is laid out. Then
[docs/findings/*.md](docs/findings) for why things are the shape they
are, and [docs/findings/spine-controls.md](docs/findings/spine-controls.md)
for which dashboard controls actually reach the spine.

## WHERE TO LOOK

| Task | Location |
|------|----------|
| A spoken sentence becomes a reply | `src/spine/loop.py` — `Loop.respond()` |
| Add a tool (voice-model-callable) | `src/agent/tools/system.py` — `TOOLS`, used by `src/agent/hands.py` |
| Add a role | a markdown file in `roles/` — see `roles/README.md` |
| Add/change an adapter (STT/LLM/TTS) | `src/spine/stt.py`, `src/spine/llm.py`, `src/spine/tts.py` |
| The daemon shell (State, pollers, HTTP glue) | `src/daemon/spine_engine.py` |
| Turn a subsystem on/off | `src/spine/features.py` — `FEATURES` |

## COMMANDS

```bash
uv sync --group dev              # install deps + dev
make test                        # uv run pytest -q
make lint                        # uv run ruff check src/ tests/
make format                      # uv run ruff format src/ tests/
make check                       # lint + test
uv run python -m src.main start  # run the daemon in the foreground
```

`from __future__ import annotations` and absolute `from src....` imports
are mandatory throughout.
