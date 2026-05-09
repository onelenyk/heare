# YAMNet Audio Event Detection — Work Plan

**Date:** 2026-05-08
**Status:** REVISION 2 — awaiting Architect/Critic re-review
**Complexity:** MEDIUM (5 new files, 4 edited files, 1 optional-dep group)

---

## RALPLAN-DR Summary

### Principles

1. **Zero-crash tolerance.** A missing model file or failed import must never take down the daemon. Every new runtime path degrades gracefully to a no-op.
2. **Audio-loop sovereignty.** The 16 kHz PCM pipeline must never block. All inference runs off-loop via `asyncio.to_thread`; frames are dropped, never queued, when inference is busy.
3. **Backward-compatible contracts.** Audio events go to a separate `audio_event.json` file. The dashboard reader renders correctly whether the file is present, corrupt, or absent.
4. **Opt-in by default.** The feature is gated behind `audio_event_detection_enabled = false` in `Settings`. Users who never touch config see zero behavioral change, zero new imports, zero new I/O.
5. **Minimal footprint.** No auto-download, no network calls, no new background threads beyond the `to_thread` inference. The ONNX model is a user-supplied file.

### Decision Drivers

1. **Latency impact on STT path.** The observer must add < 1 ms of synchronous overhead to `process_frame` (the `to_thread` call itself is fire-and-forget with a guard flag).
2. **False-positive rate.** The 2-consecutive-window confirmation rule is the primary lever; threshold (0.4) is secondary. Both are tunable via `Settings` without code changes.
3. **Model acquisition cost.** No canonical YAMNet ONNX exists. The plan provides conversion docs using `tf2onnx` rather than depending on an unverifiable URL.

### Viable Options

| # | Option | Pros | Cons | Verdict |
|---|--------|------|------|---------|
| 1 | **YAMNet via ONNX Runtime** (chosen) | ~3 MB model, 521 classes, well-documented AudioSet labels, ONNX RT is CPU-only and lightweight, no torch dependency | Requires user to convert or obtain `.onnx` file; 0.96 s window is fixed by model architecture | **Chosen** |
| 2 | PANNs (CNN14) via ONNX | Higher mAP on AudioSet; same class set | Model is ~300 MB (100x YAMNet); ONNX export is unofficial and fragile; inference is 5-10x slower on CPU | **Invalidated** — footprint violates Principle 5; latency risks Principle 2 on low-end hardware |
| 3 | AST (Audio Spectrogram Transformer) | State-of-the-art accuracy | Requires torch or custom ONNX export (~90 MB); no official ONNX; inference ~20x slower than YAMNet on CPU | **Invalidated** — same reasons as PANNs, worse on both axes |
| 4 | Rule-based (energy + ZCR heuristics) | Zero external deps; tiny | Cannot distinguish laughter from speech from cough — only detects "loud event vs silence," which STT/VAD already does | **Invalidated** — does not meet the classification requirement |
| 5 | **Whisper non-speech tokens** (`[LAUGHTER]`, `[MUSIC]`, `[COUGH]`) | Zero additional model; ~20 LOC filter on `TranscriptionFrame.text` | Covers only ~5-8 of 18 target labels (no pets, no sneeze, no purr); depends on Whisper verbose output format; not all providers emit these tokens | **Complementary** — deferred to a follow-up Phase 0 plan. Does not replace YAMNet for the full label set but could ship independently as a lightweight observer that filters transcription text. Rationale for deferral: different input source (text vs audio), different observer class, and limited label coverage make it a separate, additive feature. |

---

## Context

The heare voice assistant pipeline processes live 16 kHz mono int16 PCM audio. We want to detect non-speech vocal and environmental audio events (laughter, cough, pet sounds, etc.) on the mic stream and surface them on the watch dashboard. The detection uses a pretrained YAMNet CNN running locally via ONNX Runtime, with a curated allowlist of ~20 AudioSet classes.

## Work Objectives

