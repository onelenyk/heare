# Plan: Indication Subsystem

> Goal: the user always knows what the heare daemon is doing — via short
> non-speech sound cues, a status row in the watch dashboard, and macOS
> Notification Center alerts — with mode-aware gating, per-kind toggles,
> and zero risk to the audio pipeline.

Status: DRAFT v2 (planner output, ready for PRD breakdown into user stories)
Owner: heare core
Branch base: `s2s-realtime`
Target file count: ~12 new + ~17 edited
Estimated complexity: HIGH (cross-cutting; touches pipecat pipeline, async lifecycle, config, watch UI)


## 1. Architecture

### 1.1 Module layout

```
src/
  indication.py            # NEW — public Indication facade + IndicationKind enum + Backend protocol + IndicationCueFrame
  indication_backends/     # NEW package
    __init__.py
    sound.py               # NEW — SoundBackend: generates PCM cues at startup, injects via SoundCueProcessor
    visual.py              # NEW — VisualBackend: writes status rows to ~/.heare/indication.jsonl (read by watch.py)
    notification.py        # NEW — NotificationBackend: subprocess-osascript invocation (argv-passed, never interpolated)
  indication_assets.py     # NEW — pure numpy oscillator/envelope helpers (no I/O); produces 16-bit PCM bytes
  pipeline.py              # EDIT — instantiate Indication, wire SoundCueProcessor after TTS, capture main loop ref
  watch.py                 # EDIT — add indication panel that tails indication.jsonl
  config.py                # EDIT — add IndicationSettings dataclass + nested load
  decider.py               # EDIT — fire indication on AWAITING_CONFIRMATION + last-5s timeout; honor IndicationCueFrame for STT cooldown
  generator.py             # EDIT — fire on intent submit / cancel / OpenRouter fallback; honor IndicationCueFrame
  speaker_processor.py     # EDIT — fire on auto-enroll owner/guest; honor IndicationCueFrame for tagger gating
  actions.py               # EDIT — fire on queue rejection / long-running >5s / cancel-by-user
  main.py                  # EDIT — fire on startup (post-StartFrame) / shutdown / mic disconnect / on_action_result/error
  direct_tools.py          # EDIT — fire on re_enroll start/finish + auto-enroll events (off-loop branch)
  speaker_namer.py         # EDIT — fire on guest rename + new guest auto-enrolled
  openrouter_cli.py        # EDIT — fire on timeout (rethrown as OpenRouterError; caught in generator.py)
  mcp_utils.py             # EDIT — fire on auth required / disconnected
  heartbeat.py             # EDIT — fire on heartbeat tick (Tier 3)

tests/
  test_indication.py            # NEW — facade + cooldown + mode gating + quiet hours + thread-safe notify
  test_indication_assets.py     # NEW — PCM generation determinism + length
  test_indication_sound.py      # NEW — SoundBackend with FakeProcessor (asserts OutputAudioRawFrame + IndicationCueFrame pushed)
  test_indication_visual.py     # NEW — VisualBackend (asserts JSON line written)
  test_indication_notification.py  # NEW — NotificationBackend (asserts argv passing; injection regression)
  test_indication_wiring.py     # NEW — fake Indication injected into decider/generator/main; asserts notify(...) calls
  integration/test_indication_no_phantom.py  # NEW — live STT + cue → asserts no phantom transcript within 3s
  test_indication_runtime_disable.py  # NEW — runtime enabled=false flips → backends drained cleanly
```

### 1.2 Data flow

```
                                                ┌──────────────────────────┐
                                                │  Indication (facade)     │
                                                │   notify(kind, ...)      │
   producers (any module) ───── notify ───────► │   ↓ gate (mode, cooldown,│
                                                │     quiet_hours,         │
                                                │     per-kind toggles)    │
                                                │   ↓ thread-safe dispatch │
                                                │   ↓ fanout to backends   │
                                                └────┬───────┬────────┬────┘
                                                     ▼       ▼        ▼
                                              SoundBackend Visual  Notification
                                                 │        │           │
                                                 ▼        ▼           ▼
                                 ┌────────────────────────┐ jsonl    osascript
                                 │ SoundCueProcessor      │ file     subprocess
                                 │ emits:                 │  │       (argv-only)
                                 │  IndicationCueFrame(↑) │  │         │
                                 │  OutputAudioRawFrame   │  │         │
                                 │  IndicationCueFrame(↓) │  │         │
                                 └────────┬───────────────┘  │         │
                                          ▼                  ▼         ▼
                                  transport.output()      watch.py   Notification Center
                                          │
                                          └─► IndicationCueFrame is also observed
                                              upstream (decider, generator,
                                              speaker_tagger) to gate STT/transcript
                                              processing during cue playback,
                                              preventing echo-back phantoms.
```

### 1.3 Pipecat pipeline change

`src/pipeline.py:128-141` currently builds:

```
[transport.input(), (speaker_buffer?), stt, (speaker_tagger?), generator, tts, transport.output()]
```

Insert one new processor **after `tts` and before `transport.output()`**:

```
[..., generator, tts, sound_cue_processor, transport.output()]
```

`sound_cue_processor` is a `FrameProcessor` that:
- Forwards every frame downstream unchanged.
- Exposes a thread-safe `enqueue_cue(pcm_bytes)` method called by `SoundBackend`.
- When a cue is enqueued, emits this exact 3-frame sequence downstream:
  1. `IndicationCueFrame(start=True)` — propagates through the graph (system frame; broadcast both upstream+downstream by pipecat) so STT/decider/generator/speaker_tagger gate themselves.
  2. `OutputAudioRawFrame(audio=pcm, sample_rate=24000, num_channels=1)` — the only `transport.output()`-routable raw audio frame in pipecat. **A bare `AudioRawFrame` will NOT be played** — it's the wrong subclass. We use `OutputAudioRawFrame` explicitly.
  3. `IndicationCueFrame(start=False)` — pushed `cue_duration_ms + 200ms` later (cooldown padding) so STT echo-suppression mirrors the existing `bot_speaking_cooldown_seconds` semantics.
