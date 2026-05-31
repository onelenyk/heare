# Heare Desktop App — MVP Plan

## TL;DR

> **Quick Summary**: Ultra-light desktop app using PyWebView. Polls daemon API for state and canvas. Buttons for mute/mode/provider. Zero JS framework. One pip dependency. ~250 lines total.
>
> **Deliverables**:
> - `src/desktop/app.py` — Python launcher, inline HTML/JS
> - `src/api.py` — add `GET /canvas` endpoint
> - `src/main.py` — add `heare desktop` command
> - `pyproject.toml` — add `pywebview` dependency
>
> **Estimated Effort**: Tiny (~250 lines, 3 files touched)

---

## Architecture

```
  Daemon (:9778)                Desktop App (PyWebView)
  ─────────────                 ────────────────────────
  GET  /state     ──── poll ──► status bar (mode, provider, mute)
  GET  /canvas    ──── poll ──► canvas div (HTML render)
  POST /mode      ◄── button ── mode switcher
  POST /mute      ◄── button ── mute mic / mute bot
  POST /cancel    ◄─── hotkey ─ Ctrl+C or esc
```

Python side: minimal. Just launch PyWebView, load inline HTML.
JavaScript side: polls every 500ms, renders state, sends POST on button clicks.
All communication is `fetch()` to `http://127.0.0.1:9778`.

---

## TODOs

### - [x] 1. Add `GET /canvas` to daemon API
  **File**: `src/api.py` — add route that returns latest canvas from `displays` table where `content_type='canvas/html'` and `rendered=0`.
  - `GET /canvas` → `{"html": "...", "ts": 123, "title": "..."}`
  - Mark as `rendered=1` on read (so desktop doesn't re-render old content)

### - [x] 2. Create `src/desktop/app.py` — PyWebView launcher with inline HTML
  **File**: new — launches webview window with built-in HTML/JS
  - Python: `webview.create_window("heare", html=HTML, js_api=Api())`
  - Inline HTML: status bar, controls, canvas div, activity area
  - JS: `setInterval` polls `/state` and `/canvas`, renders UI
  - Buttons send `POST /mode`, `POST /mute`
  - Canvas div renders `innerHTML` from poll response

### - [x] 3. Add `heare desktop` CLI command
  **File**: `src/main.py` — add `desktop` subcommand that imports and runs app.py
  - `uv run python -m src.main desktop`

### - [x] 4. Add `pywebview` dependency
  **File**: `pyproject.toml` — add `pywebview>=5.0` to dependencies

---

## Verification

```
# Start daemon
heare start

# Launch desktop (separate terminal or same)
heare desktop

# Expected:
# - Window opens with heare branding
# - Status shows: mode, provider, mute state
# - Buttons work: switching mode, toggling mute
# - Canvas renders when LLM outputs [canvas] content
```

## What the window looks like

```
 ┌─────────────────────────────────────────┐
 │  kort ⚡  • active  ambient  deepseek    │
 │  [🔇 mic] [🔇 bot] [silent] [focus]     │
 │─────────────────────────────────────────│
 │                                         │
 │  ┌── canvas ──────────────────────────┐ │
 │  │                                    │ │
 │  │   (renders when LLM uses [canvas])  │ │
 │  │                                    │ │
 │  └────────────────────────────────────┘ │
 │─────────────────────────────────────────│
 │  ▸ user: покажи графік                  │
 │  ◂ kort: ось графік [canvas]           │
 └─────────────────────────────────────────┘
```
