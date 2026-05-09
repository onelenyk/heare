# Browser Bridge Stability: Fix Connect/Disconnect Cycling

**Status:** DRAFT (rev 2) -- awaiting user confirmation
**Created:** 2026-05-09
**Complexity:** MEDIUM
**Parent plan:** `browser-extension-bridge.md` (Phase 1, delivered)

---

## RALPLAN-DR Summary (Short Mode)

### Principles (4)

1. **Trust the platform** -- prefer Chrome-blessed APIs (Offscreen Document) over creative service-worker keepalive hacks that break at the next browser update.
2. **Single source of truth for connection state** -- the status file (`~/.heare/browser_bridge.status`) is written by whichever context owns the WebSocket, not guessed from SW liveness.
3. **Pair-code regeneration is a function of "no extension is paired"** -- not "no WS open right now". A 10-second blip between SW eviction and offscreen-doc takeover must not mint a new code.
4. **Wire-protocol stability** -- the Python server must not change its public message types. Python-side changes are limited to debouncing the lonely watcher.

### Decision Drivers (top 3)

1. **Stability under idle** -- the actual user-visible bug. A connected extension must survive 5+ minutes of pure idle.
2. **Minimum changes to existing RPC contract** -- the 7 RPC handlers in `background.js` (`list_tabs`, `read_page`, `click`, `fill`, `extract`, `navigate`, `open_tab`) keep their semantics. `ping` is a protocol-level keepalive handled inline in `ws.onmessage`, not an RPC handler in the `HANDLERS` map.
3. **Officially supported by Chrome** -- ship a pattern that Chrome documents and maintains, not one that works by accident.

### Viable Options

#### Option A: Offscreen Document owns the WebSocket (CHOSEN)

Add `offscreen.html` + `offscreen.js`. The offscreen document opens and owns the WebSocket, runs the auth/pair handshake, manages the pong-check, and holds connection state. The background service worker becomes a thin proxy: it creates the offscreen document on install/startup and bridges `chrome.tabs` / `chrome.scripting` / `chrome.webNavigation` RPCs between the offscreen doc and Chrome APIs (those APIs are SW-only). Communication between offscreen and SW uses a long-lived `chrome.runtime.connect` port (`name: 'heare-rpc'`); this keeps the SW alive for the duration of any in-flight RPC (Chrome documents this explicitly) and avoids the broadcast-to-all-contexts problem of `sendMessage`. User-initiated messages from popup/options still use `sendMessage` (they lack port access) and are forwarded to the offscreen doc via the port.

**Pros:**
- Officially supported since Chrome 109. Offscreen documents are not subject to SW lifetime rules.
- SW idle no longer kills the WS. The offscreen doc persists as long as the extension is loaded.
- `chrome.alarms` keepalive still wakes SW for any housekeeping if needed.
- Clean separation: networking in offscreen, Chrome APIs in SW.

**Cons:**
- Two new files (`offscreen.html`, `offscreen.js`).
- Every RPC method that uses `chrome.scripting.executeScript`, `chrome.tabs.*`, `chrome.webNavigation.*` now needs a proxy hop (offscreen -> port -> SW -> Chrome API -> SW -> port -> offscreen -> WS). All 8 existing handlers touch SW-only APIs, so all need wrapping.
- Requires Chrome 109+. Older Chrome lacks the API entirely. Mitigated by feature-gating with `chrome.offscreen` existence check.

#### Option B: Keep WS in SW, harden alarms keepalive (REJECTED)

Bump `chrome.alarms` to the minimum period and rely on alarm events to keep the SW alive.

**Why rejected:** This is exactly the architecture we already have. The logs prove it fails -- 10-second disconnect cycles. Chrome MV3 docs explicitly state that alarms do not prevent SW termination. The minimum alarm period (30s for unpacked, 1min for packed) exceeds the SW idle timeout (~30s). Even if the alarm fires in time, Chrome can evict the SW under memory pressure. This option has been empirically invalidated by the bug itself.

#### Option C: Polling reconnect -- accept SW death (REJECTED)

Accept that the SW will die. Reconnect aggressively after every SW wake.

**Why rejected:** Every reconnect costs an auth round-trip. The pair-code carousel only disappears if we suppress regeneration during the "expected" reconnect window, adding complexity to the Python side. RPC latency suffers because requests cannot be sent while reconnecting. This trades the "connection drops" bug for a "connection is flaky and slow" bug. It also contradicts Principle 1 (trust the platform -- the platform offers Offscreen Documents precisely for this use case).

### ADR

**Decision:** Migrate the WebSocket from the MV3 service worker to a Chrome Offscreen Document, and debounce the Python-side lonely watcher.