- Skips emission while `BotStartedSpeakingFrame` ↔ `BotStoppedSpeakingFrame` is open (don't talk over TTS); buffer up to 1 cue (`asyncio.Queue(maxsize=1)`). If a second cue arrives while the first is still queued, drop the second and increment a `cues_dropped` counter exposed in logs/visual.

**Echo-back gating contract.** Consumers that inspect transcripts add a one-line check on a new `_indication_speaking: bool` flag:
- `decider.py:502-509` (existing `_bot_speaking` block): same pattern, second flag.
- `generator.py:179` (transcript drop block): same pattern.
- `speaker_processor.py` SpeakerTagger: skip embedding ingestion while flag is set.

The flag is set on `IndicationCueFrame(start=True)` and cleared on `IndicationCueFrame(start=False)`. We deliberately do NOT reuse `BotStartedSpeakingFrame` because that frame muzzles the user for ~2.5s per beep via the existing `bot_speaking_cooldown_seconds` mechanism — wrong direction. `IndicationCueFrame` carries identical semantics scoped to cue duration plus a 200ms tail.

Why pre-built PCM (not Edge TTS): cues must be sub-100ms latency and must NEVER hit the network. Numpy oscillators (`indication_assets.py`) generate the waveforms at daemon startup; per-cue cost is a `bytes` reference + a queue put.


## 2. Public API

### 2.1 `Indication` facade — `src/indication.py`

```python
from enum import Enum
from typing import Protocol
from pipecat.frames.frames import SystemFrame   # IndicationCueFrame is a SystemFrame

class IndicationCueFrame(SystemFrame):
    """Marks the bracketed window during which a non-speech cue is playing.
    Upstream consumers (decider, generator, speaker_tagger) gate transcript
    processing while start=True is in flight.

    MUST inherit from `pipecat.frames.frames.SystemFrame` so pipecat
    broadcasts both upstream and downstream — required for upstream gating
    in decider/generator/speaker_tagger to fire correctly. Future
    maintainers: do not downgrade to a regular Frame."""
    start: bool

class IndicationKind(str, Enum):
    # Tier 1 — attention/error/input_waiting
    ACTION_FAILED          = "action_failed"
    ACTION_LONG_RUNNING    = "action_long_running"
    ACTION_REJECTED        = "action_rejected"
    AWAITING_CONFIRMATION  = "awaiting_confirmation"
    CONFIRMATION_DEADLINE  = "confirmation_deadline"
    REENROLL_RECORDING_START  = "reenroll_recording_start"
    REENROLL_RECORDING_END    = "reenroll_recording_end"
    OPENROUTER_TIMEOUT     = "openrouter_timeout"
    STT_ERROR              = "stt_error"
    TTS_FAILURE            = "tts_failure"
    AUDIO_DEVICE_LOST      = "audio_device_lost"
    MCP_AUTH_REQUIRED      = "mcp_auth_required"
    # Tier 2 — contextual
    INTENT_SUBMITTED       = "intent_submitted"
    INTENT_COMPLETED       = "intent_completed"
    INTENT_CANCELLED       = "intent_cancelled"
    OWNER_AUTO_ENROLLED    = "owner_auto_enrolled"
    GUEST_AUTO_ENROLLED    = "guest_auto_enrolled"
    GUEST_RENAMED          = "guest_renamed"
    WAKE_WORD_DETECTED     = "wake_word_detected"
    MODE_CHANGED           = "mode_changed"
    DAEMON_STARTED         = "daemon_started"
    DAEMON_SHUTDOWN        = "daemon_shutdown"
    CONFIRMATION_TIMED_OUT = "confirmation_timed_out"
    MCP_DISCONNECTED       = "mcp_disconnected"
    WORKFLOW_SAVED         = "workflow_saved"
    WORKFLOW_RUN_START     = "workflow_run_start"
    WORKFLOW_RUN_END       = "workflow_run_end"
    # Tier 3 — info
    SPEAKER_DRIFT          = "speaker_drift"
    MULTI_INTENT_START     = "multi_intent_start"
    MULTI_INTENT_END       = "multi_intent_end"
    NETWORK_UNREACHABLE    = "network_unreachable"
    HEARTBEAT_TICK         = "heartbeat_tick"
    NEW_CONVERSATION       = "new_conversation"

class IndicationLevel(str, Enum):
    ATTENTION    = "attention"
    ERROR        = "error"
    LONG_RUNNING = "long_running"
    SUCCESS      = "success"
    INFO         = "info"
    INPUT_WAITING = "input_waiting"

KIND_TO_LEVEL: dict[IndicationKind, IndicationLevel] = { ... }

class Backend(Protocol):
    name: str
    async def fire(self, kind: IndicationKind, level: IndicationLevel,
                   title: str, body: str, meta: dict) -> None: ...

class Indication:
    def __init__(self, settings: IndicationSettings, backends: list[Backend],
                 loop: asyncio.AbstractEventLoop | None = None) -> None:
        # Capture the main loop at construction time. MUST be called from the
        # main event loop. Producers may run in worker threads (sounddevice.rec
        # callback inside direct_tools._execute_re_enroll) and notify() must
        # work from any thread.
        self._loop = loop or asyncio.get_running_loop()
        ...

    def notify(self, kind: IndicationKind, *, title: str | None = None,
               body: str | None = None, meta: dict | None = None) -> None:
        """Fire-and-forget. Synchronous. Thread-safe. Never raises.

        Dispatch branches:
          - Called from self._loop's thread → loop.call_soon_threadsafe(
              loop.create_task, self._dispatch(...))
          - Called from any other thread → asyncio.run_coroutine_threadsafe(
              self._dispatch(...), self._loop)
          - self._loop is closed → log+drop.
        """

    async def aclose(self) -> None:
        # Idempotent. After aclose, notify() drops silently with a debug log.
        # Backends each get their own try/except aclose; one failure does not
        # block the next.
        ...

    async def reload(self, settings: IndicationSettings) -> None:
        # Runtime-enabled toggle support. Explicit ordering to avoid a
        # reload race where notify() arriving mid-drain enqueues into a
        # closing backend:
        #   1. Acquire lock; set self._enabled = False.
        #   2. Release lock.
        #   3. Drain pending dispatch tasks (await with timeout).
        #   4. Call aclose() on each backend.
        #   5. Re-read config; if still enabled, re-init backends and
        #      flip self._enabled = True.
        # Any notify() that arrives between steps 1 and 5 sees
        # _enabled=False under the lock and short-circuits — it never
        # enqueues into a closing backend.
        # No daemon restart required.
        ...
```

**Calling contract.**
- `notify()` is **sync** and **thread-safe**. Producers (sync paths inside `decider._safe_emit`, blocking `sounddevice` callbacks inside `direct_tools._execute_re_enroll`, ralph subtasks) can call without awaiting.
- Each backend `fire()` is wrapped with `try/except Exception` → `logger.warning`. One backend crashing never silences the others and never crashes the caller.

### 2.2 Default `title`/`body` table

Built into `indication.py:_DEFAULTS: dict[IndicationKind, tuple[str, str]]`. Producers can override via kwargs. Example entries:

| Kind | Default title | Default body |
|------|---------------|--------------|
| AWAITING_CONFIRMATION | "heare: confirm?" | "Say 'гава так' or 'гава ні' (30s)" |
| CONFIRMATION_DEADLINE | "heare: 5s left" | "Confirmation will expire" |
| REENROLL_RECORDING_START | "heare: speak now" | "Recording 15s for re-enrollment" |
| MCP_AUTH_REQUIRED | "heare: MCP auth needed" | "MCP server '{server}' needs authentication — check terminal" |
| ACTION_FAILED | "heare: action failed" | "{tool}: {error}" |
| ACTION_LONG_RUNNING | "heare: long action" | "{tool} still running ({elapsed}s)" |

Note on MCP_AUTH_REQUIRED body text: "check terminal" is generic on purpose. The `claude mcp authenticate <server>` command is NOT a verified, stable Claude CLI subcommand; suggesting it would mislead users. Body text directs them to the daemon log and terminal where the underlying SDK error and any auth URL are surfaced via existing logging.


## 3. Config schema

Add to `~/.heare/config.toml`:

```toml
[indication]
enabled = true                          # master switch (runtime-toggleable; see US-IND-A6)
sound_enabled = true
visual_enabled = true
notification_center_enabled = true
cooldown_seconds = 1.5
quiet_hours = ["22:00-07:00"]           # suppresses sound only

[indication.kinds.attention]
sound = true
visual = true
notification = true

[indication.kinds.error]
sound = true
visual = true
notification = true

[indication.kinds.long_running]
sound = true
visual = true
notification = false

[indication.kinds.success]
sound = false
visual = true
notification = false

[indication.kinds.info]
sound = false
visual = true
notification = false

[indication.kinds.input_waiting]
sound = true
visual = true
notification = true                     # ALWAYS — never quiet-houred per-kind
```

Loaded by extending `load_settings()` in `src/config.py` with a nested `IndicationSettings` dataclass; defaults match all values above so omitting the section is fully valid.


## 4. Backends

### 4.1 SoundBackend (`indication_backends/sound.py`)

**Asset generation (`indication_assets.py`)**
- Generated once at construction, cached as `dict[IndicationKind, bytes]`.
- Each cue is 200-500ms of int16 mono PCM at 24 kHz.
- Library: numpy only. Formula per cue:
  - `attention`: 880 Hz → 1318 Hz two-tone, 250ms, 8ms attack/release
  - `error`: 220 Hz → 165 Hz descending two-tone, 350ms, slight detune
  - `long_running`: 660 Hz single sine, 180ms, soft envelope
  - `success`: 523 Hz → 784 Hz major-third, 220ms
  - `info`: 1046 Hz click, 90ms, low amplitude
  - `input_waiting`: 880 Hz triple pip, 80ms × 3 with 60ms gaps
- Amplitude capped at 0.4 (peak); linear ADSR envelope to avoid clicks.
- Deterministic — same seed every run (so tests can hash).

**Injection**
- `SoundCueProcessor` lives in `indication.py` next to the facade and `IndicationCueFrame` (avoids cross-module circular imports with pipecat). Built in `pipeline.py`:
  ```python
  sound_cue_processor = build_sound_cue_processor(sample_rate=settings.tts_sample_rate)
  sound_backend = SoundBackend(processor=sound_cue_processor, assets=...)
  ```
- `SoundBackend.fire(...)` calls `processor.enqueue_cue(pcm)` (sync, non-blocking).
- Processor uses an `asyncio.Queue(maxsize=1)` and a long-lived drain task that, for each cue, pushes downstream. If a second cue is enqueued while the first is still pending, it is dropped and a `cues_dropped` counter is incremented (exposed in logs/visual).
  1. `IndicationCueFrame(start=True)`
  2. `OutputAudioRawFrame(audio=pcm, sample_rate=24000, num_channels=1)` — **subclass is critical**; `transport.output()` only routes `OutputAudioRawFrame`.
  3. After `len(pcm)/(sr*2) + 0.2s`, push `IndicationCueFrame(start=False)`.
- TTS-vs-cue race: track `_bot_speaking` via `BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame` in `process_frame` (same pattern as `decider.py:502-509`); defer cue emission until bot stops, drop after 2s of waiting (cue is no longer relevant).

**Why this works without confusing TTS or generating phantom transcripts**
- `OutputAudioRawFrame` is the only raw-audio frame `transport.output()` plays.
- `IndicationCueFrame` brackets the playback so upstream STT-consumers gate themselves, preventing speaker→mic→STT→phantom-transcript loop. The 200ms tail mirrors `bot_speaking_cooldown_seconds` semantics scoped to cue length.
- We sit AFTER `tts`, so we never block edge-tts MP3→PCM streaming.
- We never emit `BotStartedSpeakingFrame` for cues — it would muzzle the user for the full 2.5s `bot_speaking_cooldown_seconds`, which is wrong for a 250ms beep.

### 4.2 VisualBackend (`indication_backends/visual.py`)

- Append-only JSONL at `settings.log_dir / "indication.jsonl"`.
- One line per fire: `{"ts": float, "kind": str, "level": str, "title": str, "body": str}`.
- Rotation: keep last 200 lines on each `fire()` (cheap; <50KB).
- `watch.py` adds an `_indication_panel(settings)` function that tails the last 6 entries and renders a colored row per level, inserted into the existing layout between the `progress` panel and `log` panel (`watch.py:340-345`).

### 4.3 NotificationBackend (`indication_backends/notification.py`)

- macOS-only. On non-Darwin → backend disables itself at construction.
- **Implementation (argv-passed, NEVER interpolated):**
  ```python
  async def fire(...):
      # Body and title pass via subprocess argv. AppleScript reads them
      # via `item N of argv`. Backticks, $, \n, ", \\ become literal text.
      body = (body or "")[:240]            # length cap; osascript truncates anyway
      title = (title or "heare")[:80]
      sound_name = _level_to_sound(level)  # e.g. "Sosumi", "Glass", or ""

      script_lines = [
          "on run argv",
          ('display notification (item 1 of argv) '
           'with title (item 2 of argv)'
           + (' sound name (item 3 of argv)' if sound_name else '')),
          "end run",
      ]
      argv_extras = [body, title] + ([sound_name] if sound_name else [])
      cmd = ["osascript"]
      for line in script_lines:
          cmd += ["-e", line]
      cmd += ["--"] + argv_extras

      proc = await asyncio.create_subprocess_exec(
          *cmd,
          stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
      try:
          await asyncio.wait_for(proc.wait(), timeout=2.0)
      except asyncio.TimeoutError:
          proc.kill()
  ```
- **No string interpolation.** Body/title are never concatenated into the AppleScript source; `osascript` only sees fixed `-e` strings + argv values. A crafted body like `"; do shell script "rm -rf ~"; display notification "` is delivered as the literal banner text — no shell side-effects, no AppleScript parser surprises.
- `sound_name` derives from `level`: ATTENTION/ERROR → "Sosumi"; INPUT_WAITING → "Glass"; others → "" (silent banner; clause is omitted from the script when empty).
- Failure handling: any non-zero rc → `logger.warning`. Never raises.


## 5. Wiring map

(Architect-verified anchors retained; lifecycle anchors below clarified.)

| Tier | Producer file:line | Trigger | Kind |
|------|-------------------|---------|------|
| **T1** | `actions.py:111-117` | tool not allowed | `ACTION_REJECTED` |
| T1 | `actions.py:118-125` | args too long | `ACTION_REJECTED` |
| T1 | `actions.py:126-132` | queue full | `ACTION_REJECTED` |
| T1 | `actions.py:_process_one` (new wrap) | execution wall-clock > 5s | `ACTION_LONG_RUNNING` |
| T1 | `main.py:318-339` (`_on_action_error`) | any action error | `ACTION_FAILED` |
| T1 | `decider.py:736-748` | enter AWAITING_CONFIRMATION | `AWAITING_CONFIRMATION` |
| T1 | `decider.py:_timeout_watcher` (new sub-task) | 5s before deadline | `CONFIRMATION_DEADLINE` |
| T1 | `direct_tools.py:_execute_re_enroll` start | recording begins (off-loop thread; uses run_coroutine_threadsafe branch) | `REENROLL_RECORDING_START` |
| T1 | `direct_tools.py:_execute_re_enroll` end | recording done (off-loop thread) | `REENROLL_RECORDING_END` |
| T1 | `generator.py:417-419` | OpenRouterError caught | `OPENROUTER_TIMEOUT` |
| T1 | `pipeline.py` STT error wrapper (new) | pipecat `ErrorFrame` from GroqSTTService observed in a one-line wrapper processor placed immediately downstream of `stt` (pipecat surfaces STT errors via `ErrorFrame`; see pipecat docs for `ErrorFrame`/`FatalErrorFrame` propagation) | `STT_ERROR` |
| T1 | `tts_edge.py:170-202` | edge-tts/ffmpeg crash | `TTS_FAILURE` |
| T1 | `pipeline.py` transport hook (new) | `LocalAudioTransport` reports input lost | `AUDIO_DEVICE_LOST` |
| T1 | `mcp_utils.py` (new auth detector) | MCP server returns 401 / auth needed | `MCP_AUTH_REQUIRED` |
| **T2** | `generator.py:269-282` | `_submit_intent` returns id | `INTENT_SUBMITTED` |
| T2 | `main.py:304-316` | `_on_action_result` | `INTENT_COMPLETED` |
| T2 | `generator.py:349-353` | `cancel_latest()` returned non-None | `INTENT_CANCELLED` |
| T2 | `speaker_processor.py` owner auto-enroll | `auto_enroll_owner_after` reached | `OWNER_AUTO_ENROLLED` |
| T2 | `speaker_processor.py` guest auto-enroll | `auto_enroll_after` reached | `GUEST_AUTO_ENROLLED` |
| T2 | `speaker_namer.py:138-150` | gallery rename succeeded | `GUEST_RENAMED` |
| T2 | `decider.py:136` (RULE 0 wake) | wake-word matched in FOCUS mode | `WAKE_WORD_DETECTED` |
| T2 | `decider.py:_reload_mode` | mode changed since last tick | `MODE_CHANGED` |
| T2 | `main.py:_cmd_start` end of init | **AFTER pipecat StartFrame is dispatched and audio init succeeds** (hook the existing greeting-scheduled path; do NOT fire pre-init) | `DAEMON_STARTED` |
| T2 | `main.py:run_until_stopped` finally | shutdown | `DAEMON_SHUTDOWN` |
| T2 | `decider.py:885-896` | timeout watcher fired | `CONFIRMATION_TIMED_OUT` |
| T2 | `mcp_utils.py` (disconnect handler) | MCP server vanished mid-session | `MCP_DISCONNECTED` |
| T2 | `workflow.py` save | new workflow stored | `WORKFLOW_SAVED` |
| T2 | `actions.py:296-310` | workflow run begins | `WORKFLOW_RUN_START` |
| T2 | `actions.py:338-346` | workflow run done | `WORKFLOW_RUN_END` |
| **T3** | `main.py:_cmd_speakers_audit` (also surfaced from runtime audit) | DRIFT row | `SPEAKER_DRIFT` |
| T3 | `intent_parser.py` (new counter) | n>1 intents per response start/end | `MULTI_INTENT_START`/`_END` |
| T3 | `openrouter_cli.py:86` | `OpenRouterError("transport error")` w/ DNS-like cause | `NETWORK_UNREACHABLE` |
| T3 | `heartbeat.py` tick | proactive check-in (subject to global cooldown like everything else; see §5.1) | `HEARTBEAT_TICK` |
| T3 | `conversation.py` `get_or_create_active` returns NEW row | new conversation | `NEW_CONVERSATION` |

### 5.1 Cooldown policy clarifications
- `HEARTBEAT_TICK` **is** subject to the global `cooldown_seconds` like every other kind. Heartbeat ticks fire every `heartbeat_interval_minutes` (typically 15-30 min), well above the 1.5s cooldown — so this is a no-op in practice but documented for predictability.
- `MCP_AUTH_REQUIRED` debounce is **session-scoped, per-server**. Cleared on daemon restart so a re-launched daemon will re-notify if auth is still missing.
- `MCP_DISCONNECTED` debounce is 30s per server (existing T2 spec).
- `SPEAKER_DRIFT` debounce is 24h per affected speaker.
- All debounce state lives in-process (no disk persistence) and resets on daemon restart by design.

### 5.2 Lifecycle ordering
- `DAEMON_STARTED` **must fire after** `pipecat StartFrame` is dispatched AND audio init succeeds. Specifically: hook the existing greeting-schedule path in `_cmd_start`, which runs after `pipeline.queue_frames([StartFrame()])` resolves and `LocalAudioTransport` confirms input/output device acquisition. Firing earlier risks the indication backends attempting to push frames into a half-built pipeline.
- `DAEMON_SHUTDOWN` fires from `run_until_stopped` finally clause, before `Indication.aclose()` is awaited (so the shutdown notification has a chance to flush).


## 6. User stories (for ralph execution)

**17 stories** total (was 15; US-IND-A4 split into A4a/A4b/A4c; US-IND-A6 added for runtime disable). Each one-iteration sized.

### Phase A — Foundation (7 stories)

- **US-IND-A1: Asset generator + tests** — Add `src/indication_assets.py` with numpy-only PCM cue functions; add `tests/test_indication_assets.py` asserting deterministic length and amplitude bounds for all 6 levels.
  AC: `uv run pytest tests/test_indication_assets.py` green; no new deps.

- **US-IND-A2: Config schema** — Extend `src/config.py` with `IndicationSettings` dataclass; load nested `[indication]` table in `load_settings()`; add tests in `tests/test_config.py` for default, partial-override, and invalid quiet_hours strings.
  AC: missing section ⇒ all defaults; invalid time string logs warning and is dropped; existing config tests still pass.

- **US-IND-A3: Indication facade + Backend protocol + thread-safe notify** — Add `src/indication.py` with `IndicationCueFrame`, `IndicationKind`, `IndicationLevel`, `KIND_TO_LEVEL`, `_DEFAULTS`, `Backend` Protocol, `Indication` class with thread-safe `notify()` (captures `_loop` at construction; uses `loop.call_soon_threadsafe` on-loop and `asyncio.run_coroutine_threadsafe` off-loop), gate logic (master enabled, per-kind toggles, mode gating, quiet hours, cooldown). Add `tests/test_indication.py` with a `RecordingBackend`.
  AC: cooldown coalesces back-to-back same-kind cues; quiet hours block sound only; INPUT_WAITING bypasses quiet hours for notification.
  **AC (thread-safe):** "Spawn a worker thread, call `Indication.notify(KIND)` from it, await up to 1s, assert RecordingBackend captured the event. Repeat from main loop. Both must succeed."

- **US-IND-A4a: SoundBackend + SoundCueProcessor + IndicationCueFrame** — Implement `indication_backends/sound.py` and the `SoundCueProcessor` in `indication.py`. The processor emits `IndicationCueFrame(start=True) → OutputAudioRawFrame → IndicationCueFrame(start=False)` per cue. Add `tests/test_indication_sound.py` using a `FakeProcessor` that captures pushed frames.
  AC: pushed frame sequence is exactly `[IndicationCueFrame(start=True), OutputAudioRawFrame(...), IndicationCueFrame(start=False)]`; bare `AudioRawFrame` is NOT used (assert subclass).
  **AC (queue overflow):** fire 3 ATTENTION cues back-to-back; assert exactly 1 plays, 2 are dropped, and `cues_dropped` counter == 2.
  **AC (tail timing):** the trailing `IndicationCueFrame(start=False)` is scheduled at `len(pcm)/(sample_rate*2) + 0.2s` after the start frame (computed from actual PCM length, never hardcoded). Unit test asserts the gap matches the formula for two distinct PCM lengths.
  **AC (echo-back integration):** `tests/integration/test_indication_no_phantom.py` — boot a minimal pipeline with stub STT in loopback mode, fire one ATTENTION cue while STT is live, assert no phantom transcript appears in the transcripts table within 3s. Verifies the IndicationCueFrame upstream-gating contract end-to-end.

- **US-IND-A4b: VisualBackend** — Implement `indication_backends/visual.py` with JSONL writer + 200-line trim. Tests using tmp_path.
  AC: each `fire()` writes one valid JSON line; rotation trims to 200; failure in another backend doesn't block this one (covered by A3 isolation contract).

- **US-IND-A4c: NotificationBackend (argv-passed; injection-safe)** — Implement `indication_backends/notification.py`. Body/title pass via subprocess argv (NEVER interpolated into AppleScript source). Darwin guard at construction.
  AC (general): correct argv on Darwin; no-op on non-Darwin.
  **AC (injection regression):** "Pass body containing `\"; do shell script \"echo PWNED\"; display notification \"`. Assert: (1) `osascript` returns 0; (2) the literal string is delivered as banner text (verified via mock subprocess capturing argv); (3) NO `echo PWNED` side-effect (no `PWNED` in stdout/stderr/files); (4) no exception."
  AC: 2s timeout kills hung subprocess; level→sound_name mapping covered.
  **AC (argv arity):**
  - When `level→sound_name` is empty (info/success/long_running), the AppleScript omits the `sound name` clause and argv has length 2 (body, title).
  - When `level→sound_name` is non-empty (attention/error/input_waiting), AppleScript includes the `sound name` clause and argv has length 3 (body, title, sound_name).
  - Unit test parametrizes both arities and asserts the rendered script lines and argv length per case.

- **US-IND-A5: Pipeline integration + lifecycle + upstream IndicationCueFrame consumers** — Wire `Indication` and `SoundCueProcessor` into `src/pipeline.py` and `src/main.py`. Construct backends from settings. Pass facade to processors that need it (decider, generator, action worker callbacks). Capture main-loop ref and pass to `Indication.__init__`. Add one-line `_indication_speaking` flag + `IndicationCueFrame` handler to `decider.py` (next to `_bot_speaking` at :502-509), `generator.py` (:179), `speaker_processor.py` SpeakerTagger ingestion path. Call `aclose()` in shutdown path.
  AC: `uv run heare start` boots cleanly with `[indication]` section absent; `daemon.log` shows "indication: 3 backends ready" (or "0 if disabled"); `uv run pytest -q` green; injecting a synthetic `IndicationCueFrame(start=True)` followed by a transcript fragment in unit tests demonstrates decider/generator/tagger drop the fragment until `start=False`.

- **US-IND-A6: Runtime disable / re-enable** — Implement `Indication.reload(settings)` and a one-line file-watcher hook (or signal handler) so flipping `[indication].enabled = false` mid-session drains and closes backends without restarting the daemon. Re-enabling re-instantiates backends from current settings.
  AC: `tests/test_indication_runtime_disable.py` — start with enabled=true, fire a cue (recorded), call `reload(settings_disabled)`, fire another cue (NOT recorded), call `reload(settings_enabled)`, fire a cue (recorded). Backends close cleanly between transitions; no daemon restart.

### Phase B — Tier 1 wiring (4 stories)

- **US-IND-B1: Confirmation + re-enroll input-waiting (off-loop notify branch)** — Wire `AWAITING_CONFIRMATION` (decider.py), `CONFIRMATION_DEADLINE` (new sub-task inside `_timeout_watcher`), `REENROLL_RECORDING_START`/`_END` (`direct_tools.py`).
  Note: `direct_tools._execute_re_enroll` runs `sounddevice.rec()` in a worker thread; `notify()` calls from there exercise the off-loop `run_coroutine_threadsafe` branch added in A3.
  AC: notification center fires for all three even when sound is off (because INPUT_WAITING bypass); deadline warning fires exactly once. `tests/test_indication_wiring.py` covers the off-loop call path for re-enroll.

- **US-IND-B2: Action lifecycle errors + rejection + long-running** — Wire `ACTION_REJECTED` (3 paths in actions.py:111-132), `ACTION_FAILED` (main.py `_on_action_error`), `ACTION_LONG_RUNNING` (timer in `ActionWorker._process_one` that fires after 5s and cancels itself if action completes earlier).
  AC: queue-full, args-too-long, and tool-not-allowed each emit exactly one notification; long-running fires for actions that exceed 5s but never fires for sub-5s actions.

- **US-IND-B3: External service errors (STT_ERROR via pipecat ErrorFrame)** — Wire `OPENROUTER_TIMEOUT` (generator.py exception handler), `STT_ERROR`, `TTS_FAILURE` (tts_edge.py exception path), `AUDIO_DEVICE_LOST` (transport input observer).
  Anchor for STT_ERROR: pipecat surfaces STT errors via `ErrorFrame` (not by raising). The wiring is a one-line wrapper `FrameProcessor` placed immediately downstream of `stt` in `pipeline.py` (~`pipeline.py:128-141` insertion). Its `process_frame` checks `isinstance(frame, ErrorFrame)` and calls `indication.notify(STT_ERROR, body=str(frame.error))`. Reference: pipecat's `ErrorFrame`/`FatalErrorFrame` propagation conventions in `pipecat.frames.frames`.
  AC: each error type fires its kind exactly once per occurrence and never recurses (don't TTS the failure of TTS); STT_ERROR firing path is unit-tested by injecting a synthetic `ErrorFrame` into the wrapper and asserting `notify()` is called.

- **US-IND-B4: MCP auth required (verified body text, debounced)** — Add MCP error inspection in `mcp_utils.py` to detect 401 / `auth_required` responses; route to `MCP_AUTH_REQUIRED`. Body text: "MCP server '<name>' needs authentication — check terminal" (the `claude mcp authenticate` subcommand was NOT verified to exist on the user's CLI; using a generic "check terminal" pointer is honest and forward-compatible). Notification only — no sound (avoid noise spam if user has multiple unauthenticated servers at boot). **Per-server debounce: session-scoped (cleared on daemon restart).**
  AC: simulated 401 response from a fake MCP server emits exactly one notification per server per session; restarting the daemon re-emits if still unauthenticated.

### Phase C — Tier 2 wiring (4 stories)

- **US-IND-C1: Intent lifecycle + cancellation** — Wire `INTENT_SUBMITTED` (generator.py:_submit_intent), `INTENT_COMPLETED` (main.py:_on_action_result), `INTENT_CANCELLED` (generator.py cancel branch).
  AC: each user utterance that produces an intent yields submit→complete pair; "скасуй" yields exactly one cancellation notification.

- **US-IND-C2: Speaker events** — Wire `OWNER_AUTO_ENROLLED`, `GUEST_AUTO_ENROLLED` (speaker_processor.py), `GUEST_RENAMED` (speaker_namer.py:138-150). Owner enrollment retains its existing TTS greeting AND adds a notification.
  AC: enrolling a new guest (test fixture) fires exactly one of each kind per acoustic identity; no duplicate after rename.

- **US-IND-C3: Mode + lifecycle + workflow** — Wire `MODE_CHANGED` (decider.py:_reload_mode), `WAKE_WORD_DETECTED` (decider.py wake-word branch in FOCUS only), `DAEMON_STARTED` (post-StartFrame, post-audio-init), `DAEMON_SHUTDOWN`, `CONFIRMATION_TIMED_OUT`, `WORKFLOW_SAVED`, `WORKFLOW_RUN_START`/`_END`.
  AC: starting and stopping daemon emits exactly one of each kind; mode hot-reload (writing `~/.heare/mode`) fires once; DAEMON_STARTED only fires after the StartFrame propagation completes.

- **US-IND-C4: MCP disconnect** — Wire `MCP_DISCONNECTED` in `mcp_utils.py` connection-monitor path. Debounced 30s per server.
  AC: simulated disconnect emits one notification; reconnect emits none (different kind, unscoped here).

### Phase D — Tier 3 wiring (2 stories)

- **US-IND-D1: Info events** — Wire `MULTI_INTENT_START`/`_END` (intent_parser.py counter), `NETWORK_UNREACHABLE` (openrouter_cli.py classify of OpenRouterError), `HEARTBEAT_TICK` (heartbeat.py), `NEW_CONVERSATION` (conversation.py new-row branch).
  AC: heartbeat per `heartbeat_interval_minutes`; multi-intent only when n>1; network unreachable distinct from generic openrouter timeout.

- **US-IND-D2: Speaker drift surface** — Add a periodic audit task in `main.py` (every 6h) that runs the existing `gallery.audit()` for each enrolled speaker; on DRIFT result fire `SPEAKER_DRIFT`. Visual + notif only (no sound — drift is informational).
  AC: artificial DRIFT result triggers one notification per affected speaker per audit cycle (debounced 24h).


## 7. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Cue echoes through speaker → mic → STT → phantom transcripts | High (without mitigation) | Bogus user utterances | `IndicationCueFrame(start=True/False)` brackets cue playback; decider/generator/speaker_tagger drop transcripts during the bracketed window; integration test asserts no phantom transcript within 3s of an ATTENTION cue |
| Bare `AudioRawFrame` would not play (only `OutputAudioRawFrame` is routed by `transport.output()`) | Was Plan-v1 bug | Silent cues | Use `OutputAudioRawFrame` explicitly; sound test asserts pushed-frame subclass |
| `notify()` from worker thread raises `RuntimeError` and gets silently dropped | Was Plan-v1 bug | "User always knows" violated | Capture `_loop` at construction; on-loop branch uses `loop.call_soon_threadsafe`; off-loop branch uses `asyncio.run_coroutine_threadsafe`; thread-safety unit test required in A3 |
| `osascript` injection from action error text | Was Plan-v1 bug | Arbitrary shell exec | Body/title passed via subprocess argv only (`-- "$body" "$title"`); AppleScript reads via `item N of argv`; unit test fires a malicious payload and asserts no side-effect |
| Sound cue collides with TTS audio (overlap, clipping) | Medium | Audible glitch | `SoundCueProcessor` defers emission while `_bot_speaking`; queue size 2; 2s drop horizon |
| Notification spam on boot (many MCP servers unauth) | High | User annoyance | Per-server session-scoped debounce; `cooldown_seconds` global gate |
| `osascript` subprocess hang | Low | Worker leak | 2s `wait_for` timeout; `proc.kill()` on timeout |
| Backend exceptions during shutdown | Low | Crash on exit | `aclose()` swallows per-backend errors |
| `OutputAudioRawFrame` shape changes across pipecat versions | Medium | Build break | Pin pipecat range in pyproject; keep one shim function `_make_output_audio_frame(pcm, sr)` so future bumps are one-line |
| `indication.jsonl` grows unbounded | Low | Disk waste | Rotate at 200 lines per write |
| Quiet hours misconfigured ("25:00") | Low | Silent failure | Validation in config loader logs warning and skips entry |
| Confirmation deadline timer drift | Low | Late warning | Schedule deadline subtask as `asyncio.sleep(timeout - 5)` from arm time, not as a recurring poll |
| `DAEMON_STARTED` fires before audio init completes | Medium | Backends try to push to half-built pipeline | Hook the post-StartFrame greeting-schedule path; documented in §5.2 |


## 8. Testing strategy

| Backend / piece | Approach |
|-----------------|----------|
| `Indication` facade | `RecordingBackend` captures `(kind, level, title, body)` tuples. Tests for: master disable, per-kind toggle, mode gating, quiet hours boundaries (22:00/07:00 inclusive), cooldown coalescing, INPUT_WAITING bypass, **thread-safe notify (worker-thread + main-loop branches)**, runtime reload (US-IND-A6) |
| `indication_assets.py` | Determinism (same bytes across runs), length matches expected ms, peak amplitude ≤ 0.4 |
| `SoundBackend` + `SoundCueProcessor` | Inject `FakeSoundCueProcessor` exposing `pushed_frames: list[Frame]`. Assert sequence per cue is exactly `[IndicationCueFrame(start=True), OutputAudioRawFrame, IndicationCueFrame(start=False)]`; assert max queue depth honored; mock `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` deferral; 2s drop horizon test with monkey-patched clock |
| Echo-back integration | `tests/integration/test_indication_no_phantom.py` — minimal pipeline + stub STT in loopback; trigger ATTENTION cue; assert no phantom transcript in transcripts table within 3s |
| `VisualBackend` | tmp_path; assert valid JSONL line per fire; assert rotation trims to 200 |
| `NotificationBackend` | Patch `asyncio.create_subprocess_exec`; assert correct argv, Darwin-skip on `sys.platform != 'darwin'`, timeout behavior; **injection regression test** with `; do shell script` payload — assert literal delivery, no shell side-effects |
| Wiring tests (`test_indication_wiring.py`) | Inject `Indication(backends=[RecordingBackend()])` into `DeciderProcessor`, `GeneratorProcessor`, `ActionWorker`. Drive them through fixtures; assert expected `notify()` calls with kinds + ordering. Includes off-loop branch coverage from `direct_tools._execute_re_enroll`. |
| Runtime disable | `tests/test_indication_runtime_disable.py` — covers US-IND-A6 transitions |
| STT_ERROR | Inject synthetic `ErrorFrame` into the new STT wrapper processor; assert `notify(STT_ERROR, ...)` called |
| Integration smoke | `tests/integration/test_indication_smoke.py` — boots a minimal pipeline with stub STT/TTS, drives one act-confirm-execute cycle, asserts the JSONL file contains expected kinds in order |
| Lint | `uv run ruff check src/ tests/` clean before each story merges |


## 9. Open questions

(Tracked in `.omc/plans/open-questions.md` under the "indication" heading.)

1. Should `MODE_CHANGED` fire when the daemon boots into a non-default mode (i.e. on first read of `~/.heare/mode`)? Proposed: NO — only fires on actual transitions during a single daemon lifetime.
2. macOS notification grouping — `osascript`'s `display notification` does not support thread/group ids. Acceptable, or require `terminal-notifier` as optional dep for grouping? Proposed: accept text-only for v1.
3. Should the visual panel in `watch.py` auto-clear stale rows or always show last N? Proposed: always last 6, no time-based fade.
4. `WAKE_WORD_DETECTED` in FOCUS only, or also in AMBIENT? Proposed: FOCUS only.
5. Should `INTENT_SUBMITTED` fire for direct/simple tools? Proposed: yes, but `info` defaults to sound=false / notification=false.

(Note: the v1 question about "how to call notify() outside an async loop" is now resolved by the thread-safe dispatch contract in §2.1.)


## 10. Quality gates (per story and final)

- `uv run pytest -q` green (all existing + new tests, including thread-safety, injection-regression, echo-back integration)
- `uv run ruff check src/ tests/` clean
- Manual smoke: `uv run heare start` boots; trigger one act-confirm flow and observe one cue + one notification + one visual row per stage; toggle `[indication].enabled=false` at runtime and confirm clean drain (US-IND-A6 path).


---

# RALPLAN-DR Summary

## Principles
1. **Never block the audio path.** Indication is fire-and-forget; producers call a sync, thread-safe method and walk away.
2. **Reuse existing audio infrastructure.** Cues go through the same `transport.output()` as TTS via `OutputAudioRawFrame` — no second device, no PortAudio handles.
3. **Backends are isolated** — with one principled exception: **SoundBackend integrates with the audio pipeline by necessity**; its failure modes are bounded by the `IndicationCueFrame` protocol — failure renders cues silent but never blocks the audio path or affects upstream gating.
4. **Mode-aware by default.** Silent never beeps; focus only beeps for attention/error/input_waiting; ambient fires everything.
5. **No new binary assets.** Sounds are synthesized at startup with numpy.

## Decision Drivers (top 3)
1. Voice-pipeline safety — must not introduce audio glitches, **phantom transcripts** (echo-back), or pipecat frame-graph bugs (right frame subclass; right ordering).
2. Cross-backend resilience — partial failure is the norm (osascript missing/injection-attempted, audio device gone, file system full); single-backend failures must never cascade or expose security holes.
3. Right-sized stories — 17 stories of one-iteration size each (was 15; A4 split + A6 added) so ralph can drive Phase A→D without manual intervention between phases.

## Viable Options (≥2)

### Option 1 (CHOSEN): Single facade + 3 backends + injected SoundCueProcessor with `IndicationCueFrame` brackets
- Pros:
  - Clean separation: `Indication.notify()` is the only producer-facing API.
  - Sound cues reuse the existing audio out (`OutputAudioRawFrame`) — zero new device handling.
  - `IndicationCueFrame` protocol cleanly decouples "cue is playing" from `BotStartedSpeakingFrame`, so STT echo-suppression mirrors `bot_speaking_cooldown_seconds` semantics scoped to cue length (no 2.5s muzzle for a 250ms beep).
  - Backends are independently testable and swappable.
  - Pipeline change is one new processor at a single insertion point.
- Cons:
  - One extra processor in the pipecat graph (negligible CPU).
  - Sound assets generated at startup (~50ms one-time cost; trivial).
  - Adds a new public frame type (`IndicationCueFrame`) — 3 upstream consumers grow by 5 lines each.

### Option 2: Per-backend producer wiring (no central facade)
- Pros:
  - Slightly less indirection — call site directly invokes per-backend functions.
- Cons:
  - Producers must know about every backend → harder to add a 4th.
  - Per-kind/mode/quiet-hours/cooldown gating duplicated at every call site.
  - Cooldown coalescing impossible without central state.
  - **Invalidated**: violates the "backends isolated, producers agnostic" principle.

### Option 3: TTS-channel reuse via TTSCache pre-rendered phrases
- Pros:
  - Reuses cache+playback path 1:1; zero new audio code.
- Cons:
  - Edge TTS speaks words, not non-speech cues — defeats the requirement.
  - Pre-render still has to happen at startup.
  - **Invalidated**: requirement explicitly calls for non-speech cues.

### Option 4: `sounddevice` direct-play SoundBackend (bypass pipecat audio graph)
- Approach: `SoundBackend` opens its own `sounddevice.OutputStream` at construction and writes PCM directly to the system output device, bypassing the pipecat pipeline entirely.
- Pros:
  - **Zero pipecat-graph risk** — no new processor, no frame-subclass concerns, no chance of the cue path interfering with TTS frame routing.
  - Implementation is a few dozen lines; backend is genuinely independent.
- Cons:
  - **Second device handle** — opens a separate `sounddevice` stream while pipecat owns the same output device through `LocalAudioTransport`. On macOS Core Audio this typically works but can cause device-busy errors on some hardware combos and is undefined behavior on other backends (ALSA, WASAPI exclusive mode).
  - **Contention with TTS** — no graph-level coordination; cue can play simultaneously with a TTS phrase, producing audible mixing through the OS mixer rather than the existing `_bot_speaking` deferral.
  - **No echo-back gating leverage** — without `IndicationCueFrame` propagating upstream through the graph, decider/generator can't gate transcripts; we'd need a separate side-channel signal that crosses the pipecat boundary, reintroducing the very coupling Option 1 cleanly handles.
  - **Violates Principle P2** ("reuse existing audio infrastructure") and the spirit of P3 (this isn't a principled exception; it's an end-run around the pipeline architecture).
- **Rejected** explicitly: the second-device-handle pitfall is real on heterogeneous hardware and a regression risk we can't smoke-test from a single dev box; the lack of upstream gating leverage means we'd reinvent half of `IndicationCueFrame` anyway. Option 1's single-processor footprint is the smaller, safer surface.

Final choice: **Option 1.** Justification: the only option that satisfies all 5 principles (with the explicit P3 exception documented), scales to a 4th backend later, and centralizes the gating/cooldown/quiet-hours logic the spec requires.

---

**Plan saved to:** `/Users/lenyk/myprojects/heare/.omc/plans/indication.md`

---

# Revision History

## v2 — 2026-04-24 (response to Architect + Critic ITERATE)

**BLOCKER fixes**

- **Frame-type / echo-back (B1).** `SoundCueProcessor` now emits `OutputAudioRawFrame` (the only `transport.output()`-routable raw audio subclass; bare `AudioRawFrame` does NOT play) and brackets each cue with `IndicationCueFrame(start=True/False)`. Decider, generator, and SpeakerTagger gain a one-line `_indication_speaking` flag so transcripts produced during cue playback are dropped — preventing the speaker → mic → STT → phantom-transcript loop. We deliberately do NOT reuse `BotStartedSpeakingFrame` (would muzzle the user for the full 2.5s cooldown per beep). New AC on US-IND-A4a: "Trigger one ATTENTION cue while STT is live; assert no phantom transcript within 3s." Pipeline §1.3 and SoundBackend §4.1 rewritten; risk table §7 updated.
- **`notify()` thread-safety (B2).** `Indication.__init__` captures `self._loop` at construction (must be called from the main loop). On-loop calls use `loop.call_soon_threadsafe(loop.create_task, coro)`; off-loop calls (e.g. `direct_tools._execute_re_enroll`'s `sounddevice.rec()` worker thread) use `asyncio.run_coroutine_threadsafe(coro, self._loop)`. Both branches specified in §2.1. New AC on US-IND-A3: "Spawn a thread, call `notify()` from it, assert RecordingBackend captured the event."
- **`osascript` injection (B3).** §4.3 rewritten: AppleScript no longer interpolates body/title; instead an `on run argv` script reads `item 1 of argv` / `item 2 of argv` and `subprocess` passes body/title/sound_name as argv items after `--`. Backticks, `$`, `\n`, `"`, `\\` become literal banner text. New AC on US-IND-A4c: "Pass body containing `; do shell script ...` payload; assert literal delivery, zero side-effects, no exception."

**Story sizing**

- **US-IND-A4 split** into A4a (sound), A4b (visual), A4c (notification). A4a additionally owns the new `IndicationCueFrame` and the echo-back integration test. **Total stories: 15 → 17** (also added A6 below).

**Critic-raised additions**

- **US-IND-A6 added** — runtime disable/re-enable path. `Indication.reload(settings)` drains and re-instantiates backends without daemon restart. Test `test_indication_runtime_disable.py` covers the transition matrix.
- **US-IND-B3 STT_ERROR anchor** — pipecat surfaces STT errors via `ErrorFrame`; the wiring is a one-line wrapper `FrameProcessor` placed immediately downstream of `stt` in `pipeline.py`.
- **US-IND-B4 MCP_AUTH_REQUIRED body text** — `claude mcp authenticate <server>` is unverified. Body text changed to generic "MCP server '<name>' needs authentication — check terminal" to avoid misleading the user. Per-server debounce explicitly **session-scoped** (cleared on daemon restart).
- **HEARTBEAT_TICK cooldown policy** — explicitly subject to global `cooldown_seconds` like every other kind (§5.1).
- **DAEMON_STARTED ordering** — must fire AFTER pipecat `StartFrame` is dispatched and audio init succeeds; documented in §5.2 and AC for US-IND-C3.

**Alternatives**

- **Option 4 added** ("sounddevice direct play"): honestly enumerated — pros (zero pipecat-graph risk, implementation simplicity) — cons (second device handle, OS-backend contention, no upstream gating leverage, P2/P3 violation). Rejected with rationale.

**Principles**

- **P3 refined** to acknowledge SoundBackend as the principled exception: "Sound backend integrates with the audio pipeline by necessity; its failure modes are bounded by the `IndicationCueFrame` protocol — failure renders cues silent but never blocks the audio path or affects upstream gating."

**Untouched (preserved)**
- Phase A/B/C/D structure, all wiring-map file:line anchors Architect verified accurate, all existing story content beyond the explicit additions, `open-questions.md` entries (the v1 "no async loop" question is now subsumed by the thread-safe dispatch contract; left in §9 as a note).

## v3 — Precision pass (fold-in of reviewer nits)
- §2.1 reload(): explicit lock-and-flag-flip-before-drain ordering documented
- §1.3 + §4.1: queue maxsize reconciled to 1, cues_dropped counter added
- US-IND-A4a: AC for queue overflow drop count
- US-IND-A4a: AC for IndicationCueFrame(start=False) tail timing formula
- US-IND-A4c: AC for argv arity per sound clause presence
- §1.3/§4.1: SystemFrame inheritance comment for IndicationCueFrame
