# Stage 11: Ecosystem — heare Plugged Into Nazar's Mac + iOS + Devtool Workflow

**Research date:** 2026-04-23
**Stage:** 11 of N (Ecosystem Integration)
**Objective:** Identify concrete integration points that make heare a first-class citizen of Nazar's existing toolchain: Raycast, Shortcuts, Alfred, Services menu, iOS/Watch, HomeKit, git hooks, Claude Code hooks, Slack, and a unified JSON-RPC API.

---

## Architecture Overview

```
                          ┌─────────────────────────────────┐
                          │     heare daemon (127.0.0.1:7999)│
                          │  JSON-RPC  /rpc  Bearer-auth     │
                          │  IntentQueue → ActionWorker      │
                          │  edge-tts → speaker              │
                          └──────────────┬──────────────────┘
                                         │
          ┌──────────┬───────────┬───────┴──────┬──────────────┬─────────────┐
          │          │           │              │              │             │
   [Raycast]   [Alfred]  [Shortcuts]    [Services]      [Git hooks]  [Claude Code]
  TypeScript  bash/py   URL scheme     Automator        post-commit   HTTP hooks
  fetch()     Script    heare://       .workflow        curl /rpc     localhost
  /rpc        Filter    /rpc           NSService        7999          7999
          │          │           │              │              │             │
          └──────────┴───────────┴───────┬──────┴──────────────┴─────────────┘
                                         │
          ┌──────────┬───────────┬───────┴──────┬──────────────┐
          │          │           │              │              │
      [iOS app]  [Watch]    [HomeKit]       [Slack]     [Linear/GitHub]
     SwiftUI     WKHaptic   HAP-python    Events API    webhooks
     Tailscale   tap-pattern accessory    app_mention   /rpc POST
     APNs push   companion  Siri bridge  Socket Mode
```

---

## Findings

[FINDING:I1] **JSON-RPC HTTP API — the universal integration hub**

A single FastAPI/aiohttp endpoint at `http://127.0.0.1:7999/rpc` (Bearer token from `HEARE_RPC_TOKEN` env var) is the lowest-friction common denominator for all integrations. Any tool that can run `curl` or `fetch()` gains full heare control with zero IPC plumbing.

Proposed schema:
```json
POST /rpc
Authorization: Bearer <token>
{"jsonrpc":"2.0","method":"intent.submit","id":1,
 "params":{"tool":"bash","args":"say hello","priority":"normal"}}

Response:
{"jsonrpc":"2.0","id":1,"result":{"intent_id":42,"status":"queued"}}
```

Supplementary endpoints:
- `GET /rpc/status` → `{"state":"listening|thinking|speaking","queue_depth":N}`
- `POST /rpc/speak` → direct TTS without LLM round-trip
- `GET /rpc/history?n=10` → last N spoken utterances

[STAT:confidence] HIGH — heare already has `src/actions.py` IntentQueue + ActionWorker; adding an aiohttp route is ~50 LOC on top of the existing asyncio loop.
[STAT:n] 13 integration spokes all collapse to one authenticated endpoint.
Source: heare `src/actions.py` (IntentQueue), `src/main.py` (asyncio event loop).

---

[FINDING:I2] **Raycast extension — TypeScript fetch() to /rpc**

Raycast extensions run as Node.js workers with full npm/fetch network access (no localhost restrictions). An extension can:
1. Call `fetch("http://127.0.0.1:7999/rpc", {method:"POST", body: JSON.stringify({...})})` directly.
2. Surface three quick actions: "Ask heare" (open text input → submit intent), "Read clipboard to heare" (paste clipboard as intent args), "heare status" (poll `/rpc/status`).