1. Add a YAMNet classifier module that loads the ONNX model and runs inference on 0.96 s audio windows.
2. Add a Pipecat `FrameProcessor` observer that accumulates `InputAudioRawFrame` data, runs inference off the audio loop, applies the 2-window confirmation rule, and writes detected events to `audio_event.json`.
3. Wire the observer into the pipeline immediately after `input_mute_gate`.
4. Extend the watch dashboard to display detected events in the existing `VoiceStateBar`.
5. Add `onnxruntime` as an optional dependency, settings flags, and model conversion docs.
6. Write targeted unit tests that run without the ONNX model file.

## Guardrails

**Must Have:**
- Feature gated by `audio_event_detection_enabled` (default `false`)
- Graceful degradation: missing model file or missing `onnxruntime` logs warning, skips observer creation
- 2-consecutive-window confirmation before emitting event
- Inference via `asyncio.create_task(asyncio.to_thread(...))`; drop window if previous inference still running
- `audio_event.json` as a separate file (not touching `voice_state.json`)
- All tests pass without ONNX model file present
- `_on_result` callback always resets `_running` in `finally` block (exception safety)

**Must NOT Have:**
- Auto-download of model file
- SQLite event history table
- Second dashboard panel/widget for events
- Per-class threshold configuration
- Fine-tuning or retraining support
- Any blocking call in `process_frame`
- Any audio-event code in `voice_state_observer.py`

---

## Task Flow

```
Settings + config.toml
        |
        v
[1] src/audio_event/class_map.py        (new — static data + hardcoded allowlist)
[2] src/audio_event/classifier.py        (new — YamnetClassifier)
[3] src/audio_event/writer.py            (new — write_audio_event atomic writer)
[4] src/audio_event/observer.py          (new — AudioEventObserver FrameProcessor)
        |
        v
[5] src/config.py                        (edit — add settings fields)
[6] src/pipeline/build.py                (edit — wire observer)
[7] src/watch/data.py                    (edit — read audio_event.json)
[8] src/watch/widgets.py                 (edit — render event in VoiceStateBar)
[9] pyproject.toml                       (edit — optional dep group)
[10] tests/test_audio_event.py           (new — unit tests)
```

---

## Detailed TODOs

### Step 1 — Static data and classifier (`src/audio_event/`)

**File: `src/audio_event/__init__.py`** (new)
- Empty init, package marker.

**File: `src/audio_event/class_map.py`** (new)
- `AUDIOSET_CLASSES: list[str]` — all 521 AudioSet display names, indexed by class ID (0..520). Sourced from the canonical `yamnet_class_map.csv`.
- `ALLOWLIST: dict[int, str]` — a **hardcoded dictionary** mapping class index to display name for the ~20 curated event types. Each entry is a literal `index: "Label"` pair. No substring matching, no runtime construction from `AUDIOSET_CLASSES`. This eliminates substring-match fragility (e.g., "Hiss" accidentally matching unrelated labels).
  - Human: Laughter, Giggle, Cough, Sneeze, Screaming, Crying (sobbing), Yawn, Whispering
  - Pets: Bark, Howl, Bow-wow, Whimper (dog), Meow, Purr, Hiss, Caterwaul, Bird vocalization, Crowing
- `def label_for_index(idx: int) -> str | None` — returns `ALLOWLIST.get(idx)`.

**Acceptance criteria:**
- `len(AUDIOSET_CLASSES) == 521`
- All 18+ target labels resolve to at least one class index in `ALLOWLIST`
- `label_for_index` returns `None` for non-allowlisted indices (e.g., index 0 = "Speech")
- `ALLOWLIST` is a plain `dict[int, str]` literal, not computed at import time

**File: `src/audio_event/classifier.py`** (new)
- Class `YamnetClassifier`:
  - `__init__(self, model_path: Path)` — loads ONNX model via `onnxruntime.InferenceSession`. Validates input name and shape.
  - `def classify(self, waveform: np.ndarray) -> list[tuple[int, float]]` — runs inference on a float32 waveform (0.96 s at 16 kHz = 15360 samples). Returns list of `(class_index, score)` sorted descending by score. Waveform is expected as 1-D float32 in [-1.0, 1.0].
  - `WINDOW_SAMPLES: int = 15360` (class constant)
  - `SAMPLE_RATE: int = 16000` (class constant)
- No async code in this class — it is called from `asyncio.to_thread` by the observer.

