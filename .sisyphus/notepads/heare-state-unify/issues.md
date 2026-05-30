# Issues & Gotchas

## Phase 1

### [FIXED] Deadlock in init() → _migrate_legacy()
- `asyncio.Lock()` is non-reentrant
- Fixed by releasing lock before calling `_migrate_legacy()`

### [KNOWN] :memory: DB doesn't work across connections
- Each aiosqlite.connect(':memory:') is a separate DB
- Not an issue in production (file-based DB), only affects test convenience