**Drivers:** (1) Empirically proven SW eviction at 10s intervals; (2) Offscreen Document API is the canonical Chrome-blessed solution for persistent connections in MV3; (3) Minimal wire-protocol changes required.

**Alternatives considered:** (B) SW + alarms hardening -- already deployed, still broken. (C) Polling reconnect -- trades one bug class for another.

**Why chosen:** Only option that addresses the root cause (SW lifetime rules) with a Chrome-supported mechanism. Other options are workarounds around a platform constraint that Chrome explicitly provides an escape hatch for.

**Consequences:** Two new extension files. Every RPC handler gains a port-based message-passing hop. Chrome 108 and older lose offscreen support (graceful fallback to current behavior with console warning). Python-side `_lonely_watcher` gains a debounce window. The long-lived port keeps the SW alive during in-flight RPCs, eliminating the fragile `sendResponse`+`return true` keepalive pattern.

**Follow-ups:** Remove the `chrome.alarms` keepalive entirely once offscreen stability is confirmed in production (it becomes redundant). Consider moving the pong-check to the offscreen doc's own `setInterval`. Evaluate whether `chrome.alarms` can also be removed as a port-reconnect mechanism once the offscreen `onDisconnect` handler proves reliable.

---

## Context

The Heare browser bridge (shipped in `browser-extension-bridge.md` Phase 1) uses a Chrome MV3 extension that opens a WebSocket from its background service worker to the Python daemon on `ws://localhost:9333`. Chrome MV3 service workers are suspended after ~30s of inactivity, and a held-open WebSocket does NOT count as work that keeps the SW alive. The result: every session lasts exactly ~10 seconds before Chrome kills the SW, the WS closes, and the daemon enters its lonely-watcher pair-code regeneration loop. A previous fix bumped `PONG_TIMEOUT_MS` from 10s to 35s and stopped alarms from initiating reconnection, but the 10-second floor persists because the root cause is SW suspension, not the pong-check.

---

## Work Objectives

Fix the connect/disconnect cycling so that a paired extension stays connected for 5+ minutes of idle time, pair codes are not regenerated during transient disconnects, and reconnection after a real disconnect completes within ~2 seconds.

---

## Guardrails

### Must Have
- Offscreen document owns the WebSocket and survives SW suspension.
- All 7 existing RPC handlers (`list_tabs`, `read_page`, `click`, `fill`, `extract`, `navigate`, `open_tab`) continue to work with identical semantics. The protocol-level `ping`/`pong` keepalive (handled inline in `ws.onmessage`, not via the `HANDLERS` map) also continues to work.
- Python-side `_lonely_watcher` debounced so a single 10-second blip does not mint a new pair code.
- Feature gate on `chrome.offscreen` existence; graceful fallback to current SW-based WS on Chrome < 109.
- Existing `tests/test_browser_bridge.py` continues to pass at its current baseline of **12/13** (the 13th, `test_single_connection`, is a pre-existing failure unrelated to this plan: it expects close code 4002 but the server kicks the old client with 1000 — out of scope here).
- New test coverage for the proxy message-passing contract.

### Must NOT Have
- No changes to the Python WS wire protocol (message types, field names, version).
- No changes to the pairing UI flow (options.html, popup.html).
- No changes to the Python `call()` method signature or error contract.
- No removal of the existing `chrome.alarms` keepalive (keep it as a belt-and-suspenders SW wake mechanism; remove in a follow-up once stability is confirmed).

---

## Task Flow

```
[Step 1: Offscreen document + WS migration]
         |
[Step 2: Background SW proxy layer]
         |
[Step 3: Python-side lonely-watcher debounce]
         |
[Step 4: Tests + manual verification]
```

---

## Detailed TODOs

### Step 1: Create Offscreen Document and Migrate WebSocket

**Files to create:** `extensions/heare-bridge/offscreen.html`, `extensions/heare-bridge/offscreen.js`
**Files to edit:** `extensions/heare-bridge/manifest.json`

**1a. `manifest.json` changes:**
- Add `"offscreen"` to the `permissions` array (after `"alarms"`).
- Add `"minimum_chrome_version": "109"` to the top-level manifest object (N6 -- belt-and-suspenders alongside the runtime feature gate).

**1b. `offscreen.html`:**
- Minimal HTML document. Only purpose is to host `offscreen.js`.
```html
<!DOCTYPE html>
<html><head><script src="offscreen.js"></script></head><body></body></html>
```

**1c. `offscreen.js` -- move all WS logic here:**