**Acceptance criteria:**
- `YamnetClassifier(path)` raises `FileNotFoundError` if path does not exist
- `classify()` returns a list of `(int, float)` tuples, length 521 (one per class)
- Waveform length != 15360 raises `ValueError`

### Step 2 — Event writer and pipeline observer (`src/audio_event/`)

**File: `src/audio_event/writer.py`** (new)
- `def write_audio_event(path: Path, label: str, score: float) -> None`:
  - Writes `{"label": label, "score": round(score, 3), "ts": time.time()}` to `path` atomically via tmpfile + `os.replace`.
  - Wraps in `try/except OSError` with `logger.warning` (matches existing `write_voice_state` error-handling pattern).
- This file lives in `src/audio_event/`, NOT in `src/pipeline/stages/voice_state_observer.py`. The voice state observer must not gain any audio-event code.

**Acceptance criteria:**
- `write_audio_event` produces valid JSON with `label`, `score`, `ts` keys
- Atomic write via tmpfile + `os.replace`
- `OSError` is caught and logged, never raised

**File: `src/audio_event/observer.py`** (new)
- Class `AudioEventObserver(FrameProcessor)`:
  - `__init__(self, classifier: YamnetClassifier, state_file: Path, *, threshold: float = 0.4)`
  - Internal state: `_buf: bytearray` accumulating raw int16 PCM bytes, `_prev_label: str | None`, `_running: bool` (inference guard flag).
  - `async def process_frame(self, frame, direction)`:
    1. Call `super().process_frame(frame, direction)`.
    2. If `frame` is `InputAudioRawFrame`: append `frame.audio` bytes to `_buf`.
    3. When `len(_buf) >= 15360 * 2` (0.96 s of int16 at 16 kHz = 30720 bytes):
       a. If `_running` is True: discard the window (drop policy), reset `_buf`, push frame, return.
       b. Set `_running = True`, extract window bytes, reset `_buf`.
       c. Convert int16 bytes to float32 numpy array (divide by 32768.0).
       d. Create an asyncio task for the inference and attach a done-callback:
          ```python
          task = asyncio.create_task(asyncio.to_thread(self._infer, waveform))
          task.add_done_callback(self._on_result)
          ```
          This matches the existing pattern in `src/pipeline/stages/usage_recorder.py:139`. `add_done_callback` fires on the event loop thread because `create_task` schedules on the current loop. No `call_soon_threadsafe` is needed.
    4. Always `await self.push_frame(frame, direction)` — observe-only, never swallow frames.
  - `def _infer(self, waveform: np.ndarray) -> tuple[str, float] | None`:
    - Call `self._classifier.classify(waveform)`.
    - Filter results through `label_for_index` (allowlist) and `threshold`.
    - Return `(label, score)` of the top allowlisted hit, or `None`.
  - `def _on_result(self, fut: asyncio.Future) -> None`:
    - **Must handle both success and exception paths.** Structure:
      ```python
      try:
          result = fut.result()  # raises if _infer threw
      except Exception:
          logger.warning("audio_event: inference failed", exc_info=True)
          self._prev_label = None
          return
      finally:
          self._running = False  # ALWAYS reset, even on exception
      ```
    - If `result` is not `None` and `result[0] == self._prev_label`: emit event — call `write_audio_event(self._state_file, label, score)`.
    - Update `self._prev_label = result[0] if result else None`.

- Factory: `def create_audio_event_observer(settings: Settings) -> AudioEventObserver | None`:
  - If `not settings.audio_event_detection_enabled`: return `None`.
  - Try `import onnxruntime`; on `ImportError`: log warning, return `None`.
  - If model path does not exist: log warning, return `None`.
  - Construct `YamnetClassifier(settings.yamnet_model_path)`.
  - Return `AudioEventObserver(classifier, settings.audio_event_file, threshold=settings.audio_event_threshold)`.

**Acceptance criteria:**
- Observer passes through every frame unchanged (observe-only)
- When `_running` is True, accumulated window bytes are discarded (no queue)
- 2-consecutive-window agreement required before `write_audio_event` is called
- Factory returns `None` for all three failure modes (disabled, import error, missing file) with a log warning each
- No blocking calls in `process_frame`
- `_on_result` always resets `_running = False` via `finally` block, even when `_infer` raises