```typescript
// raycast-heare/src/ask-heare.tsx
import { Form, ActionPanel, Action, showToast, Toast } from "@raycast/api";
import fetch from "node-fetch";

export default function AskHeare() {
  async function handleSubmit(values: { query: string }) {
    const res = await fetch("http://127.0.0.1:7999/rpc", {
      method: "POST",
      headers: { Authorization: `Bearer ${process.env.HEARE_RPC_TOKEN}`,
                 "Content-Type": "application/json" },
      body: JSON.stringify({jsonrpc:"2.0",method:"intent.submit",id:1,
                            params:{tool:"bash",args:values.query}}),
    });
    const json = await res.json() as {result:{intent_id:number}};
    await showToast({ style: Toast.Style.Success,
                      title: `Queued #${json.result.intent_id}` });
  }
  return (
    <Form actions={<ActionPanel><Action.SubmitForm onSubmit={handleSubmit}/></ActionPanel>}>
      <Form.TextField id="query" title="Ask heare"/>
    </Form>
  );
}
```

[STAT:confidence] HIGH — Raycast blog confirms Node.js runtime with full npm ecosystem access; `fetch` to localhost is unrestricted.
Source: https://www.raycast.com/blog/how-raycast-api-extensions-work

---

[FINDING:I3] **macOS Shortcuts — URL scheme + `shortcuts://run-shortcut` bridge**

Two complementary paths:
1. **heare registers `heare://` URL scheme** via a tiny Swift/AppleScript wrapper app (Info.plist `CFBundleURLTypes`). Shortcuts calls `Open URL → heare://intent?tool=bash&args=…` → wrapper POSTs to `/rpc`. Enables "Take screenshot and describe" from Shortcuts.
2. **Shortcuts calls heare CLI directly** via a "Run Shell Script" action: `hearectl intent bash "$(pbpaste)"` — no URL scheme needed if `hearectl` is on PATH.

Apple Shortcuts URL scheme docs confirm: `shortcuts://run-shortcut?name=NAME&input=TEXT` can chain into heare's URL scheme.

```bash
# hearectl as Shortcuts-callable CLI (already exists at /Users/lenyk/myprojects/heare/hearectl)
hearectl intent bash "describe active window"
# hearectl posts to /rpc, exits 0 — Shortcuts treats as success
```

[STAT:confidence] HIGH — `hearectl` already exists in repo; URL scheme wrapper is ~20 lines of Swift or a 5-line Platypus app.
Source: https://support.apple.com/guide/shortcuts-mac/run-a-shortcut-from-a-url-apd624386f42/mac

---

[FINDING:I4] **Alfred — Script Filter + bash curl**

Alfred Script Filter executes any shell script and reads JSON output. The simplest Alfred workflow:
- **Script Filter input** (bash): `curl -s -X POST -H "Authorization: Bearer $HEARE_RPC_TOKEN" -H "Content-Type: application/json" -d "{...}" http://127.0.0.1:7999/rpc/status | jq -r '"heare: \(.state)"'` → populate Alfred results with current heare state.
- **Run Script action**: post intent on Alfred keyword `hq {query}`.

Alfred's python `alfred-workflow` library (deanishe) can also wrap the curl calls with caching and fuzzy search.

[STAT:confidence] HIGH — Alfred Script Filters are language-agnostic; bash+curl+jq is zero-dependency.
Source: https://www.alfredapp.com/help/workflows/inputs/script-filter/

---

[FINDING:I5] **macOS Services menu — "Send selection to heare" via Automator**

Automator lets you create a macOS Service (`.workflow` bundle) that:
1. Receives selected text from any application.
2. Runs a shell script: `curl -s -X POST … http://127.0.0.1:7999/rpc/speak -d "{"text":"$1"}"`.
3. Appears in right-click → Services → "Send to heare".

NSServices configuration happens through Automator's "Service receives selected text" template — no Xcode required. The service shows in every app that supports text selection.

```bash
# Automator "Run Shell Script" action body
TOKEN=$(cat ~/.config/heare/token)
TEXT="$1"
curl -s -X POST http://127.0.0.1:7999/rpc/speak   -H "Authorization: Bearer $TOKEN"   -H "Content-Type: application/json"   -d "{\"text\":\"$TEXT\"}"
```

[STAT:confidence] HIGH — Apple docs confirm Automator services expose selected text to shell scripts.
Source: https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/MakeaSystem-WideService.html

---

[FINDING:I6] **iOS companion app — SwiftUI + Tailscale + APNs**

