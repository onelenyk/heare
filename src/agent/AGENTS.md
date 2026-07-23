# src/agent/

LLM services, tool system, mode profiles, browser/MCP bridges, sub-agent manager.

## STRUCTURE

```
agent/
├── llm/
│   ├── providers.py         # PROVIDERS registry (price, model, API keys)
│   ├── switchable.py        # Hot-swap LLMService (DeepSeek/z.ai/OpenCode)
│   ├── context_injector.py  # Per-turn system prompt rebuild
│   ├── prompt_sections.py   # Modular prompt section definitions
│   └── pricing.py           # Cost calculator
├── tools/
│   ├── registry.py          # TOOLS dict — single source of truth
│   ├── direct.py            # execute_direct() — 50+ tool handlers (4k lines)
│   ├── schemas.py           # Pipecat register_function bridge
│   ├── system.py            # Tool schema builder + registration
│   ├── dynamic.py           # User-created tool execution
│   ├── capability_index.py  # Unified index (tools + skills + MCP)
│   └── subagent.py          # run_opencode() subprocess spawn
├── modes.py                 # ModeProfile dataclass + profiles
├── browser_bridge.py        # WebSocket server for Chrome extension
├── mcp_bridge.py            # stdio MCP server spawn + tool registration
├── identity.py              # Auto-generated persona (name, emoji, vibe)
└── subagent_manager.py      # Background OpenCode sub-agent pool
```

## WHERE TO LOOK

| Task | Location |
|------|----------|
| Add a tool | `registry.py:TOOLS` → `direct.py` handler → `schemas.py` arg schema → `system.py:build_tools_schema()` |
| Add a provider | `llm/providers.py:PROVIDERS` dict — one entry per provider |
| Add a mode | `modes.py:MODE_PROFILES` dict |
| Change system prompt | `llm/context_injector.py` + `prompt_sections.py` |
| Add MCP server | `mcp_bridge.py:connect_mcp_servers()` — no code change needed |

## GOTCHAS

- **Tool handlers lost on LLM hot-swap**: `register_function` calls are cached in `schemas.py`. New registrations must replay on delegate rebuild. See commit `9463e35`.
- **`direct.py` is 4k lines** — it's a switchboard router. Do NOT add more handlers without considering a split (tools/bash.py, tools/browser.py, etc.)
- **`mcp_bridge.py` has 9 `except Exception` blocks** — best-effort per-server. A failing MCP server never blocks daemon startup.
- **`subagent_manager.py` pooled at 5 concurrent** — `max_concurrent` in settings.
- **Browser bridge** is single-client. Second WebSocket connection gets close code 4002.