Move the following from `background.js` into `offscreen.js`:
- Constants: `DEFAULT_PORT`, `BACKOFF_STEPS`, `PONG_TIMEOUT_MS`.
- All WS state: `ws`, `backoffIdx`, `reconnectTimer`, `authOk`, `lastPongTime`, `pongCheckTimer`.
- Functions: `loadConfig()` (storage access works in offscreen), `connect()`, `disconnect()`, `scheduleReconnect()`, `startPongCheck()`, `stopPongCheck()`.
- The `ws.onopen` handler (sends auth/pair).
- The `ws.onmessage` handler, but ONLY for: `auth_result`, `pair_result`, `pong` message types. For `request` messages (RPC from daemon), forward the request to the background SW via the long-lived port and await the response.
- The `ws.onclose` handler.

**Offscreen-to-SW communication contract (port-based):**

The offscreen doc opens a long-lived port to the SW on startup. All RPC traffic flows over this port. The port keeps the SW alive for the duration of any in-flight message handler (Chrome documents this explicitly), eliminating the fragile `sendResponse` + `return true` keepalive pattern and avoiding `sendMessage` broadcast-to-all-contexts ambiguity.

```javascript
// In offscreen.js -- on script load
let swPort = null;
const pendingRPCs = new Map();  // id -> {resolve, reject, timer}

function connectPort() {
  swPort = chrome.runtime.connect({name: 'heare-rpc'});
  swPort.onMessage.addListener((msg) => {
    if (msg.type === 'rpc_response') {
      const pending = pendingRPCs.get(msg.id);
      if (pending) {
        clearTimeout(pending.timer);
        pendingRPCs.delete(msg.id);
        pending.resolve({ok: msg.ok, result: msg.result, error: msg.error});
      }
    }
    if (msg.type === 'reconnect') {
      // Forwarded from popup/options via SW
      disconnect();
      connect();
    }
  });
  swPort.onDisconnect.addListener(() => {
    // SW restarted (alarm, navigation event, etc.)
    swPort = null;
    setTimeout(connectPort, 500);  // reconnect port after brief delay
  });
}
connectPort();

// Send RPC request to SW and return a Promise
function sendRPCToSW(id, method, params) {
  return new Promise((resolve, reject) => {
    if (!swPort) { reject(new Error('port disconnected')); return; }
    const timer = setTimeout(() => {
      pendingRPCs.delete(id);
      reject(new Error('RPC timeout'));
    }, 30000);
    pendingRPCs.set(id, {resolve, reject, timer});
    swPort.postMessage({type: 'rpc_request', id, method, params});
  });
}
```

The offscreen doc also notifies SW of connection state changes for badge updates via the same port:
```javascript
// Offscreen -> SW (via port)
swPort.postMessage({type: 'connection_state', state: 'connected' | 'auth_failed' | 'pair_failed' | 'disconnected'});
// For pair success:
swPort.postMessage({type: 'pair_success', token: '...'});
```

**Offscreen `request` handler (daemon RPC):**

When `ws.onmessage` receives `type: 'request'`:
1. Call `sendRPCToSW(id, method, params)` which sends `{type: 'rpc_request', id, method, params}` to SW via `port.postMessage`.
2. The SW receives the message on its `port.onMessage` listener, executes the handler (it has access to `chrome.tabs`, `chrome.scripting`, etc.).
3. SW sends the result back via `port.postMessage({type: 'rpc_response', id, ok, result, error})`.
4. The offscreen `port.onMessage` listener resolves the pending Promise.
5. Offscreen sends the response back over the WS: `{v: 1, id, type: 'response', ok, result/error}`.

**Offscreen reconnect handling:**

The offscreen doc listens for `{type: 'reconnect'}` messages on the port (forwarded from popup/options by the SW). On receipt, calls `disconnect()` then `connect()`. No `chrome.runtime.onMessage` listener needed in the offscreen doc for this purpose.

**Acceptance criteria:**
- [ ] `offscreen.html` and `offscreen.js` exist.
- [ ] `manifest.json` includes `"offscreen"` permission.
- [ ] WebSocket is opened, authenticated, and maintained entirely within the offscreen document.
- [ ] `pong` check runs in offscreen via `setInterval` (not dependent on SW being alive).
- [ ] Offscreen doc opens a long-lived port (`chrome.runtime.connect({name: 'heare-rpc'})`) to the SW on startup.
- [ ] RPC requests from the daemon are forwarded to SW via `port.postMessage`.
- [ ] RPC responses from the SW arrive via `port.onMessage` and resolve the pending Promise.
- [ ] Connection state changes are forwarded to SW via the port for badge updates.
- [ ] Port `onDisconnect` handler reconnects the port after a brief delay (SW restart recovery).
- [ ] `chrome.storage.local` access works from offscreen (for `loadConfig()`).

