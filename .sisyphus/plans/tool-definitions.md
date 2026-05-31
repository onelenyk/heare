# Tool System Refactor — Data-Driven Definitions

## TL;DR

> Replace 3 files (~15 lines per tool) with 1 file (~2 lines per tool). All tools defined as data in a `TOOLS` list. Schemas, handlers, and registration auto-generated. Adding a tool = 1 ToolDef entry.
>
> **Deliverables**:
> - New: `src/agent/tools/definitions.py` — ToolDef dataclass + TOOLS list
> - New: `src/agent/tools/system.py` — auto-generator (schemas, handlers, registration)
> - Refactored: `registry.py`, `schemas.py`, `direct.py` → simplified/deleted
> - Updated: `build.py` — calls new register_all_tools
> - Tests updated

## Architecture

```
definitions.py          system.py               build.py
──────────              ─────────               ────────
TOOLS = [               for t in TOOLS:         register_all_tools(llm)
  ToolDef(...),           schema = build(t)      → auto from TOOLS
  ToolDef(...),           handler = dispatch(t)
]                         register(llm, handler)
```

## TODOs

### Wave 1: Foundation (2 tasks, parallel)
- [x] Create `definitions.py` — ToolDef dataclass + TOOLS list with ALL 42 tools ported
- [x] Create `system.py` — `build_tools_schema()`, `register_all_tools()`, handler dispatch

### Wave 2: Integration (2 tasks, parallel)
- [x] Refactor `build.py` — replace old register_all_tools call with new one
- [ ] Update `registry.py`, `schemas.py`, `direct.py` — keep backward compat or delete

### Wave 3: Cleanup + tests (2 tasks)
- [x] Update tests for new registration mechanism
- [ ] Run `uv run pytest tests/ -q` — all pass

## Verification

```
uv run python -c "from src.agent.tools.definitions import TOOLS; print(len(TOOLS), 'tools')"
uv run python -c "from src.agent.tools.system import build_tools_schema; print('schema OK')"
uv run pytest tests/ -q --tb=short
```
