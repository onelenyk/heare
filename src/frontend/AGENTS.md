# src/frontend/

React 18 + Vite 5 SPA dashboard served at `http://127.0.0.1:9780/`.

## STRUCTURE

```
frontend/
├── package.json        # React 18, Vite 5, @vitejs/plugin-react
├── vite.config.js      # outDir: dist, port: 5173 (dev)
├── index.html
└── src/
    ├── main.jsx        # Entry point
    ├── App.jsx         # Root component, API base URL, polling
    ├── styles.css
    └── components/     # 22 React components
        ├── Dashboard.jsx       # Main layout + polling hub (581 lines)
        ├── ControlsCard.jsx    # Mode toggle, mute buttons
        ├── StatusBar.jsx       # Daemon status + control buttons
        ├── UserVoiceBar.jsx    # VAD state visualization
        ├── AgentStatusBar.jsx  # Agent speaking/idle state
        ├── HistoryPanel.jsx    # Transcript viewer
        ├── MemoriesCard.jsx    # Memory browser
        ├── AudioPanel.jsx      # Audio device/settings
        ├── InjectPanel.jsx     # Text injection
        ├── SettingsPanel.jsx   # Config editor (no keys — see KeysCard)
        ├── KeysCard.jsx        # The one place a key is typed
        ├── UsageCard.jsx       # Cost display
        ├── DisplayCard.jsx     # Canvas output viewer
        ├── AgentsPanel.jsx     # Sub-agent management
        ├── BridgeModal.jsx     # Browser bridge pairing
        ├── ToolsModal.jsx      # Tool list
        ├── PromptManager.jsx   # Prompt template editor
        ├── SetupModal.jsx      # Onboarding wizard
        └── Toast.jsx           # Notifications
```

## CONNECTIVITY

- **No WebSocket** — pure HTTP polling
- Polls `/state`, `/activity`, `/display`, `/api/agents` every 1s
- Commands via POST to REST endpoints
- API base: `http://127.0.0.1:9780` (hardcoded in `App.jsx`)

## BUILD

```bash
cd src/frontend && npm ci && npm run build
make frontend   # From project root
```

## GOTCHAS

- No ESLint, Prettier, or TypeScript — pure JSX. Follow existing naming (PascalCase components, camelCase functions)
- Dashboard.jsx is 581 lines — it's the largest frontend file but naturally so (it orchestrates all panels)
- `Dashboard.jsx` uses `mergeActivity()` to de-duplicate polls — don't break the ID-based merge
