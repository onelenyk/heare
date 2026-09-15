# src/store/

SQLite persistence (aiosqlite, never blocks the event loop) + conversation state + LLM context builder.

## STRUCTURE

```
store/
├── storage.py          # TranscriptStore — SQLite DAO (schema v8)
├── context.py          # ContextBuilder — builds prompt context dicts
├── conversation.py     # ConversationManager — action log, topics, entities
├── user_profile.py     # Cross-session user preferences
└── __init__.py
```

## TranscriptStore

Tables: `transcripts`, `displays`, `actions`, `conversations`, `turns`, `dynamic_tools`, `usage_events`, `user_profile`, `meta`, `state`.

- All async via `aiosqlite`
- Schema migration via `meta` table key `schema_version`
- `ON CONFLICT` does NOT work with partial indices — the `unique_url` index is unconditional

## ContextBuilder

`build_for_generator()` assembles the LLM system prompt context:
- Persona + recent transcripts + conversation summary
- Active topics + entities + action log
- Memory results (from `MemoryBackend.context()`)
- Mode block, MCP descriptions, sub-agent status, display content

Uses regex-based placeholder substitution (`{{placeholder}}`) — NOT `str.format()`.

## ConversationManager

In-memory action log (`deque(maxlen=16)`) with SQLite write-through via `record_action_*` methods. `hydrate_action_log()` on startup rebuilds from persisted actions.

## GOTCHAS

- `context.py` wraps EVERY external call in `except Exception` — never breaks the turn
- `conversation.py:232` uses bare `loop.create_task(coro)` — known bug, not `safe_task()`
- `ContextBuilder` is the critical path for every LLM turn — avoid adding slow operations here