### Step 3 — Settings and config

**File: `src/config.py`** (edit)
- Add to `Settings` dataclass:
  ```python
  audio_event_detection_enabled: bool = False
  audio_event_threshold: float = 0.4
  yamnet_model_path: Path = field(
      default_factory=lambda: HEARE_HOME / "models" / "yamnet.onnx"
  )
  audio_event_file: Path = field(
      default_factory=lambda: HEARE_HOME / "audio_event.json"
  )
  ```
- These four fields are loaded from `config.toml` via the existing generic loop (Path coercion already handled for `Path` fields; `bool`/`float` are native TOML types).

**Acceptance criteria:**
- `load_settings()` returns defaults when config.toml has no audio_event keys
- `load_settings()` picks up `audio_event_detection_enabled = true` from TOML
- `yamnet_model_path` resolves to `~/.heare/models/yamnet.onnx`

### Step 4 — Pipeline wiring

**File: `src/pipeline/build.py`** (edit)

In `_assemble_native_stages`:
- Add keyword-only parameter `audio_event_observer: Any = None`, placed immediately after the existing `input_mute_gate: Any = None` parameter in the signature.
- Insert it immediately after `input_mute_gate` and before `speaker_buffer`:
  ```python
  if input_mute_gate is not None:
      stages.append(input_mute_gate)
  if audio_event_observer is not None:
      stages.append(audio_event_observer)
  if speaker_buffer is not None:
      stages.append(speaker_buffer)
  ```
  This placement ensures muted audio is already dropped (by input_mute_gate) so no wasted inference, and the observer sees raw PCM before STT or speaker buffering.

In `build_pipeline`:
- After the `input_mute_gate` creation block (line ~575), add:
  ```python
  # Audio event detection (YAMNet) — opt-in, off by default.
  audio_event_observer = None
  if settings.audio_event_detection_enabled:
      try:
          from src.audio_event.observer import create_audio_event_observer
          audio_event_observer = create_audio_event_observer(settings)
          if audio_event_observer is not None:
              logger.info("audio_event: YAMNet observer active (threshold=%.2f)", settings.audio_event_threshold)
      except Exception:
          logger.exception("audio_event: observer creation failed (non-fatal)")
  ```
- Pass `audio_event_observer=audio_event_observer` to `_assemble_native_stages`.

**Acceptance criteria:**
- When `audio_event_detection_enabled = false` (default), no import of `src.audio_event` occurs
- When enabled but model missing, pipeline builds successfully with a warning
- Observer sits between `input_mute_gate` and `speaker_buffer` in the stage list
- Existing tests for `_assemble_native_stages` still pass (new kwarg has default `None`)

### Step 5 — Dashboard integration

**File: `src/watch/data.py`** (edit)
- Add `AudioEventData` frozen dataclass:
  ```python
  @dataclass(frozen=True)
  class AudioEventData:
      label: str | None
      score: float
      ts: float
  ```
- Add `read_audio_event(path: Path) -> AudioEventData`:
  - Reads `audio_event.json`, returns parsed data. Returns `AudioEventData(label=None, score=0.0, ts=0.0)` on missing/corrupt file.
- Add `audio_event: AudioEventData` field to `DashboardSnapshot`.
- Update `fetch_dashboard_state` to call `read_audio_event(settings.audio_event_file)` and include the result in the snapshot.

**File: `src/watch/widgets.py`** (edit)
- `VoiceStateBar.refresh_data` gains a second parameter: `refresh_data(self, voice: VoiceStateData, audio_event: AudioEventData) -> None`. Store `self._audio_event = audio_event`.
- In `_build_text`, after the existing state/partial/final rendering, append an event line when `self._audio_event.label` is not `None` and `time.time() - self._audio_event.ts < EVENT_TTL_S` (5.0 s):
  ```
  event    Laughter (0.72)         [styled bold magenta]
  ```
- Add class constant `EVENT_TTL_S: float = 5.0`.

**File: `src/watch/app.py`** (edit)
- Update the `_refresh_data` method's voice_bar call from:
  ```python
  voice_bar.refresh_data(snapshot.voice_state)
  ```
  to:
  ```python
  voice_bar.refresh_data(snapshot.voice_state, snapshot.audio_event)
  ```