A minimal SwiftUI app (≈ 3 weeks solo effort, or 1 week with Cursor/Claude Code) provides:
- (a) **Voice input** → STT → POST to heare `/rpc` over Tailscale (WireGuard tunnel, no port forwarding needed).
- (b) **APNs push receive**: heare daemon calls Apple Push Notification service when an action result is ready; iOS app surfaces it as a notification.
- (c) **Pending intent approval**: GET `/rpc/pending` → list of intents awaiting confirmation → SwiftUI list with Approve/Reject buttons.

Architecture:
```
iPhone (SwiftUI) ──Tailscale──► heare daemon (Mac, 127.0.0.1:7999)
     ▲                               │
     └─────── APNs push ◄────────────┘ (heare POSTs to Apple APNs)
```

Time estimate: iOS app skeleton + Tailscale auth + push registration ≈ 40–60 hours.

[STAT:confidence] MEDIUM — Tailscale iOS SDK + APNs are well-documented; primary uncertainty is heare-side APNs token management (~8 hrs).
Source: https://developer.apple.com/documentation/activitykit/starting-and-updating-live-activities-with-activitykit-push-notifications

---

[FINDING:I7] **iOS Live Activity — heare state on Lock Screen / Dynamic Island**

Using ActivityKit + WidgetKit, a Live Activity widget shows:
- State indicator: 🎙 Listening / 🧠 Thinking / 🔊 Speaking
- Last spoken text (truncated to 60 chars)
- Queue depth

heare daemon pushes updates via APNs `liveactivity` push type (iOS 17+). Live Activity tokens are obtained at startup and persisted; each state transition fires a lightweight APNs payload (<4KB).

Live Activities run up to 8 hours; updates via `pushTokenUpdates` async stream in SwiftUI.

[STAT:confidence] MEDIUM — requires iOS companion app (I6); independent of Tailscale (APNs is always reachable).
Source: https://developer.apple.com/videos/play/wwdc2023/10185/

---

[FINDING:I8] **Apple Watch haptics — "heare wants to speak" tap pattern**

Via the iOS companion app (I6), a Watch extension uses `WKInterfaceDevice.current().play(.notification)` to tap the user's wrist. A pattern can be built by sequencing haptic types with timed delays:

```swift
// WatchKit companion extension
func heareWantsSpeakHaptic() {
    let device = WKInterfaceDevice.current()
    // Two-tap "attention" pattern
    device.play(.directionUp)
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
        device.play(.notification)
    }
}
```

Triggered when: heare daemon emits an intent result but speaker volume is 0 (silent mode). Daemon POST to iOS companion → Watch Connectivity framework → haptic.

Limitation: WatchKit lacks CoreHaptics (custom waveforms); only ~11 preset WKHapticType values available.

[STAT:confidence] MEDIUM — requires iOS companion app; WatchKit haptics are well-documented.
Source: https://developer.apple.com/documentation/watchkit/wkinterfacedevice/play(_:)

---

[FINDING:I9] **HomeKit — heare as a HAP-python accessory ("Smart Speaker" service)**

`HAP-python` (PyPI: `HAP-python`, ikalchev/HAP-python) implements Apple's HomeKit Accessory Protocol in pure Python with asyncio. heare can register as a "Smart Speaker" or custom Switch accessory:
- **ON** characteristic → submit a speak intent ("Wake me up" automation).
- **Volume** characteristic → map to heare TTS volume.
- Siri: "Hey Siri, turn on heare" → triggers a configurable intent.

```python
from pyhap.accessory import Accessory
from pyhap.const import CATEGORY_SPEAKER

class HeareAccessory(Accessory):
    category = CATEGORY_SPEAKER

    @Accessory.run_at_interval(0)
    async def run(self):
        pass  # event-driven only

    def set_on(self, value):
        if value:
            # POST to /rpc intent.submit tool=bash args="say heare activated"
            asyncio.create_task(post_rpc("bash", "say heare activated"))
```

Bonjour/mDNS advertisement is automatic via zeroconf. Pairing via QR code scan in iOS Home app.

[STAT:confidence] MEDIUM — HAP-python is stable (v4.x on PyPI); primary risk is asyncio event loop sharing with heare's existing loop (solvable with `asyncio.create_task`).
Source: https://github.com/ikalchev/HAP-python ; https://pypi.org/project/HAP-python/

---

