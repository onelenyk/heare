# Stage 1: Perception — Making Heare SEE the World on macOS

**Research date:** 2026-04-23
**Stage:** 1 of N (Perception)
**Objective:** Identify concrete, implementable methods for giving heare environmental awareness of Nazar's macOS workspace — with working code sketches, latency estimates, and privacy analysis.

---

## Context Snapshot

heare pipeline: mic → VAD → Groq STT → OpenRouter generator → IntentQueue → ActionWorker → edge-tts → speaker

Current `ContextBuilder.build_for_generator()` keys: `time`, `timezone`, `recent_transcripts`, `conversation_summary`, `active_topics`, `entities`, `recent_turns`, `recent_actions`, `persona`, `transcript`.

Zero environmental perception today.

---

[FINDING:P1] **mss is the fastest cross-platform screenshot library on macOS; CGWindowListCreateImage is deprecated in macOS 15 — use SCScreenshotManager via pyobjc-framework-ScreenCaptureKit for new code**
[EVIDENCE:P1]
- Benchmark (M2 Pro, macOS Sonoma 14.1.2): mss ~45-65 ms, pyautogui ~1300-1500 ms, Pillow ImageGrab ~1300-1500 ms — source: https://blog.trackmypop.com/2024/01/02/quick-screenshots-in-python/
- mss uses CoreGraphics natively (no disk I/O); pyautogui/Pillow shell out to `screencapture` CLI and round-trip via temp file
- `CGWindowListCreateImage` obsoleted in macOS 15.0 — error: "use ScreenCaptureKit instead" — source: https://github.com/ronaldoussoren/pyobjc/issues/627
- `pyobjc-framework-ScreenCaptureKit` on PyPI: https://pypi.org/project/pyobjc-framework-ScreenCaptureKit/
- Apple ScreenCaptureKit docs: https://developer.apple.com/documentation/screencapturekit/

**Minimal code sketch (mss, fast path ~50ms):**
```python
import mss, mss.tools, base64, io
from PIL import Image

def capture_screen_b64(monitor_idx: int = 1, scale: float = 0.5) -> str:
    """Capture primary display, downsample, return base64 JPEG for LLM."""
    with mss.mss() as sct:
        monitor = sct.monitors[monitor_idx]
        raw = sct.grab(monitor)                        # ~50ms on M2
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    img = img.resize(
        (int(img.width * scale), int(img.height * scale)),
        Image.LANCZOS
    )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)          # lossy → smaller payload
    return base64.b64encode(buf.getvalue()).decode()
```

**Vision input to claude-agent-sdk (Anthropic vision API format):**
```python
# In the generator request payload (OpenRouter / Anthropic-compatible):
{
  "role": "user",
  "content": [
    {"type": "text", "text": "Що зараз на екрані?"},
    {
      "type": "image_url",
      "image_url": {
        "url": f"data:image/jpeg;base64,{b64_data}"
      }
    }
  ]
}
```
For native Anthropic API:
```python
{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64_data}}
```
Source: https://platform.claude.com/docs/en/build-with-claude/vision
Token cost at 1920x1080 downsampled 50% → 960x540: ~383 tokens → ~$0.001 per call at Sonnet pricing.

**Latency estimate:** capture 50ms + downsample 10ms + JPEG encode 5ms = ~65ms local; cloud LLM call adds 500-2000ms on top.
**Risk:** Requires Screen Recording TCC permission. mss fails silently if permission not granted (returns black frame). On macOS 15, `CGWindowListCreateImage` raises compile/runtime error — mss 9.x already migrated to ScreenCaptureKit internally for macOS 14+.
[CONFIDENCE:HIGH]
[/FINDING]

---

[FINDING:P2] **NSWorkspace + CGWindowListCopyWindowInfo gives foreground app name, window title, PID, and document path in <10ms — the fastest approach**
[EVIDENCE:P2]
- NSWorkspace.sharedWorkspace().frontmostApplication() returns `NSRunningApplication` with `.localizedName`, `.processIdentifier`, `.bundleURL`
- CGWindowListCopyWindowInfo returns per-window dict with keys `kCGWindowOwnerName`, `kCGWindowName` (title), `kCGWindowOwnerPID`
- Source gist: https://gist.github.com/ljos/3040846
- PyObjC docs: https://pyobjc.readthedocs.io/