### Step 2: Refactor Background SW as Thin Proxy

**Files to edit:** `extensions/heare-bridge/background.js`

**2a. Remove from `background.js`:**
- All WS state variables (`ws`, `backoffIdx`, `reconnectTimer`, `authOk`, `lastPongTime`, `pongCheckTimer`).
- Functions: `connect()`, `disconnect()`, `scheduleReconnect()`, `startPongCheck()`, `stopPongCheck()`, `loadConfig()`.
- The `ws.onopen`, `ws.onmessage`, `ws.onclose`, `ws.onerror` handlers.

**2b. Keep in `background.js`:**
- `BLOCKED_SCHEMES` constant and `isBlocked()` / `blockedError()` helpers.
- All 8 RPC handler functions (`handleListTabs`, `handleReadPage`, `handleClick`, `handleFill`, `handleExtract`, `handleNavigate`, `handleOpenTab`).
- The `HANDLERS` map.
- Badge functions (`badgeGreen`, `badgeRed`, `badgePairFailed`, `badgeGrey`).
- Tab helper functions (`resolveTabId`, `getTab`).

**2c. Add to `background.js`:**

**Feature gate for Chrome < 109:**
```javascript
let HAS_OFFSCREEN = typeof chrome.offscreen !== 'undefined';
```
Note: `let` not `const` -- M3 fallback sets `HAS_OFFSCREEN = false` if `createDocument` throws.
If `HAS_OFFSCREEN` is false, fall back to the current behavior (keep all WS logic in background.js as-is). Log a console warning: `'[heare-bridge] chrome.offscreen not available; falling back to service-worker WebSocket (connection may be unstable)'`.

**Offscreen document creation (with M3 try/catch + fallback):**
```javascript
async function ensureOffscreen() {
  if (!HAS_OFFSCREEN) return false;
  try {
    const has = await chrome.offscreen.hasDocument();
    if (!has) {
      await chrome.offscreen.createDocument({
        url: 'offscreen.html',
        reasons: [chrome.offscreen.Reason.WORKERS],
        justification: 'Persistent WebSocket to local Heare daemon',
      });
    }
    return true;
  } catch (err) {
    console.warn('[heare-bridge] offscreen createDocument failed, falling back to SW WS:', err);
    HAS_OFFSCREEN = false;  // disable for this SW lifetime; alarm retry next cycle
    return false;
  }
}
```
`Reason.WORKERS` is the correct enum value -- it covers persistent background processing including WebSocket connections. `Reason.WEB_RTC` is semantically wrong (WebSocket is not WebRTC). The `catch` block handles Chrome 109-113 edge cases and stale `hasDocument()` races. When `HAS_OFFSCREEN` becomes `false`, the SW falls back to the legacy `connect()` body (kept gated for exactly this case). The alarm retry on the next cycle will re-attempt offscreen creation by checking `HAS_OFFSCREEN` before the gate (the gate is per-SW-lifetime, and alarm-triggered SW wakes get a fresh lifetime).

**Lifecycle listeners (replace current ones):**
```javascript
chrome.runtime.onInstalled.addListener(async () => {
  chrome.storage.local.set({connectionStatus: ''});
  if (HAS_OFFSCREEN) {
    await ensureOffscreen();
  } else {
    connect();  // fallback: old behavior
  }
});

chrome.runtime.onStartup.addListener(async () => {
  chrome.storage.local.set({connectionStatus: ''});
  if (HAS_OFFSCREEN) {
    await ensureOffscreen();
  } else {
    connect();
  }
});
```

**Port listener for offscreen doc (M1 -- replaces sendMessage-based RPC):**

The SW holds a reference to the offscreen port. When the offscreen doc connects (or reconnects after SW restart), the SW stores the port and wires up message handling.

```javascript
let offscreenPort = null;

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'heare-rpc') return;
  offscreenPort = port;

  port.onMessage.addListener(async (msg) => {
    if (msg.type === 'rpc_request') {
      // From offscreen doc: execute Chrome API handler
      const handler = HANDLERS[msg.method];
      let result;
      if (!handler) {
        result = {ok: false, error: {code: 'UNKNOWN_METHOD', message: 'Unknown method: ' + msg.method}};
      } else {
        try {
          result = await handler(msg.params || {});
        } catch (err) {
          result = {ok: false, error: {code: 'HANDLER_ERROR', message: String(err)}};
        }
      }
      port.postMessage({type: 'rpc_response', id: msg.id, ...result});
    }

    if (msg.type === 'connection_state') {
      // From offscreen doc: update badge
      if (msg.state === 'connected') badgeGreen();
      else if (msg.state === 'auth_failed') badgeRed();
      else if (msg.state === 'pair_failed') badgePairFailed();
      else badgeGrey();
    }

    if (msg.type === 'pair_success') {
      // From offscreen doc: store token from successful pairing
      chrome.storage.local.set({token: msg.token});
      chrome.storage.local.remove('pairCode');
    }
  });

  port.onDisconnect.addListener(() => {
    offscreenPort = null;
  });
});
```

