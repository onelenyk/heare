# Plan — Agent Overlay UI

Status: implemented (Ralph iteration 1, 2026-05-12)
Date: 2026-05-12
Owner: onelenyk

## Retro (after implementation)

Final shape matched the plan with one minor drift: the cancel-flag gate
was placed **after** `input_mute_gate` (not "before audio_event_observer"
specifically) — same net behaviour, cleaner comment. The single-file
`request_cancel` helper (added to `cancel_flag_gate.py`) makes the
server-side code one line instead of touching `Path` machinery directly.

Effort actuals: ~280 LOC + 13 tests (5 cancel-gate, 8 server). One
afternoon, as estimated.

What I cannot verify in this sandbox: the actual pywebview window
opening on the host's display. The headless smoke test
(`from src.overlay.server import create_app`) and all 13 unit tests pass.
First-launch visual verification is on the user.

## Goal

Ship an always-on-top desktop window that surfaces the live agent state, the
streaming transcript, a small set of quick actions, and a debug feed — without
introducing a parallel transport or new pipeline coupling. The overlay is a
read-mostly client of the existing file-based observer contract; writes go
through the same flag-file mechanism the watch dashboard already uses.

## Non-goals

- No new always-on transport (no WebSocket server, no SSE) in v1.
- No provider switching (deferred — `SwitchableLLMService` already writes
  `provider_file`; can be added later behind the same UI shell).
- No remote / multi-host access. Local-only.
- No new auth or pair-code surface. Localhost binding only.

## Existing pieces this leans on

| Producer | Sink (file) | Read by |
|---|---|---|
| `VoiceStateObserver` (`src/pipeline/stages/voice_state_observer.py`) | `settings.voice_state_file` | overlay |
| `AudioEventObserver` (`src/audio_event/observer.py`) | `settings.audio_event_file` | overlay |
| `assistant_response_logger` | `log_dir/*.jsonl` (assistant turns) | overlay (debug pane) |
| `mute_gate` (output) | reads `settings.mute_file` | overlay writes/removes |
| `input_mute_gate` (mic) | reads `settings.mute_input_file` | overlay writes/removes |

All four files use atomic `os.replace` (writes) or simple `Path.exists()`
(flags). The overlay just reads JSON on a poll tick and toggles flag files
for actions. No pipeline code changes required for mute/unmute.

## New pieces

### 1. Cancel-turn flag (new, mirrors mute pattern)

Cancellation today is internal to the pipeline: `TranscriptionGate` detects a
spoken stop-word and pushes `InterruptionFrame` upstream. The overlay needs the
same effect without speaking.

Add a tiny pass-through `FrameProcessor` near the front of the pipeline that
polls `settings.cancel_flag_file` (e.g. `~/.heare/cancel.flag`) on each frame
tick. When the flag exists:

1. delete the flag,
2. push `InterruptionFrame` upstream (same call site as
   `TranscriptionGate._maybe_cancel`).

This is symmetric with `mute_gate` / `input_mute_gate` — flag-file in, frame
out. The overlay creates the file; the pipeline consumes it. ~30 lines.

### 2. Overlay process (new package `src/overlay/`)

```
src/overlay/
  __init__.py
  __main__.py     # python -m src.overlay
  app.py          # pywebview window: frameless, on_top, transparent
  server.py       # FastAPI bound to 127.0.0.1:<random or fixed local port>
  static/
    index.html
    overlay.css
    overlay.js
```

Two processes? **No — one process.** `pywebview` runs the GUI thread; FastAPI
runs on a `uvicorn` task in a background thread. The webview loads
`http://127.0.0.1:PORT/`. This avoids cross-origin headaches and keeps the
deployment a single `python -m src.overlay`.

`server.py` endpoints (all localhost-only):