[FINDING:I10] **Git hooks — post-commit narration, per-repo opt-in**

A per-repo `.git/hooks/post-commit` shell script (or installed globally via `git config core.hooksPath`) POSTs commit metadata to heare `/rpc/speak`:

```bash
#!/usr/bin/env bash
# .git/hooks/post-commit  (chmod +x)
BRANCH=$(git rev-parse --abbrev-ref HEAD)
MSG=$(git log -1 --pretty=%s)
STATS=$(git diff --stat HEAD~1 HEAD 2>/dev/null | tail -1)
TOKEN=$(cat ~/.config/heare/token 2>/dev/null || echo "")
[ -z "$TOKEN" ] && exit 0
curl -s -X POST http://127.0.0.1:7999/rpc/speak   -H "Authorization: Bearer $TOKEN"   -H "Content-Type: application/json"   -d "{\"text\":\"Committed: $MSG on $BRANCH. $STATS\"}" &
```

Per-repo opt-in: only repos with the hook file installed narrate. Global opt-in: `git config --global core.hooksPath ~/.config/heare/githooks/`.

[STAT:confidence] HIGH — git hooks are a stable, OS-portable mechanism; curl to /rpc is the only dependency.
[STAT:n] Affects every `git commit` in opted-in repos.

---

[FINDING:I11] **Claude Code hooks — heare narrates agent lifecycle events**

Claude Code supports HTTP-type hooks that POST JSON to a local URL. heare's `/rpc` endpoint can receive these events directly. Configuration in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type":"http",
      "url":"http://127.0.0.1:7999/rpc/claude-hook",
      "async":true}]}],
    "PostToolUse": [{"matcher":"Bash","hooks":[{"type":"http",
      "url":"http://127.0.0.1:7999/rpc/claude-hook",
      "async":true}]}],
    "Stop": [{"hooks":[{"type":"http",
      "url":"http://127.0.0.1:7999/rpc/claude-hook",
      "async":true}]}]
  }
}
```

heare's `/rpc/claude-hook` handler maps events to TTS:
- `SessionStart` → "Claude session started"
- `PostToolUse` (Bash) → "Ran: <command[:40]>"
- `Stop` → "Claude finished"
- `SubagentStart` → "Spawning subagent"
- `TaskCompleted` → "Task done"

The full event taxonomy includes 25+ events (SessionStart, PostToolUse, PreToolUse, Stop, StopFailure, SubagentStart, SubagentStop, TaskCreated, TaskCompleted, Notification, etc.).

[STAT:confidence] HIGH — Claude Code HTTP hooks are fully documented with localhost URL support confirmed; async:true means hooks don't block Claude's execution.
Source: https://code.claude.com/docs/en/hooks

---

[FINDING:I12] **Slack — app_mention subscription via Events API**

A Slack app with `app_mention` event subscription (Slack Events API + Socket Mode) lets Nazar type `@heare що в мене у календарі?` in any channel → the Slack bot receives the mention → POSTs to heare `/rpc`:

```python
# slack_bridge.py (standalone process, ~60 LOC)
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import httpx, os

app = App(token=os.environ["SLACK_BOT_TOKEN"])

@app.event("app_mention")
def handle_mention(event, say):
    text = event["text"].split(">", 1)[-1].strip()
    httpx.post("http://127.0.0.1:7999/rpc",
               headers={"Authorization": f"Bearer {os.environ['HEARE_RPC_TOKEN']}"},
               json={"jsonrpc":"2.0","method":"intent.submit","id":1,
                     "params":{"tool":"bash","args":text}})
    say("Queued for heare.")

SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
```

Socket Mode eliminates the need for a public HTTPS endpoint — runs entirely on localhost.

[STAT:confidence] MEDIUM — Slack Events API + Socket Mode is stable; primary friction is Slack app registration (one-time, ~30 min).
Source: https://api.slack.com/events/app_mention ; https://api.slack.com/rtm

---

[FINDING:I13] **Linear / GitHub webhook inbox — action result delivery**

(Covered in stage 8; brief integration here.) heare's ActionWorker can POST action results as GitHub PR comments or Linear comments via their respective REST APIs. The reverse — Linear/GitHub events triggering heare — uses the same webhook-to-/rpc pattern as Slack (I12). A single webhook receiver process routes `github.push`, `linear.issue.created` events to `/rpc/speak` with templated Ukrainian utterances.

[STAT:confidence] HIGH — both GitHub and Linear webhooks are well-documented REST patterns; heare /rpc is the common sink.

---

## Effort Matrix

| Integration | LOC estimate | One-time setup | Dependencies |
|---|---|---|---|
| JSON-RPC /rpc API (I1) | ~60 | None | aiohttp |
| Raycast extension (I2) | ~120 TS | Raycast dev env | @raycast/api, node-fetch |
| Shortcuts URL scheme (I3) | ~20 Swift | Platypus / Script | hearectl on PATH |
| Alfred workflow (I4) | ~30 bash | Alfred Powerpack | curl, jq |
| Services menu (I5) | ~15 bash | Automator | curl |
| iOS companion app (I6) | ~40-60 hrs | Apple dev account | Tailscale, APNs |
| Live Activity (I7) | +20 hrs on I6 | Apple dev account | ActivityKit |
| Watch haptics (I8) | +8 hrs on I6 | Watch pairing | WatchKit |
| HomeKit (I9) | ~80 Python | None | HAP-python, zeroconf |
| Git hooks (I10) | ~15 bash | Per-repo chmod +x | curl |
| Claude Code hooks (I11) | ~30 Python | settings.json edit | aiohttp route |
| Slack bridge (I12) | ~60 Python | Slack app registration | slack-bolt, httpx |

**Recommended implementation order:**
1. I1 (JSON-RPC /rpc) — unlocks all others
2. I11 (Claude Code hooks) — highest daily-use value for Nazar
3. I10 (git hooks) — zero UI, high delight
4. I2 (Raycast) — fastest launcher integration
5. I9 (HomeKit) — Siri bridge, surprisingly low effort
6. I6+I7+I8 (iOS/Watch) — highest effort, highest mobility value

---

## Limitations

[LIMITATION] iOS companion app (I6–I8) requires an Apple Developer account ($99/yr) and native Swift development; Python cannot produce iOS apps directly.

[LIMITATION] HomeKit (I9): HAP-python's asyncio loop must share heare's event loop; concurrent accessory state updates during heavy TTS processing may introduce latency spikes.

[LIMITATION] Raycast extension (I2) requires the extension to be sideloaded in developer mode or published to Raycast Store; Store review adds latency.

[LIMITATION] Slack bridge (I12) requires Socket Mode which keeps a persistent WebSocket open — adds ~5MB RAM and one background process.

[LIMITATION] Claude Code HTTP hooks fire asynchronously (`async:true`); there is no delivery guarantee if heare daemon is not running. Missed events are silently dropped.

[LIMITATION] All integrations assume heare daemon is running on localhost. No daemon = silent failure across all spokes. A `launchd` plist for auto-start on login is a prerequisite for reliable ecosystem integration.

---

## Sources

1. https://www.raycast.com/blog/how-raycast-api-extensions-work (Raycast Node.js runtime, network access)
2. https://developers.raycast.com (Raycast API reference)
3. https://support.apple.com/guide/shortcuts-mac/run-a-shortcut-from-a-url-apd624386f42/mac (Shortcuts URL scheme)
4. https://www.alfredapp.com/help/workflows/inputs/script-filter/ (Alfred Script Filter)
5. https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/MakeaSystem-WideService.html (macOS Services/NSServices)
6. https://github.com/ikalchev/HAP-python (HAP-python HomeKit library)
7. https://pypi.org/project/HAP-python/ (HAP-python PyPI)
8. https://developer.apple.com/documentation/watchkit/wkinterfacedevice/play(_:) (WatchKit haptics API)
9. https://developer.apple.com/documentation/activitykit/starting-and-updating-live-activities-with-activitykit-push-notifications (ActivityKit + APNs)
10. https://code.claude.com/docs/en/hooks (Claude Code hooks reference — HTTP handler, all 25 event types)
11. https://api.slack.com/events/app_mention (Slack app_mention Events API)
12. https://api.slack.com/rtm (Slack Socket Mode / legacy RTM)

---

[STAGE_COMPLETE:11]
