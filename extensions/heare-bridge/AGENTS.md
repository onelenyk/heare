# extensions/heare-bridge/

MV3 Chrome extension that bridges the browser to the heare daemon via WebSocket.

## STRUCTURE

```
heare-bridge/
├── manifest.json          # MV3, permissions: tabs, scripting, activeTab, storage, offscreen
├── background.js          # Service worker — WebSocket lifecycle, RPC dispatch, badge state
├── offscreen.js           # Offscreen document — owns persistent WebSocket, reconnection
├── content_script.js      # Page reading/interaction
├── popup.html / popup.js  # Status display + pair code entry
├── options.html / options.js  # Settings: token, port, pair code
└── icons/                 # Extension icons
```

## TOOLS (exposed to LLM via `browser_bridge.py`)

| Tool | Function |
|------|----------|
| `list_browser_tabs` | List all open tabs |
| `read_browser_page` | Read current tab content |
| `click_in_browser` | Click element by CSS selector |
| `fill_in_browser` | Fill form field by CSS selector |
| `navigate_browser` | Load URL in tab |
| `extract_in_browser` | Extract DOM by CSS selector |
| `open_browser_tab` | Open new tab |
| `activate_browser_tab` | Bring tab to foreground |

## PROTOCOL

- WebSocket at `ws://127.0.0.1:9333`
- Token authentication (single client — second connection gets close code 4002)
- Offscreen document owns the persistent WebSocket
- Reconnection with exponential backoff + ping/pong keep-alive
- Wire protocol v1

## GOTCHAS

- Extension must be sideloaded (not in Chrome Web Store): `chrome://extensions` → Load unpacked
- Content script injection requires `activeTab` permission (user gesture)
- Options page stores token + port in `chrome.storage.sync`
- Pair code flow: daemon generates code → user enters in extension popup → bridge activates
