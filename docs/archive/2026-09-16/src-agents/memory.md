# src/memory/

Pluggable memory backend with FTS5 full-text search, regex-based zero-LLM extraction, and LLM tool handlers.

## STRUCTURE

```
memory/
├── base.py             # MemoryBackend ABC + NoopBackend + MemoryEntry + MemoryType
├── sqlite_backend.py   # SQLiteBackend — aiosqlite + FTS5 + BM25 ranking
├── extractor.py        # Zero-LLM regex extractor (EN/UK/RU patterns)
├── factory.py          # create_memory_backend(settings) — backend selection
├── tools.py            # LLM tool handlers: remember, recall, forget, memory_status
└── __init__.py
```

## DATA FLOW

```
Per turn: ContextBuilder → extract_and_store() → fire-and-forget regex extraction
On prompt rebuild: ContextBuilder → backend.context(query, limit=3)
LLM commands: remember()/recall()/forget() via register_function handlers
Dashboard: GET /api/memories, GET /api/memories/stats, POST /api/memories/{id}/forget
```

## EXTRACTION

`extractor.py` uses regex patterns per language (EN + UK + RU). Zero LLM calls. Patterns for: identity, preferences, decisions, events. Runs as fire-and-forget background task after each turn via `extract_and_store()`.

## SQLITE BACKEND

- WAL mode + FTS5 content-sync virtual table
- BM25 ranking boosted by recency (`access_count * 0.1 + recency_bonus`)
- Soft-delete via `archived` column
- Memory dedup via type-priority ordering (PREFERENCE > FACT)

## GOTCHAS

- New extractor patterns must be added per-language (don't break other languages)
- `search()` uses `ORDER BY rank` with the recency boost — if you change ranking, retest
- Backend selection via `settings.memory_backend` string: `"sqlite"` | `"noop"` | `"engram"` (NYI) | `"mem0"` (NYI)
