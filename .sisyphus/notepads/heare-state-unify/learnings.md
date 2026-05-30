# Learnings

## Phase 1: State Class Creation

### asyncio.Lock non-reentrancy deadlock
- `init()` acquires `self._lock`, then calls `_migrate_legacy()` which calls `set_bulk()` which tries to acquire `self._lock` again
- `asyncio.Lock()` is NOT reentrant → silent deadlock (no error, 0% CPU)
- Fix: release lock in `init()` before calling `_migrate_legacy()` 
- Guardrail comment necessary to prevent future refactors from reintroducing

### :memory: SQLite is connection-scoped
- Each `aisqlite.connect(':memory:')` creates a separate in-memory DB
- Our `set()`/`set_bulk()` open new connections → `:memory:` won't work across operations
- File-based DB (the real use case) works fine since all connections see the same WAL file
- Tests must use temp files, not `:memory:`

### Pattern: sync getters + async setters
- Sync getters for pipeline hot path (zero I/O, reads from `_cache` dict)
- Async setters for API calls (writes to both cache and SQLite)
- This is intentional and matches the plan