**Acceptance criteria:**
- Dashboard renders correctly when `audio_event.json` does not exist (shows no event line)
- Event line auto-decays after `EVENT_TTL_S` seconds (reader-side, no timer on writer)
- No import of `onnxruntime` or `numpy` in the watch/data path
- `fetch_dashboard_state` populates the `audio_event` field in the snapshot

### Step 6 — Optional dependency and model conversion docs

**File: `pyproject.toml`** (edit)
- Add optional-dependency group:
  ```toml
  audio-event = [
      "onnxruntime>=1.17",
      "numpy>=1.24",
  ]
  ```
  Note: `numpy` may already be pulled in transitively by `speechbrain` (speaker group) but it is not a direct dep today. Making it explicit in this group avoids surprise.

**Model conversion docs** (inline in `src/audio_event/classifier.py` module docstring):
- Include a conversion recipe using `tf2onnx` since no canonical prebuilt YAMNet ONNX exists:
  ```
  # Install: pip install tf2onnx tensorflow-hub
  # Convert:
  #   python -m tf2onnx.convert \
  #     --saved-model ./yamnet_saved_model \
  #     --output ~/.heare/models/yamnet.onnx \
  #     --opset 13
  # Verify SHA256 of the output and record it for reproducibility.
  # The TF SavedModel can be obtained from:
  #   https://tfhub.dev/google/yamnet/1
  ```
- No download script. The conversion recipe is the authoritative acquisition path.

**Acceptance criteria:**
- `uv pip install -e ".[audio-event]"` installs `onnxruntime` and `numpy`
- Conversion recipe in the docstring is copy-pasteable and references a stable TF Hub URL

### Step 7 — Tests (`tests/test_audio_event.py`)

All tests run without the ONNX model file. Tests that would need the model are gated behind `@pytest.mark.skipif(not Path(...).exists(), reason="yamnet.onnx not present")` or an env var `HEARE_TEST_YAMNET=1`.

| Test name | What it asserts |
|-----------|----------------|
| `test_class_map_length` | `len(AUDIOSET_CLASSES) == 521` |
| `test_allowlist_is_hardcoded_dict` | `ALLOWLIST` is a `dict[int, str]`; every value is a string; every key is in range 0..520 |
| `test_allowlist_coverage` | Every target label resolves to at least one class index |
| `test_label_for_index_speech_excluded` | `label_for_index(0)` returns `None` (Speech is not allowlisted) |
| `test_label_for_index_laughter` | `label_for_index(idx)` returns `"Laughter"` for the correct index |
| `test_observer_passthrough` | Observer forwards all frame types unchanged (mock classifier) |
| `test_observer_drops_when_busy` | When `_running=True`, accumulated bytes are discarded and frame still forwarded |
| `test_observer_two_window_confirmation` | Event is NOT emitted on first window match; IS emitted when second consecutive window matches same label (mock classifier) |
| `test_observer_label_change_resets` | Window 1 = Laughter, Window 2 = Cough: no event emitted; Window 3 = Cough: event emitted |
| `test_observer_below_threshold` | Score below 0.4 on allowlisted class: no event emitted |
| `test_observer_running_resets_on_infer_exception` | Mock classifier to raise; assert `_running` returns to `False` after `_on_result` fires |
| `test_factory_disabled` | `create_audio_event_observer(settings)` returns `None` when `audio_event_detection_enabled=False` |
| `test_factory_missing_model` | Returns `None` + logs warning when model path doesn't exist |
| `test_factory_missing_onnxruntime` | Returns `None` + logs warning when `onnxruntime` import fails (mock) |
| `test_build_pipeline_does_not_import_when_disabled` | Assert `src.audio_event` is NOT in `sys.modules` after `build_pipeline` (or `_assemble_native_stages`) runs with the flag off |
| `test_write_audio_event_atomic` | `write_audio_event` produces valid JSON with expected keys; file is atomically replaced |
| `test_read_audio_event_missing_file` | `read_audio_event` returns default `AudioEventData(label=None, ...)` for nonexistent path |
| `test_read_audio_event_corrupt` | Returns default on corrupt JSON |
| `test_voice_state_bar_with_event` | `VoiceStateBar` renders event label when `audio_event.ts` is recent |
| `test_voice_state_bar_event_decay` | `VoiceStateBar` hides event label when `audio_event.ts` is older than TTL |
| `test_dashboard_snapshot_includes_audio_event` | `fetch_dashboard_state` populates the `audio_event` field in `DashboardSnapshot` |
| `test_settings_defaults` | New settings fields have correct defaults |
| `test_smoke_classifier` | **Gated on HEARE_TEST_YAMNET=1.** Loads real model, runs inference on a synthetic sine wave, gets 521 scores back |