**sendMessage listener (M2 -- only for popup/options reconnect, no re-broadcast):**

Popup and options pages cannot access the offscreen port. They send `{type: 'reconnect'}` via `chrome.runtime.sendMessage`. The SW forwards this to the offscreen doc via the port -- never via `sendMessage` (which would re-broadcast back to the SW itself, causing an infinite loop).

```javascript
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'reconnect') {
    // From options/popup page -- forward to offscreen via port
    if (offscreenPort) {
      offscreenPort.postMessage({type: 'reconnect'});
      sendResponse({ok: true});
    } else if (!HAS_OFFSCREEN) {
      // Fallback: old behavior
      disconnect();
      connect().then(() => sendResponse({ok: true}));
    } else {
      // Offscreen port not yet connected; ensure offscreen doc exists
      ensureOffscreen().then(() => sendResponse({ok: true, note: 'offscreen re-created, will auto-connect'}));
    }
    return true;
  }
});
```

**Alarms (keep as belt-and-suspenders):**
Keep the existing `chrome.alarms.create('keepalive', {periodInMinutes: 0.4})`. On alarm fire, if offscreen mode: call `ensureOffscreen()` (re-creates the offscreen doc if Chrome garbage-collected it, which should not happen but is defensive). If fallback mode: existing ping logic.

**Acceptance criteria:**
- [ ] `background.js` no longer opens or holds a WebSocket.
- [ ] `background.js` creates the offscreen document on install/startup.
- [ ] SW accepts the offscreen port via `chrome.runtime.onConnect` and stores a reference (`offscreenPort`).
- [ ] RPC requests from the offscreen doc arrive via `port.onMessage`, are dispatched to the correct handler, and responses are returned via `port.postMessage`.
- [ ] Badge updates are driven by connection-state messages from the offscreen doc via the port.
- [ ] Feature gate: `chrome.offscreen` missing falls back to current SW-based WS with console warning.
- [ ] `ensureOffscreen()` has try/catch; on failure sets `HAS_OFFSCREEN = false` and falls back to SW-based WS for this SW lifetime.
- [ ] `chrome.alarms` keepalive calls `ensureOffscreen()` as a defensive check.
- [ ] Options page `reconnect` message (via `sendMessage`) is forwarded to the offscreen doc via the port -- NOT re-broadcast via `sendMessage` (no self-delivery loop).
- [ ] All 8 RPC methods work end-to-end through the port-based proxy hop.

### Step 3: Python-Side Lonely-Watcher Debounce

**Files to edit:** `src/agent/browser_bridge.py`

**3a. Debounce `_lonely_watcher` pair-code regeneration:**

Current behavior: `PAIR_CODE_REGEN_AFTER_LONELY_S = 5.0` -- after just 5 seconds of being lonely, a new pair code is minted. This is too aggressive; a transient SW restart (even in the old architecture) causes unnecessary code churn.

Changes:
- Increase `PAIR_CODE_REGEN_AFTER_LONELY_S` from `5.0` to `30.0`. This means the watcher waits 30 uninterrupted seconds of no connection before regenerating a pair code.
- Add a check: if a pair code already exists and has more than 30 seconds remaining on its TTL, skip regeneration. This prevents the carousel where a disconnect-reconnect cycle at ~10s intervals causes a new code every minute.

Concrete diff in `_lonely_watcher`:
```python
# Before the _generate_pair_code() call, add:
if code_active:
    continue
# Already present. But also add: if a code exists (even expired),
# require the lonely window to be UNINTERRUPTED for the full threshold.
# The key change is _lonely_since is reset to None on connect and
# set to time.time() on disconnect. If a reconnect happens within
# the threshold, _lonely_since resets and the counter starts over.
```

The existing logic already handles this correctly via `_lonely_since` being set to `None` on connect and `time.time()` on disconnect. The only change needed is bumping `PAIR_CODE_REGEN_AFTER_LONELY_S` from `5.0` to `30.0`.

**3b. Verify `_lonely_since` reset correctness:**

In `_handle_connection`, line 209: `self._lonely_since = None` -- set on successful auth. Correct.
In `_handle_connection`, line 240: `self._lonely_since = time.time()` -- set on disconnect. Correct.

