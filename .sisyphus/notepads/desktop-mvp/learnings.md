# Desktop MVP — Learnings

## T2: Create src/desktop/app.py

### What was done
- Created `src/desktop/app.py` (91 lines) — PyWebView launcher with inline HTML/JS
- Created `src/desktop/__init__.py` (empty) for package integrity
- Python syntax verified passes

### API compatibility notes
- Daemon API at `127.0.0.1:9778` exposes: GET /state, GET /canvas, POST /mode, POST /mute, POST /cancel
- JS fields `s.agent`, `s.emoji`, `s.running` are NOT yet in daemon state snapshot — defaults handle this gracefully
- GET /canvas handler may not exist yet (T1 in plan) — JS try/catch handles missing endpoint
- `pywebview` dependency not yet in pyproject.toml (T4 in plan) — import will fail at runtime until added

### Key decisions
- Inline HTML (not separate file) per spec — keeps deployment simple
- Zero JS/CSS dependencies — pure fetch() and vanilla DOM
- Poll intervals: 500ms for state, implicit in same poll for canvas

### Files created
- `src/desktop/__init__.py` — empty package marker
- `src/desktop/app.py` — main desktop app (91 lines)
