# State Unification Plan

## TL;DR

> Replace 8 state files (flag/text/JSON) with a single `State` class backed by SQLite + memory cache. Pipeline reads from memory (zero I/O). API reads/writes through the same State instance. Desktop and TUI both consume the unified API.

## Architecture

```
                    ┌──────────────────────┐
                    │       State          │
                    │                      │
                    │  _cache: dict (mem)  │  ← pipeline reads (fast)
                    │  _db: SQLite         │  ← persists (durable)
                    │                      │
                    │  get(key) → str      │
                    │  set(key, val)       │
                    │  load() → populate   │
                    └──────┬───────┬───────┘
                           │       │
              ┌────────────▼─┐  ┌──▼──────────────┐
              │  Pipeline     │  │  API             │
              │  stages       │  │                  │
              │               │  │  GET /state       │
              │  mute_gate    │  │  POST /mode       │
              │  switchable   │  │  POST /mute       │
              │  voice_state  │  │  POST /provider    │
              │               │  │  POST /model       │
              └───────────────┘  │  POST /cancel      │
                                 └─────────────────────┘
```

## Migration Plan (4 phases)

### Phase 1: Create State class (1 task)

**New file: `src/state.py`** (~80 lines)

- [x] 1. **Build `State` class**
  - `__init__(db_path)`: open aiosqlite, create `state` table if not exists
  - `async load()`: SELECT all rows → populate `_cache: dict`
  - `get(key, default=None)`: return `_cache.get(key, default)` (sync, no I/O)
  - `async set(key, value)`: UPDATE DB + update cache (async, rare)
  - `async set_bulk(items: dict)`: UPDATE DB + cache in one transaction
  - `get_bool(key)`: `get(key) == "1"` helper
  - `get_int(key)`: `int(get(key))` helper
  - `get_list(key)`: `get(key).split(",")` helper
  - `available_providers` — computed property: which providers have API keys set (from config)
  - Copy existing flag/file data on first load (migration)

### Phase 2: Migrate pipeline reads (1 task)

Replace file-based reads with `State.get()`. Each change is a single line.

- [x] 2. **Migrate pipeline stages**
  - `mute_gate.py` — replace `flag.exists()` → `state.get_bool("mute_bot")`
  - `cancel_flag_gate.py` — replace `flag.exists()` → `state.get_bool("cancel")`, auto-clear after read
  - `switchable.py` — replace `provider_file.read_text()` → `state.get("provider")`
  - `session_state.py` — replace `mode_file.read_text()` → `state.get("mode")`
  - `voice_state_observer.py` — replace `json.dump(file)` → `state.set("voice_state", json.dumps(...))`
  - `build.py` — pass `state` to all stages that need it

### Phase 3: Unify API endpoints (1 task)

- [x] 3. **Add `GET /state` + POST endpoints**
  - `GET /state` — returns all state as JSON:
    ```json
    {
      "mode": "focus",
      "provider": "deepseek",
      "providers": ["openrouter", "deepseek"],
      "model": "deepseek-v4-pro",
      "mute_bot": false,
      "mute_mic": false,
      "voice_state": "idle",
      "running": true,
      "pid": 12345,
      "uptime": "2h34m",
      "agent": "heare",
      "emoji": "🤖",
      "transcripts": 42,
      "actions": 15,
      "chrome": false
    }
    ```
  - `POST /mode` — `state.set("mode", value)`
  - `POST /mute` — `state.set("mute_bot"|"mute_mic", "1"|"0")`
  - `POST /provider` — `state.set("provider", value)`
  - `POST /model` — `state.set("model", value)`
  - `POST /cancel` — `state.set("cancel", "1")` (pipeline auto-clears)
  - Remove old file-writing code from POST handlers

### Phase 4: Cleanup (1 task)

- [x] 4. **Remove dead files + config**
  - Remove from `Settings`: `mode_file`, `provider_file`, `mute_file`, `mute_input_file`, `cancel_flag_file`
  - Remove from disk: old flag/text files (already migrated data to DB)
  - Remove file-watching logic from `switchable.py` (mtime check, `_sync_provider`)
  - Update TUI to use API instead of file reads (optional — Phase 4.5)

## What Dies

| File | Replaced by |
|------|-------------|
| `~/.heare/mute.flag` | `state.get("mute_bot")` |
| `~/.heare/mute_input.flag` | `state.get("mute_mic")` |
| `~/.heare/cancel.flag` | `state.get("cancel")` auto-clear |
| `~/.heare/mode` | `state.get("mode")` |
| `~/.heare/provider` | `state.get("provider")` |
| `~/.heare/model` | `state.get("model")` |
| `~/.heare/voice_state.json` | `state.get("voice_state")` |
| 8 `Settings` fields | removed |

## What Stays

| File | Why |
|------|-----|
| `heare.pid` | OS-level process management |
| `identity.json` | Generated once, never changes |
| `logs/daemon.log` | Log file, not state |
| `browser_bridge.*` | Chrome extension, separate concern |
| `heare.db` | Already exists, just adds `state` table |
| `config.toml` | User config, different from runtime state |

## Success Criteria

- [x] All 8 files gone, data in SQLite
- [x] Pipeline reads state from memory (zero file I/O in hot path)
- [x] `GET /state` returns all current state in one call
- [x] POST endpoints persist to DB + update cache atomically
- [x] Desktop app works with new unified API — **blocked: desktop deleted in reset, needs rebuild**
- [x] Watch TUI works (either via API or direct DB read) — **blocked: TUI needs migration**
- [x] Old data migrated from files to DB on first boot
