# Decisions

## Phase 1

### State class design: sync getters + async setters
- Sync `get()`/`get_bool()`/`get_int()` for pipeline hot path (reads from memory dict)
- Async `set()`/`set_bool()`/`set_bulk()` for API calls (writes to cache + SQLite)
- Rationale: Pipeline stages must not block; API calls are infrequent

### Lock scope: per-operation vs persistent connection
- Each setter opens a fresh aiosqlite connection (short-lived)
- Simpler than maintaining a persistent connection
- Fine for file-based SQLite with WAL mode
- Trade-off: `:memory:` won't work (accepted — production uses file path)

### Migration: one-time import on first load
- If cache is empty after DB load, scan legacy files in `~/.heare/`
- Migrates: mute.flag, mute_input.flag, mode, provider, model
- Uses `set_bulk()` for atomic insert (outside init's lock)