| Method | Path | Returns / Effect |
|---|---|---|
| GET | `/api/state` | merged JSON: voice_state + last audio_event + mute flags + recent assistant turns |
| POST | `/api/mute/output` `{on: bool}` | touches/removes `settings.mute_file` |
| POST | `/api/mute/input` `{on: bool}` | touches/removes `settings.mute_input_file` |
| POST | `/api/cancel` | touches `settings.cancel_flag_file` |
| GET | `/api/events` (optional, v1.1) | SSE tail of `log_dir/*.jsonl` — only if poll feels laggy |

Settings are read from `src.config.Settings` so paths stay in sync with the
daemon (same `~/.heare/` root).

### 3. UI layout (single HTML file)

```
┌──────────────────────────────────────────────────────┐
│ ● listening                        🎤 🔇 ⏹  ──  × │  ← drag handle + actions
├──────────────────────────────────────────────────────┤
│ "what's the weather like…"                            │  ← partial (italic, dim)
│ "What's the weather like today?"                      │  ← final
│ ▸ Bot: It's 18°C and sunny in Kyiv.                   │
├──────────────────────────────────────────────────────┤
│ ▾ Debug                                                │
│   audio_event: Speech (0.84)                          │
│   provider: openrouter / glm-4.6                      │
│   bridge: connected (pair=AB12)                       │
└──────────────────────────────────────────────────────┘
```

- Status pill: bound to `voice_state.state` (`idle/listening/stt/result`).
- Transcript: scrolling list, partial + finals from `voice_state.last_partial`
  and the assistant turn log.
- Three action buttons (mic mute, output mute, cancel) — each fires a POST
  and optimistically flips the visual.
- Debug accordion: collapsed by default; shows audio_event, browser-bridge
  status (read from existing bridge status file), token-usage summary.
- Poll cadence: `setInterval(fetch '/api/state', 150)` — under one network
  hop, on the loopback, costs nothing.

### 4. Integration with `hearectl`

Add `hearectl overlay` (start) and `hearectl overlay-stop` subcommands.
Same PID-file pattern (`$HEARE_HOME/overlay.pid`) as the daemon.

The overlay does **not** require the daemon to be running — it simply renders
"daemon offline" if `voice_state_file` is missing or stale (>10 s since
`since_ts`). This means it can be left open across daemon restarts.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `pywebview` on macOS needs `pyobjc` and runs on the main thread | Document in `pyproject.toml` as an optional extra (`heare[overlay]`); `__main__.py` checks for it and prints install hint. |
| Poll-tick cost when overlay is hidden | Pause polling on `visibilitychange` → `hidden`. |
| Flag-file race on rapid cancel clicks | The new cancel-flag observer deletes the flag *before* pushing `InterruptionFrame`; double-click just no-ops the second time. |
| Overlay style drift from watch dashboard | Both render the same state files — divergence is visual only and acceptable. |

## Acceptance criteria

1. `hearectl overlay` opens a frameless, always-on-top window in <2 s.
2. With the daemon running, status pill reflects speaking/listening within
   ~200 ms (one poll tick).
3. Clicking 🎤 toggles `mute_input_file`; daemon stops processing mic audio
   within one frame (visible in `voice_state` going to `idle`).
4. Clicking ⏹ during a bot reply cuts TTS within ~100 ms (existing
   `_TtsFadeOnInterruption` path).
5. Overlay survives daemon restart — shows "offline" then auto-recovers.
6. New cancel-flag observer has a unit test (same pattern as
   `tests/test_mute_gate.py` if present).

## Out of scope (v2 ideas)

- Provider switch dropdown.
- Live waveform / VAD bar.
- Click-through transparent overlay (cosmetic).
- WebSocket upgrade if file-poll latency becomes the bottleneck.
- Multi-monitor / saved position.

## Effort

Single afternoon for the happy path:
- ~60 LOC: `cancel_flag_gate.py` + wiring in `pipeline/build.py`.
- ~40 LOC: `src/overlay/server.py`.
- ~30 LOC: `src/overlay/app.py`.
- ~150 LOC: `static/` (HTML + CSS + JS).
- ~30 LOC: `hearectl` subcommands.
- 1 unit test for the cancel-flag observer.

Total: ~310 LOC + 1 test + 1 optional dep group.