The debounce logic in `_lonely_watcher` (lines 445-453) already requires `lonely_for >= PAIR_CODE_REGEN_AFTER_LONELY_S` before regenerating. Bumping the constant to 30.0 means a reconnect within 30 seconds resets `_lonely_since` to `None` (line 209), preventing regeneration. This is sufficient.

**3c. No other Python changes needed:**

- `ping_interval=20, ping_timeout=20` in `serve(...)` -- these are websockets library-level pings (not app-level). They are fine; the offscreen doc will respond to library-level pings automatically.
- The `_handle_inbound` method handles app-level `ping` messages and responds with `pong`. No changes needed.
- The `_write_status` method writes `connected: true/false` -- no changes needed.
- `bridge_connected()` in `data.py` reads the status file -- no changes needed.

**Acceptance criteria:**
- [ ] `PAIR_CODE_REGEN_AFTER_LONELY_S` changed from `5.0` to `30.0`.
- [ ] A transient disconnect of < 30 seconds does NOT trigger pair-code regeneration.
- [ ] After 30+ uninterrupted seconds of no connection, a new pair code is generated as before.
- [ ] No changes to the WS wire protocol or message types.
- [ ] No changes to `call()`, `connected`, or the error contract.

### Step 4: Tests and Manual Verification

**Files to edit:** `tests/test_browser_bridge.py`
**No new test files for the extension JS** (extension tests are manual; JS unit testing infra is out of scope for this plan).

**4a. Existing test suite:**

Run `tests/test_browser_bridge.py` -- the **12** previously-passing tests must continue to pass unchanged. `test_single_connection` is **already failing** on `main` (expects close 4002, gets 1000 — see Risks); do not let its red status mask new regressions, and do not modify it as part of this plan. The Python-side changes are limited to a constant bump, so no test modifications are expected for the 12 passing tests.

**4b. New test: lonely-watcher debounce (fast -- S5):**

Add `test_lonely_watcher_debounce` to `tests/test_browser_bridge.py`. Monkey-patch the threshold to avoid a 35-second real-time wait:
- At test start: `monkeypatch.setattr('src.agent.browser_bridge.PAIR_CODE_REGEN_AFTER_LONELY_S', 2.0)`.
- Start a bridge.
- Connect a client, authenticate, then disconnect.
- Wait 1 second (less than the patched 2.0s threshold).
- Assert: no new pair code was generated (check `bridge._pair_code` is `None` or unchanged).
- Continue waiting until 2.5 seconds after disconnect.
- Assert: a new pair code WAS generated (check `bridge._pair_code` is not `None`).

The production constant stays at `30.0`. The test exercises the debounce logic in ~3 seconds instead of ~35 seconds, keeping CI fast.

**4c. New test: rapid connect/disconnect cycling does not generate pair codes:**

Add `test_rapid_cycling_no_pair_codes` to `tests/test_browser_bridge.py`:
- Start a bridge.
- In a loop (5 iterations): connect, authenticate, wait 1 second, disconnect, wait 1 second.
- After the loop, assert: `bridge._pair_code` is `None` (no code was generated during the rapid cycling because `_lonely_since` kept resetting).

**4d. Manual verification procedure (acceptance criteria 1-5):**

**AC1: 5-minute idle stability:**
1. Load the updated extension (`chrome://extensions` -> Load unpacked).
2. Pair or authenticate.
3. Open `~/.heare/logs/daemon.log` in a terminal: `tail -f ~/.heare/logs/daemon.log`.
4. Wait 5 minutes. Do nothing in Chrome.
5. Verify: a single `client authenticated` line appears, followed by 5+ minutes with NO `client disconnected` line.

**AC2: No pair-code regeneration while connected:**
1. While the extension is connected (AC1 test running), grep the log: `grep "pair code" ~/.heare/logs/daemon.log`.
2. Verify: zero `pair code` log lines appear between the `authenticated` and any subsequent `disconnected`.

**AC3: Clean reconnect after real disconnect:**
1. Reload the extension (`chrome://extensions` -> click the reload button on Heare Bridge).
2. Watch `tail -f ~/.heare/logs/daemon.log`.
3. Verify: `client disconnected` appears, followed within ~2 seconds by `client authenticated`.
4. Verify: at most one `pair code` line appears during the reconnect window (only if the extension was using token auth, not pairing; if using token auth, zero pair codes should appear).

**AC4: Manual pair flow works:**
1. Stop the daemon, delete `~/.heare/browser_bridge.token`, restart the daemon.
2. Open the extension options page.
3. Enter the 6-digit pair code from the daemon log / dashboard.
4. Verify: extension badge turns green, dashboard shows connected.

