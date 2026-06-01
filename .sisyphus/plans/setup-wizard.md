# Desktop Settings — No Terminal Required

## Goal
Everything configurable from the app UI. No terminal. No `.env` editing.

## Architecture

```
Main UI:                           Settings panel (⚙):
────────                            ──────────────────
┌──────────────────┐               ┌──────────────────┐
│ kort ⚡ running    │               │ ⚙ Settings        │
│ mode: ambient     │               │                  │
│ ...               │  ⚙ button    │ API Keys:        │
│                   │──────────►   │ Groq:   [______] │
│ [silent][focus]   │               │ DeepSeek:[______] │
│ ...               │               │                  │
└──────────────────┘               │ Language: [uk▾]  │
                                    │ Voice: [uk▾]     │
                                    │ Mode: [ambient▾] │
                                    │                  │
                                    │ [Save & Restart]  │
                                    └──────────────────┘
```

First run (no API keys): settings panel shows automatically.
After setup: accessible via ⚙ button in controls.

## API endpoints to add

### `GET /setup/status`
```json
{"configured": false, "groq_key": false, "deepseek_key": false, "language": "uk", "tts_voice": "uk-UA-OstapNeural"}
```

### `POST /settings`
```json
{"groq_api_key": "sk-xxx", "deepseek_api_key": "sk-xxx", "language": "uk", "tts_voice": "uk-UA-OstapNeural"}
```
Saves to `.env` + `config.toml`.

## Desktop changes

### `src/desktop/app.py`
- Add Settings panel HTML (hidden by default, toggled via ⚙ button)
- On load: if not configured → show settings automatically
- "Save & Restart" → POST `/settings` → daemon restart → refresh UI
- Settings panel accessible anytime via button

## TODOs

### Wave 1: Settings API (2 tasks)
- [ ] Add `GET /setup/status` + `POST /settings` to `src/api.py`
- [ ] Add `.env` writer to `src/config.py`

### Wave 2: Settings panel (2 tasks)
- [ ] Add settings panel HTML to `src/desktop/app.py`
- [ ] JS: ⚙ toggle, check status on load, auto-show if not configured