**Acceptance criteria:**
- `pytest tests/test_audio_event.py` passes in CI without ONNX model file
- Smoke test passes locally when model is present and env var is set

---

## No-Model Verification Checklist

These steps verify the feature works correctly without the ONNX model file present:

1. Set `audio_event_detection_enabled = true` in `config.toml` (model file absent).
2. Start daemon. Confirm log contains `"audio_event: model file not found"` warning.
3. Confirm pipeline starts normally — STT, TTS, and all existing features work.
4. Run `pytest tests/test_audio_event.py` — all non-smoke tests pass.
5. Open watch dashboard — `VoiceStateBar` renders without errors, no event line shown.
6. Confirm `audio_event.json` is NOT created (no writer instantiated).
7. Set `audio_event_detection_enabled = false` — confirm no `src.audio_event` import in `sys.modules`.

**With-model verification** (local dev only):

1. Place `yamnet.onnx` at `~/.heare/models/yamnet.onnx`.
2. Set `audio_event_detection_enabled = true`. Start daemon.
3. Confirm log contains `"audio_event: YAMNet observer active"`.
4. Generate laughter/cough near mic. Confirm `audio_event.json` is written with correct label after 2 consecutive windows.

---

## Failure Modes

| Failure | Detection | Response |
|---------|-----------|----------|
| Model file missing | `Path.exists()` check in factory | Log warning, return `None`, pipeline builds without observer |
| `onnxruntime` not installed | `ImportError` in factory | Log warning, return `None`, pipeline builds without observer |
| Sample rate != 16000 | Pipeline's `LocalAudioTransportParams(audio_in_sample_rate=16000)` guarantees this | Architecture invariant; no runtime check needed |
| Inference exception (ONNX runtime error) | `_on_result` calls `fut.result()` inside `try/except` | Log warning, reset `_prev_label` to `None`, `_running` reset to `False` in `finally` block. Observer continues processing frames normally. |
| `audio_event.json` write failure | `OSError` in `write_audio_event` | Log warning (matches existing `write_voice_state` pattern), continue |
| Feature enabled but numpy not installed | `ImportError` when observer module loads | Caught by factory's outer `try/except`, logged, returns `None` |
| Inference exception leaves `_running=True` permanently | Cannot happen | Handled by `add_done_callback` which fires on both success and exception paths; `_on_result` calls `fut.result()` inside `try/except` and always resets `_running` in `finally` |

---

## Backward Compatibility

- `voice_state.json` is **unchanged**. Audio events go to a sibling file `audio_event.json`. The dashboard reader handles the file being absent (returns defaults). Zero impact on existing `VoiceStateObserver` or `VoiceStateBar` behavior.
- `voice_state_observer.py` is **not edited**. All audio-event write logic lives in `src/audio_event/writer.py`.
- `_assemble_native_stages` gains one new kwarg `audio_event_observer` with `default=None`, placed after `input_mute_gate` in the signature. All existing callers and tests are unaffected.
- `VoiceStateBar.refresh_data` gains a second parameter `audio_event: AudioEventData`. The single call site in `src/watch/app.py:_refresh_data` is updated to pass it.
- `DashboardSnapshot` gains one new field `audio_event: AudioEventData`. The `fetch_dashboard_state` function populates it.
- `Settings` gains four new fields with defaults matching "feature off." `load_settings()` already handles unknown TOML keys gracefully.

---

## Out of Scope

