# Browser Extension Bridge

**Status:** REVISION 2 -- awaiting Architect/Critic re-review
**Created:** 2026-05-09
**Complexity:** HIGH
**Security sensitivity:** HIGH (agent gains read access to user's signed-in browser sessions)

---

## RALPLAN-DR Summary

### Principles

1. **Least privilege by default** -- the extension ships with `<all_urls>` for capability but the daemon's tool surface gates what the agent can actually do. The extension's content script dispatcher rejects `chrome://`, `chrome-extension://`, and `file://` URLs before injection. Future work can add a per-domain allowlist in the extension popup without protocol changes.
2. **Fail-open for UX, fail-closed for security** -- a missing extension or dropped WS returns a clear, actionable, structured error to the LLM (not a hang); an invalid auth token is rejected immediately and surfaced on the dashboard. All disconnect/timeout errors include `retryable: bool` so the LLM knows whether to retry or escalate.
3. **Protocol-first design** -- the WS wire protocol defines more methods than Phase 1 tools expose; new tools can be added without re-versioning the extension or protocol. All messages include `"v": 1` for future migration.
4. **Single-connection simplicity** -- the daemon accepts exactly one extension connection at a time; no multi-browser fanout, no tab multiplexing beyond what Chrome APIs already provide. Only one Chrome profile's extension can hold the bridge connection; others get close code 4002.
5. **Observable by default** -- every RPC call is logged to `daemon.log` with method, tab, elapsed time, and truncated result; the dashboard shows extension-connected state at a glance. Bridge tools registered in `TOOLS` dict are auto-logged via `_make_handler`'s `record_action_pending` / `record_action_result` wrappers in `src/agent/tools/schemas.py`.

### Decision Drivers (top 3)

1. **Security surface vs. capability** -- the extension can read Gmail, banking, etc. The token-gated localhost WS, audit logging, and opt-in-per-session toggle are the mitigations. Accepting this tradeoff is the user's explicit choice.
2. **Cross-platform longevity** -- must work identically on macOS and Linux; must not depend on platform-specific IPC. WebSocket over localhost satisfies this.
3. **MV3 service worker constraints** -- Chrome suspends idle service workers after ~30s. The WS connection must survive suspension or reconnect transparently. This is the hardest technical constraint.

### Viable Options

#### Option A: Browser Extension MV3 + WebSocket bridge (CHOSEN)

- Extension background service worker opens a WS to `ws://localhost:9333`.
- Daemon side runs an async WS server (`websockets` library, already a dependency).
- Content scripts injected on-demand via `chrome.scripting.executeScript`.

**Pros:** Pure JS + Python; no native binary compilation. `websockets` already in `pyproject.toml`. Works on macOS + Linux identically. Sideload is one-time drag-and-drop. Bidirectional: daemon can push events to extension.

**Cons:** MV3 service worker suspension kills the WS socket. Mitigation: `chrome.alarms` keepalive (fires every 25s), reconnect-on-wake with exponential backoff. `<all_urls>` permission is broad (sideload-only). Token handshake requires a one-time manual copy-paste step.

#### Option B: Browser Extension MV3 + Native Messaging host

**Pros:** No open port on localhost (smaller attack surface). Chrome manages host lifecycle; no keepalive needed. Auth is implicit.

**Cons:** Per-OS native messaging host manifest installed in Chrome-specific directories (2 paths: macOS `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/`, Linux `~/.config/google-chrome/NativeMessagingHosts/`). Per-OS manifest maintenance cost across 4 install paths (macOS + Linux x Chrome + Chromium). Uni-directional initiation blocks push-based features. Adds a separate process alongside the daemon.

**Why not chosen:** Per-OS manifest install adds fragile, multi-path maintenance burden (4 install paths across macOS/Linux x Chrome/Chromium). Uni-directional initiation blocks future push-based features. The 1 MB message limit is irrelevant for Phase 1 (all text payloads <50 KB) but constrains Phase 2 screenshots. The security benefit (no open port) is marginal when the port is localhost-only and token-gated.

**Native Messaging remains a candidate for Phase 2 if the localhost WS surface proves too broad for enterprise users.** It eliminates the MV3 service worker problem entirely and has a categorically stronger security posture. If WS proves problematic, migration is feasible since the wire protocol is transport-agnostic.

#### Option C: AppleScript JS-injection (REJECTED) -- macOS-only, no tab management, no cross-platform path.

#### Option D: Isolated Chrome via Playwright/CDP (REJECTED) -- Chrome 136+ blocks CDP attach; profile copying is slow, stale, risks corruption. Violates "real browser" requirement.

### Pre-mortem (deliberate mode)

**Scenario 1: Token leak via extension storage.** `chrome.storage.local` is readable by any extension with the same ID. *Mitigation:* single-connection policy (daemon rejects second WS), persistent token with explicit rotation via CLI, dashboard warning on unexpected disconnect.

**Scenario 2: Localhost port hijack by a local process.** *Mitigation:* daemon validates token on first message (256-bit entropy); without it the connection is dropped. Binds `127.0.0.1` only. Origin header validation rejects WS handshake unless `Origin` matches `chrome-extension://<extension-id>`.

**Scenario 3: Agent exfiltrates sensitive page content.** *Mitigation:* Phase 1 logs every RPC call with tab URL and truncated result. Phase 2 adds domain blocklist. Tool descriptions warn the LLM not to read financial or medical pages unless asked.

### Expanded Test Plan (deliberate mode)

**Unit** (`tests/test_browser_bridge.py`): auth, correlation, timeout (`retryable`), disconnect, malformed, single-connection, Origin validation, protocol version.
**Integration** (`tests/integration/test_browser_bridge_e2e.py`): Mock extension WS client, full RPC round-trip, token persistence, Origin validation.
**E2E** (manual, Step 6): Install extension, paste token, verify dashboard, test all 7 tools against concrete URLs.
**Observability:** Every RPC logged. Dashboard green/grey dot. Auto-logged via `_make_handler`.

---

## Context

The heare daemon is a Pipecat-based voice assistant that uses direct tools (defined in `src/agent/tools/registry.py`, dispatched by `src/agent/tools/direct.py`, schema'd in `src/agent/tools/schemas.py`) to interact with the local machine. The user wants the agent to be able to read and interact with their real, signed-in Chrome browser.

Chrome 136+ blocks CDP attachment to the default user-data-dir, making the existing `src/daemon/browser.py` CDP launcher insufficient. The chosen architecture is a Manifest V3 Chrome extension communicating over a token-authenticated WebSocket to a bridge server running inside the daemon process.

### Relevant existing code

- **Tool registry:** `src/agent/tools/registry.py` -- `Tool` dataclass, `TOOLS` dict, `register_dynamic_tool()`.
- **Tool schemas:** `src/agent/tools/schemas.py` -- `_TOOL_SPECS` dict, `register_all_tools()`, `_make_handler` wrappers (`record_action_pending` / `record_action_result`).
- **Tool dispatch:** `src/agent/tools/direct.py` -- `execute_direct()` routes by tool name.
- **Config:** `src/config.py` -- `Settings` dataclass, `load_settings()` reads `~/.heare/config.toml`.
- **Dashboard:** `src/watch/widgets.py` -- `BrowserSection`, `ControlsBar.update_chrome_status()`.
- **Daemon lifecycle:** `src/main.py` -- `_cmd_start()` assembles pipeline, `run_until_stopped(runner, pipeline, warmup=None, *, namer_task=None)` manages background tasks.
- **Existing browser code:** `src/daemon/browser.py` -- stays as-is (CDP launcher still useful for dev/debug).
- **Dependencies:** `websockets>=12.0` already in `pyproject.toml`.

---

## Work Objectives

Build a working browser bridge that lets the agent's direct tools read pages, list tabs, click elements, fill forms, and navigate in the user's real Chrome browser via a sideloaded MV3 extension.

---

## Guardrails

### Must Have
- Token-authenticated WebSocket on localhost only (`127.0.0.1`).
- Persistent token in `config.toml` (generated on first use, rotatable via CLI).
- Origin header validation on WS handshake.
- Clean, structured error messages with `retryable` flag when extension is not connected.
- Dashboard shows extension-connected state.
- At least 7 direct tools working end-to-end in Phase 1.
- Unit tests with mock WS client covering auth, correlation, timeout, disconnect.
- Audit log of every RPC call in `daemon.log`.
- URL blocklist in extension content script dispatcher (`chrome://`, `chrome-extension://`, `file://`).

### Must NOT Have
- No `0.0.0.0` binding.
- No hardcoded secrets -- token generated via `secrets.token_urlsafe(32)`.
- No Chrome Web Store distribution -- sideload only.
- No Firefox support in this plan.
- No `eval()` or arbitrary JS execution tool in Phase 1.
- No changes to the existing CDP launcher (`src/daemon/browser.py`).

---

## Task Flow

```
[Step 1: Wire protocol + bridge server]
         |
[Step 2: Chrome extension]
         |
[Step 3: Direct tools + registry]
         |
[Step 4: Dashboard + config + lifecycle]
         |
[Step 5: Tests]
         |
[Step 6: Manual smoke test + docs]
```

---

## Detailed TODOs

### Step 1: Wire Protocol and Bridge Server

**Files to create:** `src/agent/browser_bridge.py`

**Wire protocol (JSON over WebSocket):**

All messages include `"v": 1` for protocol versioning.

Request (daemon -> extension):
```json
{"v": 1, "id": "uuid", "type": "request", "method": "read_page", "params": {"tab_id": 123}}
```

Response (extension -> daemon):
```json
{"v": 1, "id": "uuid", "type": "response", "ok": true, "result": {"title": "...", "text": "..."}}
```

Error response:
```json
{"v": 1, "id": "uuid", "type": "response", "ok": false, "error": {"code": "SELECTOR_NOT_FOUND", "message": "..."}}
```

Auth handshake (extension -> daemon, first message after WS open):
```json
{"v": 1, "type": "auth", "token": "<token-value>"}
```

Auth result (daemon -> extension):
```json
{"v": 1, "type": "auth_result", "ok": true}
```

**Full method list (bridge protocol):**

| Method | Params | Result | Phase |
|---|---|---|---|
| `list_tabs` | `{}` | `{tabs: [{id, url, title, active}]}` | 1 |
| `read_page` | `{tab_id?}` | `{url, title, text, html?}` | 1 |
| `click` | `{tab_id?, selector}` | `{clicked: bool}` | 1 |
| `fill` | `{tab_id?, selector, value}` | `{filled: bool}` | 1 |
| `navigate` | `{tab_id?, url}` | `{url, title}` | 1 |
| `extract` | `{tab_id?, selector}` | `{elements: [{tag, text, attrs}]}` | 1 |
| `open_tab` | `{url}` | `{tab_id, url, title}` | 1 |
| `close_tab` | `{tab_id}` | `{closed: bool}` | 2 |
| `screenshot` | `{tab_id?}` | `{data_url: "data:image/png;base64,..."}` | 2 |
| `wait_for` | `{tab_id?, selector, timeout_ms?}` | `{found: bool}` | 2 |
| `scroll_into_view` | `{tab_id?, selector}` | `{scrolled: bool}` | 2 |
| `execute_script` | `{tab_id?, code}` | `{result: any}` | NEVER (security) |

**Bridge server behavior:**
- Binds to `127.0.0.1:<port>` (default 9333).
- **Token lifecycle:** reads `browser_bridge_token` from `config.toml` via `Settings`. On first use (field is empty/absent), generates a 32-byte token via `secrets.token_urlsafe(32)`, writes it back to `config.toml` under `[browser_bridge]`, and writes a convenience copy to `~/.heare/browser_bridge.token` (mode 0o600) for easy `cat`-and-paste. Token persists across daemon restarts. Explicit rotation via `heare rotate-browser-token` CLI subcommand.
- **Origin header validation:** on WS handshake, checks `Origin` header. Rejects connections unless origin is `chrome-extension://` prefixed. Logs rejected origins at WARNING level.
- Accepts at most one authenticated connection. Second client gets close code 4002 ("already connected").
- First message must be `{"type": "auth", "token": "..."}`. Wrong token -> close code 4001.
- After auth, accepts RPC requests via `bridge.call(method, params) -> result`.
- Pending requests tracked in `dict[str, asyncio.Future]` keyed by request ID.
- Default RPC timeout: 5s. `screenshot` and `wait_for` use 15s.
- **Structured error contract for all disconnect/timeout scenarios:**
  - Connection drop (mid-RPC): `{"success": false, "error": "Browser extension disconnected while processing request. Try again in a few seconds.", "retryable": true}`
  - Timeout (no response): `{"success": false, "error": "Browser extension timed out. The request may still be processing.", "retryable": true}`
  - MV3 suspension (WS closes, extension reconnects later): `{"success": false, "error": "Browser extension temporarily disconnected (Chrome suspended the connection). Try again in a few seconds.", "retryable": true}`
  - Not connected (no extension at all): `{"success": false, "error": "Browser not connected. Install the Heare Bridge extension from extensions/heare-bridge/ via chrome://extensions.", "retryable": false}`
  - Auth failure: `{"success": false, "error": "Browser extension authentication failed. Check the bridge token.", "retryable": false}`
- Exposes `bridge.connected: bool` property for the dashboard.
- Logs every RPC call: `browser_bridge rpc method=<m> tab=<id> elapsed_ms=<n> ok=<bool>`.

**Acceptance criteria:**
- [ ] `BrowserBridge` class with `start()`, `stop()`, `call(method, params)`, `connected` property.
- [ ] Token read from `Settings.browser_bridge_token`; generated and persisted to `config.toml` on first use.
- [ ] Convenience token file written to `~/.heare/browser_bridge.token` (mode 0o600) on start.
- [ ] Origin header validated on WS handshake (reject non-`chrome-extension://` origins).
- [ ] Single-connection enforcement with close code 4002.
- [ ] Auth validation with close code 4001 on failure.
- [ ] Request/response correlation via UUID `id` field.
- [ ] All wire messages include `"v": 1`.
- [ ] All disconnect/timeout/not-connected errors return structured `{success, error, retryable}`.
- [ ] Audit logging of every RPC call.

### Step 2: Chrome Extension (MV3)

**Files to create:**
- `extensions/heare-bridge/manifest.json`
- `extensions/heare-bridge/background.js` (service worker)
- `extensions/heare-bridge/content_script.js`
- `extensions/heare-bridge/options.html` + `options.js` (token entry)
- `extensions/heare-bridge/icons/` (16, 48, 128 px placeholders)

**`manifest.json` permissions:** `tabs`, `scripting`, `activeTab`, `storage`, `alarms`, `<all_urls>` host permission.

**URL blocklist in content script dispatcher:**
Before injecting any content script, `background.js` checks the target tab URL. If it matches `chrome://`, `chrome-extension://`, or `file://`, the handler returns an error immediately without injection:
```javascript
const BLOCKED_SCHEMES = ['chrome://', 'chrome-extension://', 'file://'];
if (BLOCKED_SCHEMES.some(s => tab.url.startsWith(s))) {
  return {ok: false, error: {code: 'BLOCKED_URL', message: `Cannot access ${tab.url.split('://')[0]}:// pages`}};
}
```

**MV3 service worker keep-alive strategy:**
- `chrome.alarms.create("keepalive", {periodInMinutes: 0.4})` (24s interval).
- On alarm fire: if WS closed, attempt reconnect. If WS open, send `{"v": 1, "type": "ping"}`. Daemon responds with `{"v": 1, "type": "pong"}`.
- If service worker is suspended mid-RPC, WS socket closes. On wake, alarm fires, reconnects. Daemon-side future times out and returns structured error with `retryable: true`. The LLM retries rather than asking the user to reinstall.

**All messages include `"v": 1`** in their JSON payload.

**`background.js` behavior:**
- On install/update, read token from `chrome.storage.local`.
- If token set, open WS to `ws://localhost:<port>` (port from storage, default 9333).
- On WS open, send auth. On auth success, badge green. On auth failure, badge red, clear token.
- On WS message with `type: "request"`, dispatch to handler, send response.
- Handlers use `chrome.tabs.query`, `chrome.scripting.executeScript`, `chrome.tabs.update`, `chrome.tabs.create`.
- `read_page`: inject content script returning `{url, title, text: document.body.innerText}`. Limit `text` to 50,000 characters.
- `click`, `fill`, `extract`: inject scripts with CSS selectors (no arbitrary JS).
- `navigate`: `chrome.tabs.update(tabId, {url})`, wait for `chrome.webNavigation.onCompleted` with timeout.
- `list_tabs`: `chrome.tabs.query({})`.
- `open_tab`: `chrome.tabs.create({url})`.

**`options.html` + `options.js`:** Simple form for token + port. Shows current connection status. On save, triggers reconnect via `chrome.runtime.sendMessage`.

**Acceptance criteria:**
- [ ] Extension loads via `chrome://extensions` -> Load unpacked.
- [ ] Options page accepts and persists token + port.
- [ ] Background service worker connects, authenticates, maintains connection.
- [ ] All 7 Phase 1 methods implemented and returning correct results.
- [ ] Badge: green (connected), red (auth failure), grey (disconnected).
- [ ] Keepalive alarm prevents suspension during active use.
- [ ] Reconnect on WS close with exponential backoff (1s, 2s, 4s, 8s, max 30s).
- [ ] URL blocklist rejects `chrome://`, `chrome-extension://`, `file://` URLs before content script injection.
- [ ] All messages include `"v": 1`.

### Step 3: Direct Tools and Registry

**Files to edit:** `src/agent/tools/registry.py`, `src/agent/tools/schemas.py`, `src/agent/tools/direct.py`.

**Phase 1 tools (7):**

| Tool name | Args | Returns |
|---|---|---|
| `read_browser_page` | `{tab_id?: int}` | `{url, title, text}` |
| `list_browser_tabs` | `{}` | `{tabs: [{id, url, title, active}]}` |
| `click_in_browser` | `{selector: str, tab_id?: int}` | `{clicked: bool}` |
| `fill_in_browser` | `{selector: str, value: str, tab_id?: int}` | `{filled: bool}` |
| `navigate_browser` | `{url: str, tab_id?: int}` | `{url, title}` |
| `extract_in_browser` | `{selector: str, tab_id?: int}` | `{elements: [{tag, text, attrs}]}` |
| `open_browser_tab` | `{url: str}` | `{tab_id, url, title}` |

**Implementation pattern in `direct.py`:**
Each handler calls `_get_bridge()` -> singleton `BrowserBridge` or raises. Then `await bridge.call(method, params)`. The bridge's structured error contract (from Step 1) propagates directly: all errors include `{success, error, retryable}`. When bridge is not connected, returns `{"success": false, "error": "Browser not connected. Install the Heare Bridge extension...", "retryable": false}`.

The bridge singleton is set during daemon startup (Step 4) via a module-level `set_bridge(bridge)` function.

**Acceptance criteria:**
- [ ] All 7 tools registered in `TOOLS` dict with `execution="direct"`.
- [ ] All 7 tools have schema entries in `_TOOL_SPECS` with correct properties and required fields.
- [ ] All 7 tools dispatch through `execute_direct()` to the bridge.
- [ ] All error responses include `retryable` field (from bridge error contract).
- [ ] Tools appear in `get_tool_descriptions()` output for LLM prompt injection.

### Step 4: Dashboard, Config, and Lifecycle Integration

**Files to edit:** `src/config.py`, `src/main.py`, `src/watch/widgets.py`, `src/watch/data.py`.

**Config additions to `Settings`:**
```python
browser_bridge_enabled: bool = True
browser_bridge_port: int = 9333
browser_bridge_token: str = ""  # generated on first use if empty
```

Loaded from `[browser_bridge]` section in `config.toml`. When `browser_bridge_token` is empty, `BrowserBridge.start()` generates a token, writes it back to `config.toml` under `[browser_bridge]`, and writes the convenience copy to `~/.heare/browser_bridge.token`.

**CLI subcommand: `heare rotate-browser-token`:**
Add a `_cmd_rotate_browser_token` function in `src/main.py` (registered as a subcommand). It generates a new token via `secrets.token_urlsafe(32)`, writes it to `config.toml` under `[browser_bridge] token = "..."`, writes the convenience file, and prints instructions to paste the new token into the extension options page. If the daemon is running, it prints a note that the daemon must be restarted for the new token to take effect.

**Lifecycle integration in `_cmd_start()` -- `run_until_stopped` with `bridge_task`:**

Update `run_until_stopped` signature to accept `bridge_task`:
```python
async def run_until_stopped(
    runner, pipeline, warmup=None, *, namer_task=None, bridge_task=None,
) -> None:
```

Follow the exact `namer_task` pattern (lines 364-375 of current `src/main.py`):
- Add `bridge_task` to `watch_set` at line 365 (if not None).
- Add `bridge_task` to `background_tasks` list in `finally` block at line 374.
- Add `(bridge_task, "browser-bridge")` to `named_tasks` for shutdown logging at line 383.

In `_cmd_start()`, if `settings.browser_bridge_enabled`:
1. Create `BrowserBridge(port=settings.browser_bridge_port, token=settings.browser_bridge_token)`.
2. Call `set_bridge(bridge)` on the direct tools module.
3. `bridge_task = asyncio.create_task(bridge.start())`.
4. Pass `bridge_task=bridge_task` to `run_until_stopped()`.
5. In the `finally` block of `_cmd_start()`, call `await bridge.stop()`.

**Dashboard updates:**
- `BrowserSection` shows extension connection state (green dot = connected, grey dot = not).
- `data.py`: add `bridge_connected(settings) -> bool` that reads `~/.heare/browser_bridge.status` JSON (written by the bridge, same pattern as `voice_state.json`).

**Acceptance criteria:**
- [ ] `browser_bridge_enabled`, `browser_bridge_port`, `browser_bridge_token` in Settings.
- [ ] `heare rotate-browser-token` CLI subcommand generates new token, writes to `config.toml` and convenience file.
- [ ] `run_until_stopped` accepts `bridge_task` kwarg following the `namer_task` pattern exactly.
- [ ] Bridge starts as a background task in `_cmd_start()` when enabled.
- [ ] Bridge stops cleanly on daemon shutdown (convenience token file optionally cleaned).
- [ ] Dashboard shows extension connection state.
- [ ] Token persists across daemon restarts (no re-paste needed).

### Step 5: Tests

**Files to create:** `tests/test_browser_bridge.py`

**Test cases (unit, using mock WS client):**

1. **Server lifecycle:** bridge starts, listens on configured port, stops cleanly.
2. **Auth success:** client sends valid token, receives `auth_result` ok=true.
3. **Auth failure:** client sends wrong token, receives close code 4001.
4. **Single connection:** second client while first is connected receives close code 4002.
5. **RPC round-trip:** `bridge.call("list_tabs", {})`, mock responds, caller gets result.
6. **Request correlation:** two concurrent calls, mock responds out-of-order, each gets correct result.
7. **RPC timeout:** mock never responds, `bridge.call()` returns `{success: false, retryable: true}`.
8. **Connection drop:** mock disconnects mid-call, futures resolve with `{retryable: true}` error, `bridge.connected` becomes False.
9. **Reconnect:** after drop, new client connects and authenticates, new calls work.
10. **Malformed message:** client sends non-JSON, server logs warning, does not crash.
11. **Token persistence:** bridge reads token from Settings, does not regenerate on restart.
12. **Protocol version:** all messages include `"v": 1`.
13. **Origin validation:** WS handshake without `chrome-extension://` origin is rejected.

**Acceptance criteria:**
- [ ] All 13 test cases pass.
- [ ] Tests use `pytest-asyncio`.
- [ ] No real Chrome needed -- pure mock WS client.
- [ ] Tests run in < 5s total.

### Step 6: Manual Smoke Test

**Smoke test procedure:**
1. Start daemon with `browser_bridge_enabled = true`.
2. Verify `~/.heare/browser_bridge.token` exists and contains a token.
3. Open Chrome, `chrome://extensions`, Developer mode, Load unpacked `extensions/heare-bridge/`.
4. Open extension options, paste token from `~/.heare/browser_bridge.token`, save.
5. Verify extension badge turns green.
6. Open `heare watch` dashboard, verify BROWSER section shows green dot.
7. Navigate Chrome to `https://example.com`.
8. Trigger `read_browser_page` -- verify output contains "Example Domain".
9. Trigger `list_browser_tabs` -- verify current tab appears.
10. Trigger `navigate_browser` to `https://httpbin.org/html` -- verify tab navigates.
11. Trigger `click_in_browser` with selector `a` on `https://example.com` (has the IANA "More information..." link) -- verify click occurs.
12. Close Chrome -- verify dashboard shows grey dot, tools return `{retryable: true}` error.
13. Reopen Chrome -- verify extension reconnects, dashboard shows green dot.
14. Restart daemon -- verify **same token** is used (persisted in `config.toml`), extension reconnects without re-pasting token.
15. Run `heare rotate-browser-token` -- verify new token generated, old token rejected by daemon after restart, extension needs new token paste.

**Acceptance criteria:**
- [ ] All 15 smoke test steps pass.
- [ ] Extension install + token setup takes < 2 minutes for a new user.

---

## Success Criteria

1. The agent can read page content from any tab in the user's real, signed-in Chrome browser.
2. The agent can navigate, click, fill forms, list tabs, and open new tabs.
3. Connection failures produce structured errors with `retryable` flag (not hangs or stack traces).
4. The dashboard shows extension connection state at a glance.
5. Every browser RPC call is logged to `daemon.log`.
6. Unit tests pass with a mock WS client -- no real Chrome needed for CI.
7. The extension installs via a single "Load unpacked" step and one token paste (persistent across restarts).

---

## ADR: Browser Extension Bridge

**Decision:** Implement browser interaction via a Manifest V3 Chrome extension communicating over a token-authenticated WebSocket to a bridge server running inside the heare daemon.

**Drivers:**
1. The user's real, signed-in browser must be accessible (not an isolated instance).
2. Chrome 136+ blocks CDP attachment to the default user-data-dir.
3. Cross-platform support (macOS + Linux) is required; Firefox is deferred.

**Alternatives considered:**
- **Native Messaging host:** more secure (no open port) but adds per-OS install complexity (4 install paths: macOS/Linux x Chrome/Chromium), no daemon-to-extension push. Remains a Phase 2 candidate if WS surface proves too broad.
- **AppleScript JS-injection:** macOS-only, no tab management, no cross-platform path.
- **Isolated Chrome with profile snapshot:** violates "real browser" requirement, risks profile corruption.

**Why chosen:** WebSocket bridge is the simplest cross-platform option preserving full bidirectional communication, works within MV3 constraints, and reuses the existing `websockets` dependency. Security tradeoffs (localhost port, `<all_urls>`) are acceptable for a sideloaded developer tool with token authentication, Origin validation, and audit logging.

**Consequences:**
- The agent gains read access to every page the user visits. This is an explicit user opt-in.
- MV3 service worker suspension requires keepalive alarm; edge cases produce structured `retryable` errors.
- Token persists in `config.toml` across restarts. Explicit rotation via `heare rotate-browser-token`.

**Follow-ups (Phase 2):**
- Domain blocklist for sensitive sites.
- `screenshot` and `wait_for` tools.
- Per-session opt-in toggle on the dashboard.
- RPC rate limiting.
- Firefox MV3 extension.
- Evaluate Native Messaging as alternative transport if WS surface proves problematic.

---

## Failure Modes

| Failure | User-visible behavior | Detection |
|---|---|---|
| Extension not installed | `{success: false, error: "Browser not connected. Install...", retryable: false}` | `bridge.connected == False` |
| Chrome closed | `{success: false, error: "Browser not connected...", retryable: false}` | `bridge.connected == False` |
| Wrong token | Extension badge red. Dashboard grey dot. | Auth close code 4001 logged |
| Token rotated (after `heare rotate-browser-token`) | Extension disconnects, needs new token paste | Auth close code 4001 logged |
| Tab not found | `{success: false, error: "Tab 123 not found", retryable: false}` | Error in RPC response |
| Selector matches nothing | `{success: false, error: "No element matches selector 'div.foo'", retryable: false}` | Error in RPC response |
| Page is `chrome://`, `chrome-extension://`, or `file://` | `{success: false, error: "Cannot access chrome:// pages", retryable: false}` | Blocked by extension URL check |
| WS frame too large (>1MB page text) | Text truncated to 50,000 chars by content script | Handled transparently |
| MV3 service worker suspended mid-RPC | `{success: false, error: "Browser extension temporarily disconnected (Chrome suspended the connection). Try again in a few seconds.", retryable: true}` | Timeout error; LLM retries |
| Daemon crashes while extension connected | Extension enters reconnect loop with backoff. Badge goes grey. | Extension reconnect logs |
| Multi-profile conflict | Second Chrome profile's extension gets close code 4002 | Only one profile can hold the bridge connection |

---

## Security Considerations

1. **Scope of access:** `<all_urls>` grants DOM read on any page. Explicit user opt-in (sideloaded).
2. **Token authentication:** 256-bit entropy, persisted in `config.toml` + `~/.heare/browser_bridge.token` (0o600). Rotatable via `heare rotate-browser-token`. Not rotated per-restart.
3. **Origin validation:** WS handshake rejected unless `Origin: chrome-extension://`. Prevents local-process-hijack.
4. **Localhost binding:** `127.0.0.1` only.
5. **Single-connection policy:** Second connection gets close code 4002.
6. **No arbitrary JS:** `execute_script` NOT exposed in Phase 1.
7. **URL blocklist:** Extension rejects `chrome://`, `chrome-extension://`, `file://` before injection.
8. **Audit logging:** Every RPC logged (method, tab URL, elapsed, ok/fail). Auto-logged via `_make_handler`.
9. **Content truncation:** 50,000 char limit in content script.

**Deferred to Phase 2:** Domain blocklist, per-session opt-in, RPC rate limiting.

---

## Out of Scope

- Firefox extension.
- Chrome Web Store distribution.
- Recording / replay UX.
- Per-element OCR / vision fallback.
- Multi-window selection beyond "active tab in front window".
- Changes to existing CDP launcher (`src/daemon/browser.py`).
- `screenshot`, `wait_for`, `scroll_into_view`, `close_tab` tools (Phase 2).

---

## Changelog (REVISION 2)

1. **MUST_FIX #1 (Token lifecycle):** Replaced per-restart token rotation with persistent `config.toml` token (`browser_bridge_token` field in Settings). Token generated on first use if absent. Added `heare rotate-browser-token` CLI subcommand for explicit rotation. Updated Steps 1, 2, 4, and smoke test step 14.
2. **MUST_FIX #2 (MV3 suspension error contract):** Added structured `{success: false, error: "...", retryable: true}` return for ALL bridge disconnect/timeout/suspension scenarios. Applied uniformly in Step 1 (bridge server), Step 3 (tool handler error path), and Failure Modes table.
3. **MUST_FIX #3 (`run_until_stopped` integration):** Added `bridge_task` keyword argument to `run_until_stopped()` following the exact `namer_task` pattern (watch_set, background_tasks, named_tasks). Updated Step 4 with concrete code change description.
4. **MUST_FIX #4 (Open question #1 resolution):** Token persistence decision folded into plan body. Open questions sidecar entry marked RESOLVED.
5. **MUST_FIX #5 (URL blocklist):** Added `chrome://`, `chrome-extension://`, `file://` URL rejection in extension's content script dispatcher (Step 2) with ~3 lines of JS. Satisfies Principle 1.
6. **Non-blocking: Wire protocol versioning:** Added `"v": 1` to all JSON messages (zero cost, saves migration pain).
7. **Non-blocking: Origin header validation:** Added WS handshake Origin check for `chrome-extension://` prefix (~5 LOC in Step 1).
8. **Non-blocking: Action logging confirmation:** Explicitly documented that bridge tools in TOOLS dict are auto-logged via `_make_handler` wrappers.
9. **Non-blocking: Multi-profile documentation:** Added Failure Modes table row for multi-profile conflict (close code 4002).
10. **Non-blocking: Smoke test step 11 fix:** Changed "page with links" to concrete URL `https://example.com` (has the IANA "More information..." link).
11. **Non-blocking: Native Messaging deferral:** Strengthened rejection with per-OS maintenance cost (4 install paths). Added explicit sentence deferring Native Messaging to Phase 2 candidacy.