**AC5: Test suite passes:**
1. Run `python -m pytest tests/test_browser_bridge.py -v`.
2. Verify: all tests pass (existing 13 + new 2).

**Acceptance criteria:**
- [ ] All 12 currently-passing tests in `test_browser_bridge.py` still pass unchanged. (`test_single_connection` is broken on `main` — pre-existing, out of scope.)
- [ ] New test `test_lonely_watcher_debounce` passes.
- [ ] New test `test_rapid_cycling_no_pair_codes` passes.
- [ ] Manual AC1-AC5 verification procedure documented and executable.

---

## Risk Section

### Chrome < 109 (no Offscreen API)

Chrome 109 shipped January 2023. Any Chrome installation older than ~3.5 years lacks `chrome.offscreen`. Mitigation: feature-gate in `background.js`:

```javascript
let HAS_OFFSCREEN = typeof chrome.offscreen !== 'undefined';
```

If false (or set to false by M3 fallback), the extension falls back to the current behavior (WS in SW, alarm keepalive). A console warning is logged. The extension remains functional but subject to the same 10-second cycling bug. This is acceptable because:
- The extension is sideloaded (developer tool), so users control their Chrome version.
- Chrome auto-updates; staying on Chrome < 109 for 3+ years requires deliberate effort.
- The fallback is identical to today's behavior (no regression).

### Offscreen document garbage collection

Chrome may garbage-collect an offscreen document if it determines it is "inactive." The `Reason.WORKERS` justification and the active WebSocket should prevent this. Belt-and-suspenders: the `chrome.alarms` keepalive calls `ensureOffscreen()` every 24 seconds, re-creating the document if it was collected.

### Message-passing latency

Every RPC now has an extra hop (offscreen -> port -> SW -> Chrome API -> SW -> port -> offscreen). Port-based `postMessage` adds ~1-5ms per hop, comparable to `sendMessage`. For RPCs that already take 50-500ms (DOM injection, navigation), this is negligible. No user-visible latency regression expected.

### Offscreen `createDocument` throws -- silent dead state (M3 mitigation)

`chrome.offscreen.createDocument` can throw on Chrome 109-113 edge cases or on a stale `hasDocument()` race. Without the M3 try/catch, a thrown error would leave the extension in a silent dead state: no offscreen doc, no WS, no error visible to the user. **Mitigated:** `ensureOffscreen()` wraps the call in try/catch. On failure, it sets `HAS_OFFSCREEN = false` for the current SW lifetime, triggering the legacy SW-based WS fallback. The console warning makes the failure visible for debugging. The alarm retry on the next SW wake gets a fresh `HAS_OFFSCREEN = true` and re-attempts offscreen creation.

---

## Files Changed Summary

| File | Action | Description |
|---|---|---|
| `extensions/heare-bridge/manifest.json` | Edit | Add `"offscreen"` permission, add `"minimum_chrome_version": "109"` |
| `extensions/heare-bridge/offscreen.html` | Create | Minimal HTML host for offscreen.js |
| `extensions/heare-bridge/offscreen.js` | Create | WebSocket owner: connect, auth, pong-check, RPC forwarding |
| `extensions/heare-bridge/background.js` | Major edit | Remove WS logic, add offscreen creation + RPC proxy + badge relay |
| `src/agent/browser_bridge.py` | Minor edit | Bump `PAIR_CODE_REGEN_AFTER_LONELY_S` from 5.0 to 30.0 |
| `tests/test_browser_bridge.py` | Edit | Add 2 new tests for debounce behavior |

---

## Success Criteria

1. A connected extension stays connected for 5+ minutes of pure idle (measured from daemon log). Reinforced by: offscreen document WS lifetime (not subject to SW suspension) AND long-lived port keepalive (keeps SW alive during in-flight RPCs).
2. While connected, zero `pair code` log lines between `authenticated` and `disconnected`.
3. After a real disconnect (extension reload, daemon restart), reconnection completes within ~2 seconds. Port `onDisconnect` handler auto-reconnects the offscreen-to-SW port.
4. Manual pair flow works end-to-end unchanged.
5. `tests/test_browser_bridge.py` passes (12 currently-passing + 2 new tests; `test_single_connection` remains a pre-existing failure, out of scope). Debounce test runs in ~3 seconds (monkey-patched threshold).

---

## Changelog (rev 2 -- 2026-05-09)

Revisions from Architect + Critic ITERATE feedback.