- **SQLite event history** — no `audio_events` table. Events are ephemeral (file-based, most recent only).
- **Dedicated dashboard panel** — events render inline in the existing `VoiceStateBar`, not in a new widget.
- **Auto-download** of model file — user converts via `tf2onnx` using the documented recipe.
- **Download script** — replaced by conversion docs in the classifier module docstring.
- **Per-class thresholds** — single global `audio_event_threshold` applies to all allowlisted classes.
- **Fine-tuning / retraining** — model is used as-is from AudioSet pretrained weights.
- **Streaming inference** — model requires a complete 0.96 s window; no partial-window support.
- **Multi-event output** — only the top allowlisted class per window is considered.
- **Whisper non-speech tokens** — deferred to a separate follow-up plan (see Options table, row 5).

---

## ADR: Audio Event Detection Architecture

**Decision:** YAMNet via ONNX Runtime with a separate `audio_event.json` file, hardcoded allowlist, and `create_task`/`add_done_callback` async pattern.

**Drivers:** Latency budget (< 1 ms sync overhead), false-positive rate (2-window confirmation), model footprint (~3 MB), zero-crash tolerance.

**Alternatives considered:** PANNs (too large), AST (too slow, needs torch), rule-based (insufficient classification), Whisper non-speech tokens (insufficient label coverage for primary use case, deferred as complementary).

**Why chosen:** YAMNet is the only option that meets all five principles simultaneously. The ~3 MB ONNX model runs in < 50 ms on CPU, the 521-class AudioSet label set covers all target events, and ONNX Runtime has no torch dependency.

**Consequences:** User must convert the model via `tf2onnx` (one-time setup). The feature is opt-in and entirely invisible to users who do not enable it. Dashboard gains one new field to read. One new package (`src/audio_event/`) with 4 modules.

**Follow-ups:** (1) Whisper non-speech token observer as a complementary Phase 0. (2) Event TTL on dashboard may need tuning after real-world usage (currently 5.0 s).

---

## Changelog

| MUST_FIX # | Issue | Resolution |
|------------|-------|------------|
| 1 | Async pattern contradicted itself (`to_thread` + `call_soon_threadsafe`) | Rewritten to use `asyncio.create_task(asyncio.to_thread(...))` with `task.add_done_callback(self._on_result)`, matching `usage_recorder.py:139` pattern. Removed `call_soon_threadsafe`. |
| 2 | ONNX model URL unresolved; download script unimplementable | Removed download script entirely. Replaced with `tf2onnx` conversion recipe in classifier module docstring, referencing stable `tfhub.dev/google/yamnet/1` URL. |
| 3 | `write_audio_event` placed in `voice_state_observer.py` (cohesion violation) | Moved to new file `src/audio_event/writer.py`. `voice_state_observer.py` is no longer edited. |
| 4 | Step 3 was stream-of-consciousness (voice_state.json vs audio_event.json) | Rewritten as a clean single decision: `audio_event.json` is a separate sibling file. Step 3 (old) merged into Step 2 as the writer module. |
| 5 | Allowlist construction unspecified (substring vs hardcoded) | Specified as hardcoded `dict[int, str]` literal in `class_map.py`. No substring matching. Acceptance criteria updated. |
| 6 | 3 missing tests | Added: `test_build_pipeline_does_not_import_when_disabled`, `test_observer_running_resets_on_infer_exception`, `test_dashboard_snapshot_includes_audio_event`. |
| 7 | Whisper non-speech tokens not addressed | Added as Option 5 in the RALPLAN-DR Options table with disposition: COMPLEMENTARY, deferred to follow-up Phase 0 with explicit rationale. |
| 8 | Missing failure mode: inference exception leaves `_running=True` | Added to Failure Modes table with explanation of `finally` block in `_on_result`. `_on_result` code example explicitly shows `try/except/finally` structure. |
| (Critic note) | Dashboard refresh pattern unspecified | Specified: `VoiceStateBar.refresh_data` gains second `audio_event` parameter; `app.py:_refresh_data` passes `snapshot.audio_event`. |
| (Critic note) | `audio_event_observer` kwarg placement unspecified | Specified: placed immediately after `input_mute_gate` in the `_assemble_native_stages` signature. |
| (Critic note) | No-model verification checklist missing | Added 7-step no-model checklist + 4-step with-model checklist. |