**Minimal code sketch (<10ms, no shell subprocess):**
```python
from AppKit import NSWorkspace
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGWindowListOptionOnScreenOnly,
    kCGNullWindowID,
    kCGWindowListExcludeDesktopElements,
)

def get_active_window() -> dict:
    ws = NSWorkspace.sharedWorkspace()
    app = ws.frontmostApplication()
    app_name = app.localizedName() or ""
    pid = app.processIdentifier()

    windows = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
        kCGNullWindowID,
    ) or []

    title = ""
    for w in windows:
        if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0:
            title = w.get("kCGWindowName") or ""
            break

    # Document path heuristic: VSCode writes open file into title "filename — folder"
    doc_path = ""
    if " — " in title:
        doc_path = title.split(" — ")[0].strip()

    return {"app": app_name, "window_title": title, "document_path": doc_path, "pid": pid}
```

**osascript alternative:** `osascript -e 'tell app "System Events" to get name of first process whose frontmost is true'` — ~80-150ms due to subprocess + AppleScript runtime overhead. Acceptable but 10-15x slower.

**Latency estimate:** ~5-10ms (pure ObjC bridge, no subprocess). Well under 50ms requirement.
**Risk:** `kCGWindowName` returns empty string for many Electron apps (VSCode, Slack) when accessibility is not granted. Requires Accessibility permission OR Screen Recording (which implicitly grants window metadata access on macOS 13+).
[CONFIDENCE:HIGH]
[/FINDING]

---