| ID | Priority | Summary |
|---|---|---|
| M1 | MUST | Replaced `chrome.runtime.sendMessage` with `chrome.runtime.connect` (long-lived port, name `heare-rpc`) for all offscreen-to-SW RPC traffic. Port keeps SW alive during in-flight RPCs. Offscreen reconnects port on `onDisconnect`. |
| M2 | MUST | Fixed `reconnect` self-delivery loop. SW now forwards popup/options `reconnect` messages to offscreen via the port, not via `sendMessage` re-broadcast. |
| M3 | MUST | Added try/catch + fallback to `ensureOffscreen()`. On `createDocument` failure, sets `HAS_OFFSCREEN = false` for current SW lifetime, triggering legacy SW-based WS fallback. |
| S4 | SHOULD | Changed `Reason.WEB_RTC` to `Reason.WORKERS` throughout. WebSocket is not WebRTC; `WORKERS` is the correct semantic match. |
| S5 | SHOULD | Debounce test now monkey-patches `PAIR_CODE_REGEN_AFTER_LONELY_S` to `2.0` instead of waiting 35 seconds real-time. Test completes in ~3 seconds. |
| N6 | NICE | Added `"minimum_chrome_version": "109"` to `manifest.json` alongside the runtime feature gate. |
| Cl7 | CLARIFICATION | Disambiguated `rpc_response` routing. With the port-based design, all RPC traffic flows over a single `port.onMessage` listener on each end. No `sendResponse` callback semantics, no broadcast ambiguity. Contract section fully rewritten. |

**Risks updated:** Removed "sendMessage routing ambiguity" risk (no longer applicable with port-based design). Added "offscreen createDocument throws -- silent dead state" risk with M3 mitigation.

---

## Implementation addendum (2026-05-09)

After landing the consensus plan above, two issues surfaced during live use that were not anticipated by the planner or reviewers and required a follow-up patch:

### A1. `chrome.storage` is not universally available in offscreen documents

The Chrome offscreen-document API surface is more restricted than the documentation suggests. On the user's Chrome build, `chrome.storage` was `undefined` inside offscreen.html, even with `"storage"` in `permissions`. The error surfaced as `TypeError: Cannot read properties of undefined (reading 'local')` at `offscreen.js:24` (`chrome.storage.local.get(...)`).

**Fix shipped:** all storage and `chrome.runtime.openOptionsPage()` calls in offscreen.js are now proxied through the SW via three new port message types:

- `{type:'load_config'}` — offscreen → SW; SW reads `chrome.storage.local.get({token, port, pairCode})` and replies `{type:'config', token, port, pairCode}`
- `{type:'storage_remove', key}` — offscreen → SW; SW deletes the key
- `{type:'open_options_page'}` — offscreen → SW; SW calls `chrome.runtime.openOptionsPage()`

The offscreen doc's only remaining `chrome.*` dependency is `chrome.runtime.connect` (universally available). For future implementers: **assume offscreen documents only have `chrome.runtime.connect`/`sendMessage`, the WebSocket API, fetch, and DOM. Proxy everything else through the SW.**

### A2. Connection policy reverted from "kick old" to "refuse new"

The pre-existing `_handle_connection` behavior (kick old, accept new with close code 1000) was added before the offscreen migration to handle SW restarts that left zombie WebSockets. With the offscreen architecture the WS no longer dies on SW suspension, so a second connection now almost always means a second Chrome profile competing for the same daemon. "Kick old" caused a kick-war between profiles.

**Fix shipped:** revert to refuse-new (close code `CLOSE_ALREADY_CONNECTED` / 4002). Side benefit: the pre-existing `test_single_connection` failure (which was out-of-scope per the original PRD) is now green. Multi-profile remains a single-client model — see "Two-profile kick war" follow-up below.

### A3. Follow-up: tab activation tool

Unrelated to stability but exposed during validation: the agent had `navigate` and `open_tab` but no way to bring an existing tab to the foreground. Asking it to "activate tab X" caused either a URL reload (`navigate`) or a duplicate (`open_tab`). Added an `activate_browser_tab` tool: `chrome.tabs.update(tabId, {active:true})` + `chrome.windows.update(windowId, {focused:true})`, exposed via `activate_tab` RPC.

### Outstanding follow-ups

- **Two-profile concurrent support** — current refuse-new policy means only one Chrome profile can connect at a time. Multi-profile would require routing RPCs to a "primary" client or fanning them out. Out of scope for stability; tracked here.
- **Remove `chrome.alarms` keepalive** — redundant once offscreen-doc + port keepalive proves stable in real use; defer until multi-day stability confirmed.

**Acceptance criteria reinforced:** AC1 (5-min idle) now backed by both offscreen WS lifetime AND port keepalive. AC3 (reconnect) reinforced by port `onDisconnect` auto-reconnect. AC5 (test suite) reinforced by fast debounce test.