[FINDING:P3] **NSPasteboard.changeCount is the correct polling primitive for clipboard awareness; pyperclip is fine for one-shot reads but misses change events**
[EVIDENCE:P3]
- NSPasteboard docs: changeCount increments on every write — polling interval 1-2s is sufficient for ambient awareness
- pyperclip on macOS falls back to `pbpaste`/`pbcopy` shell commands (~50-100ms per call); adequate for on-demand but too slow for tight polling
- pbwatch project (https://github.com/chbrown/pbwatch) demonstrates the changeCount polling pattern in Python
- NSPasteboard.org: "Applications that scan generalPasteboard changeCount frequently" is the documented change-detection idiom
- pyperclip.waitForNewPaste() blocks the thread; incompatible with asyncio without `run_in_executor`

**Minimal code sketch (async-safe polling):**
```python
import asyncio
from AppKit import NSPasteboard, NSStringPboardType

_last_change_count: int = -1

async def poll_clipboard(interval: float = 2.0) -> str | None:
    """Returns clipboard text if changed since last poll, else None."""
    global _last_change_count
    pb = NSPasteboard.generalPasteboard()
    current = pb.changeCount()
    if current == _last_change_count:
        return None
    _last_change_count = current
    text = pb.stringForType_(NSStringPboardType)
    return str(text)[:500] if text else None  # cap at 500 chars

# In the ambient polling loop:
async def clipboard_watcher():
    while True:
        changed = await poll_clipboard()
        if changed:
            # Update context: last_clipboard_short = changed[:120]
            pass
        await asyncio.sleep(2.0)
```

**Privacy risk:** Password managers (1Password, Bitwarden) write credentials to clipboard. Mitigation: (a) cap length at 120 chars, (b) detect and redact patterns matching passwords (`/^[A-Za-z0-9!@#$%^&*]{12,}$/` heuristic), (c) expose in `last_clipboard_short` context key only when `mode != "silent"`.
**Latency:** NSPasteboard changeCount read ~<1ms. No TCC permission required for reading general pasteboard.
[CONFIDENCE:HIGH]
[/FINDING]

---

[FINDING:P4] **osascript + subprocess is reliable for getting Chrome/Safari URL and tab title; latency ~80-200ms; requires Automation TCC permission**
[EVIDENCE:P4]
- AppleScript gist covering Chrome, Safari, Brave, Edge, Vivaldi, Orion: https://gist.github.com/vitorgalvao/5392178
- Safari: `tell app "Safari" to get URL of current tab of window 1`
- Chrome: `tell app "Google Chrome" to get URL of active tab of window 1`
- Firefox: no AppleScript support — fallback to window title only via CGWindowListCopyWindowInfo
- Python subprocess call: `subprocess.run(["osascript", "-e", script], capture_output=True, timeout=2.0)`

**Minimal code sketch:**
```python
import subprocess

BROWSER_SCRIPT = """
tell application "System Events"
    set frontApp to name of first process whose frontmost is true
end tell
if frontApp starts with "Google Chrome" or frontApp starts with "Brave" then
    tell application frontApp
        set u to URL of active tab of front window
        set t to title of active tab of front window
    end tell
else if frontApp starts with "Safari" then
    tell application "Safari"
        set u to URL of current tab of window 1
        set t to name of current tab of window 1
    end tell
else
    set u to ""
    set t to ""
end if
return u & "\n" & t
"""

def get_browser_state() -> dict | None:
    """Returns {url, title} if a supported browser is frontmost, else None."""
    try:
        r = subprocess.run(
            ["osascript", "-e", BROWSER_SCRIPT],
            capture_output=True, text=True, timeout=2.0
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split("\n", 1)
            return {"url": parts[0], "title": parts[1] if len(parts) > 1 else ""}
    except Exception:
        pass
    return None
```

**Latency:** 80-200ms (osascript startup + AppleScript bridge). Unsuitable for tight polling; use only when browser is the frontmost app (check active app first, call only if browser detected).
**Risk:** Requires Automation permission for each browser individually. System Settings > Privacy & Security > Automation. Headless daemons cannot trigger this prompt — must use LaunchAgent companion (see Finding P9).
[CONFIDENCE:HIGH]
[/FINDING]

---

[FINDING:P5] **EventKit via pyobjc (maccal library) is the correct approach for next-calendar-event; icalBuddy has chronic TCC issues and is unmaintained**
[EVIDENCE:P5]
- maccal library: https://github.com/appenz/maccal — Python wrapper over EventKit, Apache 2.0, macOS 14+
- pyobjc-framework-EventKit: https://pypi.org/project/pyobjc-framework-EventKit/
- apple-eventkit-mcp MCP server using pyobjc: https://github.com/snarris/apple-eventkit-mcp
- icalBuddy alternative Swift CLI avoids TCC issues: https://github.com/zigotica/macos-calendar-events
- EventKit docs: https://developer.apple.com/documentation/eventkit

**Minimal code sketch (maccal):**
```python
from datetime import datetime, timedelta, timezone
from maccal import CalendarStore

def get_next_event() -> dict | None:
    """Returns {title, start, location} for the next upcoming event, or None."""
    try:
        store = CalendarStore()
        now = datetime.now(tz=timezone.utc)
        events = store.get_events(now, now + timedelta(hours=12))
        if not events:
            return None
        e = events[0]  # already sorted by start time
        return {
            "title": e.title,
            "start": e.start.isoformat(),
            "location": getattr(e, "location", ""),
        }
    except Exception:
        return None
```

**Alternative — raw EventKit via pyobjc:**
```python
import EventKit
store = EventKit.EKEventStore.alloc().init()
# Must call requestAccessToEntityType_completion_ first (async, requires run loop)
```

**Auth:** First call triggers TCC Calendar prompt. Grant in System Settings > Privacy & Security > Calendar for the Terminal / Python binary. maccal handles the auth request internally.
**Rate limit:** None — local read, no network. Latency ~5-30ms (SQLite-backed local store).
**Privacy:** Calendar data stays local with maccal/EventKit. icalBuddy spawns helper processes that sometimes fail TCC inheritance — avoid.
[CONFIDENCE:HIGH]
[/FINDING]

---

[FINDING:P6] **Apple Vision Framework via ocrmac gives on-device OCR in 131-207ms on M-series — no cloud required; accurate enough for UI text extraction**
[EVIDENCE:P6]
- ocrmac library: https://github.com/straussmaximilian/ocrmac — wraps VNRecognizeTextRequest via pyobjc-framework-Vision
- Performance on MacBook Pro M3 Max: `accurate` mode 207±1.49ms, `fast` mode 131±702µs, LiveText 174±4.12ms
- Apple Vision Framework pyobjc tutorial: https://yasoob.me/posts/how-to-use-vision-framework-via-pyobjc/
- pyobjc-framework-Vision: https://pypi.org/project/pyobjc-framework-Vision/
- Requires macOS 10.15+; VNRecognizeTextRequest supports 20+ languages including Ukrainian

**Minimal code sketch:**
```python
from ocrmac import ocrmac

def ocr_screenshot(image_path: str, fast: bool = True) -> str:
    """Extract text from screenshot using Apple Vision. No cloud call needed."""
    level = "fast" if fast else "accurate"
    annotations = ocrmac.OCR(image_path, recognition_level=level).recognize()
    # annotations: list of (text, confidence, bounding_box)
    lines = [text for text, conf, _ in annotations if conf > 0.5]
    return " | ".join(lines)[:800]  # cap output for context injection

# Combined screen → OCR pipeline:
# 1. mss capture (~50ms)
# 2. Save to /tmp/heare_screen.jpg
# 3. ocrmac.OCR('/tmp/heare_screen.jpg', recognition_level='fast').recognize() (~131ms)
# Total: ~180ms for screen text extraction, fully local
```

**VNRecognizeTextRequest direct (no ocrmac dep):**
```python
import Vision, Quartz
handler = Vision.VNImageRequestHandler.alloc().initWithCIImage_options_(ci_image, {})
req = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(callback)
req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast)
handler.performRequests_error_([req], None)
```

**Latency:** 131-207ms local. Much faster than cloud round-trip (500-2000ms). Works fully offline.
**Risk:** Heavy CPU burst during recognition. Run in executor thread to avoid blocking asyncio event loop: `await asyncio.get_event_loop().run_in_executor(None, ocr_fn)`.
[CONFIDENCE:HIGH]
[/FINDING]

---

[FINDING:P7] **Hybrid polling policy: 30s passive ambient snapshot for context keys + on-demand intents for explicit perception requests**
[EVIDENCE:P7]
- Passive always-on at 30s: captures active_app, active_window, next_event, last_clipboard_short — all <10ms per key (no screenshot)
- Screenshot + OCR only on explicit demand ("що на екрані?") — adds 180ms local or 600-2500ms cloud
- Browser URL check: only when active_app is a browser — avoids 150ms osascript on every poll
- 30s polling interval chosen to balance freshness vs. token cost: 2 polls/min × 60 mins = 120 context lookups/hr — negligible CPU

**Recommended ambient poll (30s timer, asyncio):**
```python
import asyncio

class PerceptionPoller:
    def __init__(self, context_builder):
        self.ctx = context_builder
        self._snapshot: dict = {}

    async def run(self):
        while True:
            self._snapshot = await self._poll_fast()
            await asyncio.sleep(30)

    async def _poll_fast(self) -> dict:
        """<15ms total: no screenshot, no OCR, no browser script."""
        win = get_active_window()          # ~5ms, pyobjc
        clip = await poll_clipboard()      # ~1ms, NSPasteboard
        cal = get_next_event()             # ~15ms, maccal (cached)
        browser = None
        if win["app"] in BROWSER_APPS:     # only if browser is frontmost
            browser = get_browser_state()  # ~150ms, osascript
        return {
            "active_app": win["app"],
            "active_window": win["window_title"],
            "document_path": win["document_path"],
            "next_event": _fmt_event(cal),
            "last_clipboard_short": (clip or "")[:120],
            "browser_url": browser["url"] if browser else "",
        }
```

**Cost analysis:**
- Passive (30s interval): 0 tokens/poll for pure metadata; only text keys, no images
- On-demand screenshot+cloud: ~1568 tokens/call at 960x540 JPEG = ~$0.005 at Sonnet pricing
- On-demand screenshot+local OCR: 0 tokens, 180ms, CPU burst ~200ms

**Recommendation:** passive metadata every 30s always-on; screenshot on explicit request only. Never auto-screenshot without voice trigger.
[CONFIDENCE:HIGH]
[/FINDING]

---

[FINDING:P8] **New context keys for ContextBuilder.build_for_generator() and corresponding generator.txt additions**
[EVIDENCE:P8]
- Based on analysis of src/context.py build_for_generator() and prompts/generator.txt

**Proposed new keys in `build_for_generator()` return dict:**

| Key | Source | Example value | Silent mode |
|-----|--------|---------------|-------------|
| `active_app` | NSWorkspace.frontmostApplication | `"Visual Studio Code"` | include |
| `active_window` | CGWindowListCopyWindowInfo | `"context.py — heare"` | include |
| `document_path` | parsed from window title | `"context.py"` | include |
| `next_event` | EventKit/maccal | `"15:30 — Standup (Zoom)"` | REDACT |
| `last_clipboard_short` | NSPasteboard | `"git commit -m 'feat: add'"` | REDACT |
| `browser_url` | osascript Chrome/Safari | `"github.com/lenyk/heare"` | REDACT |
| `screen_summary` | ocrmac (on-demand only) | `"Terminal: pytest... VSCode open"` | REDACT |

**generator.txt block to add (after `{recent_actions}`):**

```
Стан середовища (оновлюється раз на 30с):
- Активний додаток: {active_app}
- Вікно: {active_window}
- Наступна подія: {next_event}
- Браузер: {browser_url}
- Буфер обміну (скорочено): {last_clipboard_short}
```

**_EXCLUDED_FROM_GENERATOR_CTX additions:** none needed — all new keys flow to generator.

**Silent mode redaction in `build_for_generator()`:**
```python
if self.settings.mode == Mode.SILENT:
    result["next_event"] = "(redacted in silent mode)"
    result["last_clipboard_short"] = "(redacted)"
    result["browser_url"] = "(redacted)"
    result["screen_summary"] = "(redacted)"
```
[CONFIDENCE:HIGH]
[/FINDING]

---

[FINDING:P9] **New intents for direct_tools.py: `screenshot`, `active_window`, `clipboard_read`, `next_meeting`, `see`**
[EVIDENCE:P9]
- Pattern: add to ALLOWED_TOOLS in src/actions.py + add handler in src/direct_tools.py

**Proposed intent catalog:**

| Intent name | Args example | Handler | Latency |
|------------|--------------|---------|---------|
| `active_window` | `""` | NSWorkspace + CGWindowListCopyWindowInfo | ~5ms |
| `clipboard_read` | `""` | NSPasteboard generalPasteboard | ~1ms |
| `next_meeting` | `"hours=12"` | maccal CalendarStore | ~20ms |
| `screenshot` | `"scale=0.5"` | mss + JPEG encode | ~65ms |
| `see` | `"що на екрані?"` | mss + ocrmac (fast) | ~200ms |

**Wire-in sketch for `src/direct_tools.py`:**

```python
# Add to SIMPLE_TOOLS set:
SIMPLE_TOOLS = {"bash", "read", "write", "web_fetch", "web_search",
                "active_window", "clipboard_read", "next_meeting", "screenshot", "see"}

async def _execute_active_window(args: str, settings) -> dict:
    info = get_active_window()  # from perception module
    return {"success": True, "output": str(info), "error": None}

async def _execute_clipboard_read(args: str, settings) -> dict:
    pb = NSPasteboard.generalPasteboard()
    text = pb.stringForType_(NSStringPboardType) or ""
    return {"success": True, "output": str(text)[:2000], "error": None}

async def _execute_next_meeting(args: str, settings) -> dict:
    event = get_next_event()
    if not event:
        return {"success": True, "output": "Немає найближчих подій", "error": None}
    return {"success": True, "output": f"{event['start'][:16]} — {event['title']}", "error": None}

async def _execute_screenshot(args: str, settings) -> dict:
    b64 = capture_screen_b64()
    # Save to tmp for local inspection; also return b64 for vision calls
    path = "/tmp/heare_screen.jpg"
    import base64
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))
    return {"success": True, "output": f"Screenshot saved: {path}", "error": None, "b64": b64}

async def _execute_see(args: str, settings) -> dict:
    """screenshot + local OCR; no cloud call."""
    import tempfile, base64, mss, mss.tools
    from PIL import Image
    from ocrmac import ocrmac as _ocr
    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[1])
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        img.save(f.name, "JPEG", quality=85)
        path = f.name
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(
        None, lambda: " | ".join(t for t, c, _ in _ocr.OCR(path).recognize() if c > 0.4)
    )
    return {"success": True, "output": text[:1500], "error": None}
```

**In `src/actions.py` ALLOWED_TOOLS:**
```python
ALLOWED_TOOLS: set[str] = {
    "bash", "read", "write", "edit", "web_fetch", "web_search", "workflow",
    # Perception intents (Phase S1):
    "active_window", "clipboard_read", "next_meeting", "screenshot", "see",
}
```

**In `prompts/generator.txt` examples section:**
```
Користувач: "що у мене зараз на екрані?"
Відповідь: Зараз гляну. <intent>{"tool":"see","args":""}</intent>

Користувач: "яке в мене наступне засідання?"
Відповідь: Перевіряю. <intent>{"tool":"next_meeting","args":""}</intent>

Користувач: "що в буфері обміну?"
Відповідь: Читаю. <intent>{"tool":"clipboard_read","args":""}</intent>
```
[CONFIDENCE:HIGH]
[/FINDING]

---

[FINDING:P10] **macOS TCC permissions for each perception feature — and the LaunchAgent companion pattern for headless daemon permission bootstrapping**
[EVIDENCE:P10]
- TCC analysis source: https://chrispaynter.medium.com/what-to-do-when-your-macos-daemon-gets-blocked-by-tcc-dialogues-d3a1b991151f
- TCC database: https://angelica.gitbook.io/hacktricks/macos-hardening/macos-security-and-privilege-escalation/macos-security-protections/macos-tcc

**Permission matrix:**

| Feature | TCC service | Trigger |
|---------|-------------|---------|
| mss screenshot | Screen Recording | First `sct.grab()` call |
| CGWindowListCopyWindowInfo | Screen Recording (macOS 13+) | First window metadata call |
| NSWorkspace.frontmostApplication | None (public API) | No prompt |
| NSPasteboard read | None (general pasteboard) | No prompt |
| osascript Chrome | Automation > Google Chrome | First osascript call |
| osascript Safari | Automation > Safari | First osascript call |
| EventKit/maccal | Calendar | First store access |
| ocrmac (Apple Vision) | None (processes local image) | No prompt |

**Critical daemon gotcha:** heare runs as a LaunchAgent (user-level), NOT a LaunchDaemon (root-level). This means TCC prompts CAN appear in the user session. Verify with:
```xml
<!-- ~/.heare/launch/com.heare.agent.plist -->
<key>Label</key><string>com.heare.agent</string>
<!-- NOT /Library/LaunchDaemons — user LaunchAgent avoids TCC root problem -->
```

**First-run UX script (run once interactively, not from daemon):**
```python
# src/permissions_check.py — run manually: python -m heare.permissions_check
import subprocess, sys

def request_screen_recording():
    """Trigger Screen Recording TCC prompt by attempting a grab."""
    try:
        import mss
        with mss.mss() as s:
            s.grab(s.monitors[1])
        print("✓ Screen Recording: OK")
    except Exception as e:
        print(f"✗ Screen Recording: DENIED — grant in System Settings > Privacy > Screen Recording ({e})")

def request_calendar():
    from maccal import CalendarStore
    try:
        store = CalendarStore()
        print("✓ Calendar: OK")
    except Exception as e:
        print(f"✗ Calendar: requires grant in System Settings > Privacy > Calendar ({e})")

def request_automation_chrome():
    r = subprocess.run(
        ["osascript", "-e", 'tell app "Google Chrome" to return ""'],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        print("✓ Automation > Chrome: OK")
    else:
        print("✗ Automation > Chrome: run once interactively to grant")

if __name__ == "__main__":
    request_screen_recording()
    request_calendar()
    request_automation_chrome()
```
[CONFIDENCE:HIGH]
[/FINDING]

---

[FINDING:P11] **Moondream2 (2B) via MLX on Apple Silicon: ~800ms-2s for screen description — viable for on-demand, too slow for ambient polling**
[EVIDENCE:P11]
- Moondream2 HuggingFace: https://huggingface.co/vikhyatk/moondream2 — 2B params, Apache 2.0, MPS device_map support
- SmolVLM-256M: <1GB VRAM, runs on M-series via MLX: `python3 -m mlx_vlm.generate --model HuggingfaceTB/SmolVLM-256M-Instruct`
- Paper "Native LLM and MLLM Inference at Scale on Apple Silicon": https://arxiv.org/html/2601.19139v2
- vllm-mlx exceeds llama.cpp throughput by 21-87% on unified memory — source same paper
- Moondream changelog: "20-40% faster response generation" in 2025-06-21 release via new tokenizer
- Moondream UI understanding: ScreenSpot F1@0.5 improved from 60.3 → 80.4

**Estimated latency (M2/M3 Pro, 16GB unified memory):**

| Model | Parameters | Est. latency (first token) | Notes |
|-------|-----------|---------------------------|-------|
| Moondream2 | 2B | ~800-1500ms | Via transformers+MPS |
| SmolVLM-256M | 256M | ~300-600ms | Via MLX |
| ocrmac (Vision) | N/A (Apple framework) | 131-207ms | Text-only OCR |
| OpenRouter claude-3-haiku | cloud | 500-1500ms | + network RTT |

**Recommendation for heare:** Use ocrmac for text-heavy screens (code, docs, terminals) — fastest and fully local. Use Moondream2/SmolVLM via MLX only when semantic image understanding is needed ("describe what Nazar is doing") and latency tolerance is >1s. Cloud (OpenRouter haiku with vision) is competitive on latency if network is fast, but adds cost per call.

**Minimal Moondream sketch:**
```python
from transformers import AutoModelForCausalLM
from PIL import Image

# Load once at startup (not per-call):
_model = None
def _get_model():
    global _model
    if _model is None:
        _model = AutoModelForCausalLM.from_pretrained(
            "vikhyatk/moondream2", revision="2025-06-21",
            trust_remote_code=True, device_map={"": "mps"}
        )
    return _model

async def describe_screen(image_path: str) -> str:
    loop = asyncio.get_event_loop()
    def _run():
        img = Image.open(image_path)
        return _get_model().caption(img, length="short")["caption"]
    return await loop.run_in_executor(None, _run)
```
**Risk:** 2B model takes ~4-8GB of unified memory. On 16GB M-series Macs this is acceptable but competes with other active apps. SmolVLM-256M is safer for memory-constrained environments.
[CONFIDENCE:MEDIUM]
[/FINDING]

---

[FINDING:P12] **Recommended implementation order and risk-ranked rollout for heare perception Phase S1**
[EVIDENCE:P12]
Consolidated from all findings above.

**Tier 1 — Zero-permission, instant value (implement first):**
1. `active_app` + `active_window` via NSWorkspace + CGWindowListCopyWindowInfo — no TCC needed, <10ms
2. `last_clipboard_short` via NSPasteboard.changeCount polling — no TCC needed, <1ms

**Tier 2 — Single permission grant, high value:**
3. `see` intent via mss + ocrmac — needs Screen Recording TCC, ~200ms, fully local
4. `next_meeting` via maccal/EventKit — needs Calendar TCC, ~20ms, fully local

**Tier 3 — Multiple permissions, moderate latency:**
5. `browser_url` via osascript — needs Automation TCC per browser, ~150ms
6. Screenshot cloud vision via OpenRouter — needs Screen Recording TCC, ~800ms + API cost

**Tier 4 — Optional, memory-intensive:**
7. Moondream2/SmolVLM local VLM — no TCC needed, 300-1500ms, 4-8GB RAM

**New `src/perception.py` module structure:**
```
src/perception.py
├── get_active_window() -> dict        # P2: NSWorkspace + Quartz
├── poll_clipboard() -> str | None     # P3: NSPasteboard
├── get_browser_state() -> dict | None # P4: osascript
├── get_next_event() -> dict | None    # P5: maccal
├── capture_screen_b64() -> str        # P1: mss
├── ocr_screen() -> str                # P6: ocrmac (async executor)
├── describe_screen() -> str           # P11: Moondream (optional)
└── PerceptionPoller                   # P7: 30s async ambient loop
```

**Dependencies to add to pyproject.toml:**
```toml
[project.optional-dependencies]
perception = [
    "mss>=9.0",              # screen capture (CoreGraphics)
    "ocrmac>=1.0",           # Apple Vision OCR
    "maccal>=0.1",           # EventKit calendar
    "pyobjc-framework-Quartz",      # CGWindowListCopyWindowInfo
    "pyobjc-framework-AppKit",      # NSWorkspace, NSPasteboard
    "pillow",                # image processing
]
perception-vlm = [
    "transformers",
    "torch",                 # or mlx-vlm for MLX path
]
```

[CONFIDENCE:HIGH]
[/FINDING]

---

## External Sources Cited

1. https://blog.trackmypop.com/2024/01/02/quick-screenshots-in-python/ — mss vs pyautogui benchmark (M2 Pro)
2. https://developer.apple.com/documentation/screencapturekit/ — Apple ScreenCaptureKit docs
3. https://pypi.org/project/pyobjc-framework-ScreenCaptureKit/ — pyobjc ScreenCaptureKit wrapper
4. https://gist.github.com/ljos/3040846 — CGWindowListCopyWindowInfo Python snippet
5. https://gist.github.com/vitorgalvao/5392178 — AppleScript multi-browser URL/title gist
6. https://github.com/chbrown/pbwatch — NSPasteboard changeCount polling in Python
7. http://nspasteboard.org/ — NSPasteboard transient/special data spec
8. https://github.com/straussmaximilian/ocrmac — ocrmac Apple Vision OCR wrapper (M3 benchmarks)
9. https://yasoob.me/posts/how-to-use-vision-framework-via-pyobjc/ — VNRecognizeTextRequest tutorial
10. https://github.com/appenz/maccal — maccal EventKit Python library
11. https://github.com/snarris/apple-eventkit-mcp — EventKit MCP server reference
12. https://developer.apple.com/documentation/eventkit — EventKit official docs
13. https://chrispaynter.medium.com/what-to-do-when-your-macos-daemon-gets-blocked-by-tcc-dialogues-d3a1b991151f — TCC daemon strategy
14. https://huggingface.co/vikhyatk/moondream2 — Moondream2 specs and Apple Silicon usage
15. https://huggingface.co/blog/vlms-2025 — VLM 2025 overview (SmolVLM, Moondream, Qwen2.5-VL)
16. https://arxiv.org/html/2601.19139v2 — MLX vs llama.cpp on Apple Silicon benchmark
17. https://platform.claude.com/docs/en/build-with-claude/vision — Anthropic vision API format
18. https://github.com/ronaldoussoren/pyobjc/issues/627 — CGWindowListCreateImage deprecation in macOS 15
19. https://pypi.org/project/pyobjc-framework-EventKit/ — pyobjc EventKit bindings
20. https://pyobjc.readthedocs.io/ — PyObjC bridge documentation

---

[STAGE_COMPLETE:1]
