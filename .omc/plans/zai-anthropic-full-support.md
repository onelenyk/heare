# Full z.ai Anthropic API Support in Pipecat (Runtime Switching)

**Date:** 2026-05-01
**Status:** REVISED (v2) — addressing Architect/Critic ITERATE consensus
**Mode:** RALPLAN-DR (short consensus + deliberate enhancements: pre-mortem + expanded test plan)
**Complexity:** MEDIUM-HIGH
**Scope:** ~3 files modified (`src/switchable_llm.py`, `src/pipeline.py`, `src/main.py`), ~5 test files added/extended.
**Predecessor:** `.omc/plans/runtime-llm-provider-switching.md` (v1 — shipped the OpenAI-compatible scaffolding; this v2 closes the Anthropic delegation gap).
**Revision history:** v1 DRAFT rejected by Architect/Critic consensus (8 critical, 6 major, 5 minor findings). This v2 addresses all findings inline. Changed sections are marked with `[REV]`.

---

## 0. Problem Statement (verifiable)

`SwitchableLLMService` (`src/switchable_llm.py:18`) inherits `OpenAILLMService` and constructs an `AnthropicLLMService` instance at lines 51-68 that is never reached by frames or method calls. The class docstring (lines 21-26) explicitly admits this:

> "z.ai client is initialized but frames don't properly route through the pipeline when AnthropicLLMService is called outside the pipeline context."

Concretely:

- `process_frame()`, `get_chat_completions()`, `build_chat_completion_params()`, `register_function()`, and the LLM-context aggregation pair all bind to `OpenAILLMService` because that is the inherited parent (line 18: `class SwitchableLLMService(OpenAILLMService)`).
- `_sync_provider()` (lines 74-101) updates `self._active_provider`, but no method reads that flag to route to `self._zai_service` (constructed on line 59).
- The pipeline at `src/pipeline.py:394-401` plugs `llm_service` into the `Pipeline(stages)` exactly once at startup. There is no second LLM instance and no router stage between the user aggregator and the LLM.
- The `set_provider` direct tool (`src/direct_tools.py:154-155`, `_execute_set_provider` at `src/direct_tools.py:3260`) writes `~/.heare/provider` but the daemon's running `SwitchableLLMService` only flips a string, not an active client.

This is the architectural gap to close.

---

## 1. RALPLAN-DR Summary

### 1.1 Principles

1. **Single pipeline, single processor slot.** Pipecat's `Pipeline(stages)` resolves edges at construction time via `link()` (`frame_processor.py:581-589`), setting `_next` / `_prev` on each `FrameProcessor`. The LLM occupies one slot in `_assemble_native_stages` (`src/pipeline.py:500-518`). Solutions that try to swap two `LLMService` instances in/out of the pipeline at runtime are out of scope.
2. **Hot-swap without restart.** `~/.heare/provider` flips must take effect on the next user turn (<=1 LLM call latency), matching the v1 design and the existing `set_provider` tool contract.
3. **Conservative blast radius.** Frame routing, tool registration (`register_all_tools`), context aggregation (`LLMContextAggregatorPair`), and observer hooks (`assistant_response_logger`, `tts_fade_observer`) must continue to work for both providers without changes to `pipeline.py`'s stage graph.
4. **Provider-shaped fidelity.** When z.ai is active, the request must hit `https://api.z.ai/api/anthropic` via the `AsyncAnthropic` client with Anthropic message-shape (system as a top-level field, content blocks, tool_use/tool_result blocks). Re-using the OpenAI client against z.ai's Anthropic endpoint is incorrect and the source of the current bug.
5. **Fail closed, never crash.** Missing `ZAI_API_KEY`, malformed provider file, transient z.ai 5xx, and tool-call schema mismatches must degrade to OpenRouter with a single warning log — never raise into the audio pipeline.

### 1.2 Decision Drivers (top 4) `[REV — added #4 per Major #6]`

1. **Frame-shape compatibility:** Pipecat's `OpenAILLMService.process_frame` emits `LLMTextFrame`/`LLMFullResponseStartFrame`/`LLMFullResponseEndFrame`/`FunctionCallInProgressFrame`/`FunctionCallResultFrame`. Whatever wraps z.ai must emit the same frames so downstream stages (`assistant_response_logger`, `assistant_aggregator`, `tts`) keep working.
2. **Tool/function-call parity:** `register_all_tools` (`src/llm_tools.py:615`) calls `llm.register_function(name, handler, cancel_on_interruption=...)`. Both providers must honour this call surface. `register_function` only fans out handler registration; tool schemas are translated by each delegate's own adapter (`OpenAILLMAdapter` vs `AnthropicLLMAdapter`) from the shared universal `LLMContext` + `ToolsSchema`. `[REV — Major #2: clarified schema translation]`
3. **Latency cost of provider sync:** `_sync_provider` must NOT be called per-frame. It is called only on turn-start frames (`LLMContextFrame` / `LLMMessagesFrame`). The mtime-gated read (`src/switchable_llm.py:81-85`) is correct for that cadence. `[REV — Major #5: explicitly scoped to turn-start only]`
4. **Identity propagation (pipeline wiring):** `[REV — NEW, Major #6]` Delegates are not linked into the pipeline graph — only the wrapper has `_next`, `_prev`, `_task_manager`, `_clock`, and `_observer` set by `FrameProcessor.setup()` (`frame_processor.py:557-571`). When a delegate calls `push_frame()` or `broadcast_frame()`, its internal `__internal_push_frame()` checks `self._next` / `self._prev` — both are `None`. Frames would be silently dropped. The wrapper MUST relay delegate-emitted frames through itself.

### 1.3 Viable Options

#### Option A — Delegating wrapper with frame relay (composition over inheritance)

Replace `SwitchableLLMService(OpenAILLMService)` with `SwitchableLLMService(LLMService)` (Pipecat's abstract base) that holds both an `OpenAILLMService` and an `AnthropicLLMService` as members. Before each delegate call, monkey-patch `delegate.push_frame` and `delegate.broadcast_frame` to relay through the wrapper's own `push_frame`/`broadcast_frame` (which have valid `_next`/`_prev` links from `pipeline.link()`).

- Pros:
  - Each delegate is a fully-formed Pipecat LLM service — frame shapes and tool handling are correct by construction.
  - Switching is just `self._active = self._zai if active=='zai' else self._or`. No client/model swap inside a single class.
  - Allows future provider additions (Vertex, Bedrock) without inheritance gymnastics.
  - Frame relay is a single method override — delegates don't need to know about the pipeline.
- Cons:
  - Must implement the abstract `LLMService` surface correctly, including lifecycle fan-out with deduplication.
  - `register_function` calls landing during boot must register on both delegates.
  - Monkey-patching `push_frame`/`broadcast_frame` on delegates requires care to avoid breaking internal delegate state.

#### Option B — Subclass `OpenAILLMService` and intercept Anthropic-bound frames internally

Keep `class SwitchableLLMService(OpenAILLMService)`. Override `_process_context` so that when `_active_provider == "zai"`, we call `AsyncAnthropic.messages.create` directly, translate the OpenAI-shape context into Anthropic-shape, and re-emit Pipecat's standard frames manually.

- Pros:
  - No abstract-base implementation work. The OpenAI path is unchanged.
  - Single class, single processor instance — no frame relay needed.
- Cons:
  - We re-implement Pipecat's `AnthropicLLMService` frame emission, streaming buffer logic, tool-use/tool-result decoding, and metric emission. This is exactly the bug class from v1.
  - Tool registration via `register_function` on `OpenAILLMService` doesn't automatically translate to Anthropic tool schemas.
  - Brittle: every Pipecat upgrade that touches `_process_context` breaks our override.

#### Option C — Two-LLM pipeline with a router `FrameProcessor`

Insert a `_ProviderRouter` `FrameProcessor` upstream of the LLM that forks frames to one of two LLM sub-pipelines.

- Pros:
  - Each LLM service is used as Pipecat intends — fully wired with `link()`.
- Cons:
  - `ParallelPipeline` runs both branches concurrently — wrong XOR semantics.
  - State (context aggregator) must be shared but only one branch may write.
  - 3-4x more moving parts than Options A/B.

#### Invalidation rationale (why not B or C)

- **Option B** fails Driver #1 (frame-shape compatibility) and Principle #4 (provider-shaped fidelity): hand-rolling Anthropic streaming inside an OpenAI subclass duplicates `AnthropicLLMService` and reintroduces the bug class.
- **Option C** fails Principle #3 (conservative blast radius): rewriting the pipeline graph for what is fundamentally a router decision is overkill, and `ParallelPipeline` semantics are wrong.

**Selected:** Option A (delegating wrapper with frame relay).

---

## 2. Pre-mortem (Deliberate Mode) `[REV — Critical #2 rewrote scenario #2; Major #1 aligned mitigations]`

Three realistic ways this ships and breaks within two weeks of merge, plus mitigations baked into the plan.

### Failure scenario 1 — "Tools work on OpenRouter, fail silently on z.ai"

**How it happens:** `register_all_tools` registers handlers on the active delegate at boot. User toggles to z.ai mid-conversation. The Anthropic delegate has no handlers. Tool calls from z.ai are dropped (Pipecat logs a warning for unknown function names at `llm_service.py:719`, but audio keeps flowing — user hears "I'll do that" and nothing happens).

**Mitigation (planned):**
- `SwitchableLLMService.register_function` fans out the registration to **both** `_or_service` and `_zai_service` at the time of the call (see Step 2 below).
- Acceptance test `test_register_function_fans_out_to_both_delegates` (U5) asserts both delegates have the handler after one `register_function` call.
- Boot log line `"switchable_llm: registered N tools on both providers"` — visible in `make watch` and `.omc/logs/`.

### Failure scenario 2 — "Provider file flip mid-turn corrupts context" `[REV — Critical #2: corrected gate trigger]`

**How it happens:** User says utterance N. OpenRouter starts streaming (inside `BaseOpenAILLMService._process_context` at `base_llm.py:444`). Provider file is flipped to "zai" (via dashboard hotkey or `set_provider`). If `_sync_provider` runs again before the turn completes, a subsequent frame could be routed to the Anthropic delegate mid-stream, corrupting context.

**Mitigation (planned):**
- The sticky-turn gate is keyed on **incoming turn-start frames** (`OpenAILLMContextFrame` / `LLMContextFrame` / `LLMMessagesFrame`), NOT on `LLMFullResponseStartFrame` which is emitted *by* the delegate. Specifically: when the wrapper's `process_frame` sees an `OpenAILLMContextFrame`, `LLMContextFrame`, or `LLMMessagesFrame`, it locks `_turn_delegate` to the current active delegate. All subsequent frames (including `FunctionCallResultFrame`, `FunctionCallInProgressFrame`, `InterruptionFrame`) route to that locked delegate until the wrapper observes `LLMFullResponseEndFrame` (emitted by the delegate, relayed through the wrapper's frame-relay hook — see Step 2).
- `_sync_provider` is NOT called per-frame. It is called ONLY when a turn-start frame arrives AND `_turn_delegate` is None (no turn in flight).
- Test: `test_provider_flip_during_turn_defers_to_next_turn` (U6) pushes an `LLMContextFrame`, then writes "zai" to the provider file, then pushes additional frames. Asserts the locked delegate stays OpenRouter until `LLMFullResponseEndFrame` is relayed.

### Failure scenario 3 — "z.ai 401/5xx silently degrades but logs explode"

**How it happens:** `ZAI_API_KEY` rotated; user has not updated `.env`. Every turn now logs a full Anthropic exception traceback at ERROR. `.omc/logs/` fills with megabytes of stack traces. STT/TTS audio still works (good), but logs become unreadable.

**Mitigation (planned):**
- The wrapper's `process_frame` catches `anthropic.AuthenticationError` / `anthropic.APIStatusError` from the delegate and logs **once per minute** (rate-limited via `_last_error_log_ts`). It **forces** `_active_provider = "openrouter"` permanently (for this process lifetime) and pushes an `ErrorFrame` upstream so the indication subsystem fires.
- Test: `test_zai_auth_error_falls_back_and_rate_limits_logs` (U7).

---

## 3. Expanded Test Plan (Deliberate Mode) `[REV — Critical #5: added U9; updated U3, U6]`

### 3.1 Unit tests — `tests/test_switchable_llm.py` (new)

| # | Test | What it asserts |
|---|------|---|
| U1 | `test_init_with_both_keys` | Both delegates constructed; `active_provider == "openrouter"` by default. |
| U2 | `test_init_zai_key_missing_no_zai_delegate` | `_zai_service is None`; switching to "zai" stays on openrouter with warning log captured via `caplog`. |
| U3 | `test_init_only_zai_key` | `[REV — Minor #2: fixed contradiction]` If only z.ai key present, init **succeeds** (does not raise). `_or_service` is None. `active_provider == "zai"`. Switching to openrouter is a no-op (stays zai with warning). |
| U4 | `test_sync_provider_mtime_gated` | Multiple reads with same mtime call `Path.read_text` once; mtime change triggers re-read (monkeypatch `os.path.getmtime`). |
| U5 | `test_register_function_fans_out_to_both_delegates` | After `swit.register_function("bash", handler)`, both `_or_service._functions` and `_zai_service._functions` contain "bash". |
| U6 | `test_provider_flip_during_turn_defers_to_next_turn` | `[REV — Critical #2]` Push `LLMContextFrame` (turn-start), then write "zai" to provider file, then push additional data frames. Assert `_turn_delegate` stays OpenRouter. Then relay `LLMFullResponseEndFrame` from delegate. Assert next `LLMContextFrame` picks up zai. |
| U7 | `test_zai_auth_error_falls_back_and_rate_limits_logs` | Stub `_zai_service._process_context` to raise `AuthenticationError`; assert single ERROR log per 60s window, `_active_provider == "openrouter"` permanently, and `_turn_in_flight` is cleared. |
| U8 | `test_active_provider_property` | `active_provider` returns "zai" after flip + `_sync_provider`. |
| U9 | `test_delegate_frames_reach_downstream` | `[REV — Critical #5: NEW]` Wire a `FrameSpy` processor as `swit._next` (simulating pipeline link). Push `LLMContextFrame` into wrapper. Assert the spy captures relayed frames: `LLMFullResponseStartFrame`, >=1 `LLMTextFrame`, `LLMFullResponseEndFrame` — proving delegate-emitted frames traverse the wrapper's `push_frame` into the pipeline. Acceptance criterion: "after delegate emits N frames, wrapper's push_frame is observed N times by the spy." |

### 3.2 Integration tests — `tests/test_llm_tools.py` (extend)

| # | Test | What it asserts |
|---|------|---|
| I1 | `test_register_all_tools_visible_on_both_delegates` | After `register_all_tools(swit, settings=settings)`, `_or_service` and `_zai_service` each have all enabled tools. |
| I2 | `test_set_provider_tool_writes_file_and_takes_effect` | Call `_execute_set_provider("zai", settings)`; on next `_sync_provider`, active flips to "zai". |
| I3 | `test_tools_schema_is_provider_agnostic` | `build_tools_schema()` produces one `ToolsSchema` consumed by both delegates; tool counts match. |

### 3.3 End-to-end tests — `tests/integration/test_zai_e2e.py` (new, marker `@pytest.mark.integration`, skipped if no `ZAI_API_KEY`)

| # | Test | What it asserts |
|---|------|---|
| E1 | `test_zai_simple_completion` | Build a minimal pipeline with `SwitchableLLMService`, write "zai" to provider file, push an `LLMContextFrame`, capture downstream frames via spy; assert >=1 `LLMTextFrame` with non-empty text. |
| E2 | `test_zai_tool_call_roundtrip` | Same setup; LLM context contains a system prompt that triggers `bash` (e.g. "list files"). Assert `FunctionCallInProgressFrame("bash")` then `FunctionCallResultFrame` then a final `LLMTextFrame` — all captured by downstream spy. `[REV — Critical #5: E2 now explicitly asserts downstream visibility]` |
| E3 | `test_provider_swap_midconversation_e2e` | Run two turns on openrouter, flip provider file, run third turn on zai; assert the final `LLMTextFrame` was produced by the zai delegate (verifiable via spy on `_zai_service._process_context`). |

### 3.4 Observability tests — `tests/test_switchable_llm_observability.py` (new)

| # | Test | What it asserts |
|---|------|---|
| O1 | `test_provider_switch_logged_once_per_change` | Toggling provider 5x, only 5 INFO log lines `"switchable_llm: switched to <provider>"`. |
| O2 | `test_active_provider_exposed_for_dashboard` | `swit.active_provider` is callable from the watch dashboard tick; returns current string in O(1) (no file I/O when mtime unchanged). |
| O3 | `test_metric_per_turn_tagged_with_provider` | Wrapper sets `MetricsData(processor=..., model=...)` to include provider tag before each turn so dashboards can split costs/latency. |
| O4 | `test_indication_fires_on_zai_fallback` | When fallback triggers (scenario #3), an `ErrorFrame` is pushed upstream (captured by spy). |

### 3.5 Lint / type / ruff

`make lint` (ruff) must pass on the new module. No new pip deps. `make test` (full suite) green.

---

## 4. Full Implementation Plan

### 4.1 Requirements Summary

| Requirement | Source | Verifiable via |
|---|---|---|
| Hot-swap between OpenRouter and z.ai without restart | Task description; v1 plan Work Objectives | E3 |
| Frame routing must reach `AnthropicLLMService` when z.ai active | Task description ("methods never reach delegate"); `src/switchable_llm.py:21-26` | E1, E2, U9 |
| Delegate-emitted frames must reach downstream pipeline stages | `[REV — Critical #1, #3, #5]` | U9, E2 |
| `register_function` must work for both providers | `src/llm_tools.py:648`; `register_all_tools` contract | I1, U5 |
| Initialization validation for z.ai keys | Task description | U2, U3 |
| `set_provider` direct tool keeps working | `src/direct_tools.py:154`, `:3260` | I2 |
| No new pip deps | v1 plan Must NOT Have | `make lint && make test` |
| Mid-turn flips deferred to next turn | Pre-mortem #2 | U6, E3 |
| Auth/5xx errors degrade quietly | Pre-mortem #3 | U7, O4 |

### 4.2 Architecture & Design (delegation pattern with frame relay) `[REV — Critical #1, #3: frame relay; Critical #4: lifecycle; Major #4: event handlers resolved]`

```
Pipeline link chain (set by Pipeline.link() / FrameProcessor.setup()):
... -> user_aggregator -> [SwitchableLLMService] -> assistant_response_logger -> ...
                                  |
                  wrapper has _next, _prev, _clock,
                  _task_manager, _observer from setup()
                                  |
          +-----------+-----------+
          |                       |
    _or_service              _zai_service
    (OpenAILLMService)       (AnthropicLLMService)
    _next = None             _next = None
    _prev = None             _prev = None
          |                       |
    push_frame PATCHED:      push_frame PATCHED:
    -> wrapper.push_frame    -> wrapper.push_frame
    broadcast_frame PATCHED: broadcast_frame PATCHED:
    -> wrapper.broadcast_frame-> wrapper.broadcast_frame
```

#### 4.2.1 Frame relay mechanism `[REV — Critical #1, #3: this is the key fix]`

Delegates are NOT linked into the pipeline. Their `_next`/`_prev` are None. When a delegate internally calls `self.push_frame(LLMTextFrame(...))` (e.g. `anthropic/llm.py:544`, `base_llm.py:544`), the frame would be silently dropped by `__internal_push_frame` at `frame_processor.py:918` (`if direction == DOWNSTREAM and self._next` — `_next` is None).

**Solution: override `push_frame` and `broadcast_frame` on each delegate instance to relay through the wrapper.**

During `__init__`, after constructing each delegate:

```python
def _install_frame_relay(self, delegate):
    """Patch delegate's push_frame and broadcast_frame to relay through wrapper."""
    wrapper = self  # capture reference

    # Save originals for internal delegate calls that must NOT relay
    delegate._original_push_frame = delegate.push_frame
    delegate._original_broadcast_frame = delegate.broadcast_frame

    async def relayed_push_frame(frame, direction=FrameDirection.DOWNSTREAM):
        # Relay through the wrapper, which has valid _next/_prev
        await wrapper.push_frame(frame, direction)

    async def relayed_broadcast_frame(frame_cls, **kwargs):
        # Relay through the wrapper
        await wrapper.broadcast_frame(frame_cls, **kwargs)

    delegate.push_frame = relayed_push_frame
    delegate.broadcast_frame = relayed_broadcast_frame
```

This ensures:
- `LLMTextFrame` emitted by `AnthropicLLMService._process_context` at `anthropic/llm.py:544` reaches `wrapper.push_frame` which has valid `_next` -> `assistant_response_logger`.
- `broadcast_frame(FunctionCallInProgressFrame, ...)` at `llm_service.py:854` reaches `wrapper.broadcast_frame` which pushes both downstream and upstream through the wrapper's linked neighbors.
- `broadcast_frame(FunctionCallResultFrame, ...)` at `llm_service.py:874` — same relay path.
- `push_frame(LLMFullResponseStartFrame())` at `anthropic/llm.py:489` and `LLMFullResponseEndFrame()` at `anthropic/llm.py:643` — same relay path.

**Acceptance criterion: after delegate emits N frames, wrapper's push_frame is observed N times by downstream spy (test U9).**

#### 4.2.2 Sticky-turn gate `[REV — Critical #2: correct trigger frames]`

The gate prevents mid-turn provider switches. It is triggered by **incoming upstream frames**, not by delegate-emitted frames:

- **Lock:** When the wrapper's `process_frame` receives `OpenAILLMContextFrame`, `LLMContextFrame`, or `LLMMessagesFrame` (the turn-start frames — see `base_llm.py:596-605` and `anthropic/llm.py:669-676`), it calls `_sync_provider()`, resolves `_turn_delegate`, and locks it.
- **Sticky:** All subsequent frames arriving at the wrapper's `process_frame` (including `FunctionCallResultFrame` pushed upstream by the assistant aggregator, `InterruptionFrame`, etc.) are forwarded to the locked `_turn_delegate`.
- **Unlock:** When the wrapper's `push_frame` relay sees `LLMFullResponseEndFrame` coming from the delegate (relayed through the wrapper), it clears `_turn_delegate` and `_turn_in_flight`. The NEXT turn-start frame will re-evaluate the provider.

This is correct because `LLMFullResponseEndFrame` is always emitted by the delegate in a `finally` block (`base_llm.py:621`, `anthropic/llm.py:643`), guaranteeing unlock even on error/cancellation.

#### 4.2.3 Delegated surface table `[REV — Critical #3: added broadcast_frame; Critical #4: lifecycle clarified; Major #4: event handlers resolved]`

| Method | Routing | Notes |
|---|---|---|
| `process_frame(frame, direction)` | active-only (sticky per turn) | Turn-start frames trigger `_sync_provider`. All other frames go to locked delegate. Wrapper calls `await super().process_frame(frame, direction)` FIRST for `StartFrame`/`CancelFrame`/`InterruptionFrame` handling on the wrapper itself, then forwards to delegate. |
| `push_frame(frame, direction)` | wrapper-owned | Delegates' `push_frame` is patched to relay through wrapper. Wrapper's `push_frame` uses its own `_next`/`_prev` (valid from pipeline link). The relay hook also observes `LLMFullResponseEndFrame` to unlock the sticky-turn gate. |
| `broadcast_frame(frame_cls, **kw)` | wrapper-owned | `[REV — Critical #3]` Same relay pattern as `push_frame`. Used by `run_function_calls` at `llm_service.py:710` and `_run_function_call` at `llm_service.py:854`. |
| `start(frame)` | active-only `[REV — Critical #4]` | Only `start()` the currently active delegate. The inactive delegate is started lazily on first switch. Rationale: `LLMService.start()` (`llm_service.py:303-311`) calls `_create_sequential_runner_task()`. Fan-out would create double runner tasks and double metric pipelines. |
| `stop(frame)` / `cancel(frame)` / `cleanup()` | fan-out (both) | Safe to stop/cancel both — these are teardown operations. `stop()` cancels sequential runner task (`llm_service.py:320-322`). Running it on an unstarted delegate is a no-op (task is None). |
| `setup(setup)` | wrapper-only + propagate to delegates `[REV — Critical #4]` | Wrapper receives `setup()` from pipeline. It must propagate `FrameProcessorSetup(clock, task_manager, observer)` to both delegates so they can create tasks via `self.create_task()`. But delegates' `__create_input_task` is harmless — frames are relayed, not queued through delegates' own input queues. |
| `register_function(name, handler, ...)` | fan-out (both) | Both delegates must know every tool. Handler registration only; schema adaptation is per-delegate via their respective adapters. `[REV — Major #2]` |
| `unregister_function(name)` | fan-out (both) | Symmetric with above. |
| `_call_event_handler` / event handlers | relay via wrapper `[REV — Major #4: RESOLVED]` | Event handlers (`on_function_calls_started`, `on_completion_timeout`, `on_before_process_frame`, etc.) are registered on the wrapper via `_register_event_handler` (inherited from `FrameProcessor.__init__`). Delegates' event handlers fire internally but are not externally observable since delegates have no pipeline observers. The wrapper's own event handlers fire when the relay hook pushes frames, which triggers the wrapper's `_call_event_handler("on_before_push_frame", frame)` at `frame_processor.py:757`. External consumers (like `PipelineTask.event_handler`) attach to the wrapper, not to delegates. No fan-out needed — relay-through-wrapper is sufficient. |
| `run_inference(context, ...)` | active-only | Only the active provider produces inference results. |

#### 4.2.4 Lazy start for inactive delegate `[REV — Critical #4: lifecycle deduplication]`

Problem: `LLMService.start()` calls `_create_sequential_runner_task()` and `FrameProcessor.__start()` calls `__create_process_task()`. Fan-out would double these tasks.

Solution: only `start()` the active delegate when `StartFrame` arrives. Track `_started_delegates: set[str]` (values: `"or"`, `"zai"`). On provider switch, if the newly-active delegate hasn't been started yet:

```python
async def _ensure_delegate_started(self, delegate, key):
    if key not in self._started_delegates:
        await delegate.setup(self._delegate_setup)  # propagate FrameProcessorSetup
        # Replay the StartFrame that was saved during initial start
        await delegate.queue_frame(self._saved_start_frame, FrameDirection.DOWNSTREAM)
        self._started_delegates.add(key)
```

This avoids double-task creation and ensures metrics are only active on one delegate at a time.

### 4.3 Implementation Steps

#### Step 1 — Refactor `SwitchableLLMService` to inherit `LLMService` (`src/switchable_llm.py`) `[REV — Minor #1: clarified _or_service construction]`

Change line 18 from `class SwitchableLLMService(OpenAILLMService):` to `class SwitchableLLMService(LLMService):` (import `from pipecat.services.llm_service import LLMService`).

Delete `self._or_service = self` (current line 47). `[REV — Minor #1]` Instantiate `_or_service` as a **separate** `OpenAILLMService` object:

```python
self._or_service: OpenAILLMService | None = None
if openrouter_api_key:
    self._or_service = OpenAILLMService(
        api_key=openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
        model=openrouter_model,
        **kwargs,
    )
    self._install_frame_relay(self._or_service)

self._zai_service: AnthropicLLMService | None = None
if zai_api_key:
    self._zai_service = AnthropicLLMService(
        api_key=zai_api_key,
        model=zai_model,
        client=AsyncAnthropic(api_key=zai_api_key, base_url=zai_base_url),
    )
    self._install_frame_relay(self._zai_service)
```

Add z.ai boot smoke test `[REV — Major #3]`:

```python
if zai_api_key:
    # Validate API key shape at boot (no live call — just construction).
    # Live smoke test deferred to first actual turn to avoid per-restart cost.
    if not zai_api_key.startswith("sk-"):
        logger.warning("switchable_llm: ZAI_API_KEY doesn't start with 'sk-'; may be invalid")

# Initialize state variables
self._last_error_log_ts: float = 0.0
self._zai_disabled: bool = False
self._turn_in_flight: bool = False
self._turn_delegate: LLMService | None = None
self._started_delegates: set[str] = set()
self._delegate_setup: FrameProcessorSetup | None = None
self._saved_start_frame: FrameData | None = None
```

Validate at init time:
- If both keys missing -> raise (matches `src/pipeline.py:228-232` startup guard).
- If only openrouter key -> log INFO `"z.ai disabled (ZAI_API_KEY unset)"`; switching is a no-op. `[REV — Minor #2: U3 fixed]`
- If only zai key -> `_or_service` is None; set `_active_provider = "zai"` initially. Switching to openrouter is a no-op (stays zai with warning).

**Acceptance criteria:**
- `__init__` constructs at least one delegate as a **separate object** (not `self`).
- `_or_service` and `_zai_service` are independent Pipecat `LLMService` instances.
- Frame relay is installed on both delegates.
- U1, U2, U3 pass.

**Files:** `src/switchable_llm.py`.

#### Step 2 — Implement delegating surface with frame relay `[REV — Critical #1, #2, #3, #4: complete rewrite]`

Add these methods to `SwitchableLLMService`:

```python
# --- Frame relay (Critical #1 fix) ---

def _install_frame_relay(self, delegate):
    """Patch delegate's push_frame and broadcast_frame to relay through wrapper."""
    wrapper = self

    async def relayed_push_frame(frame, direction=FrameDirection.DOWNSTREAM):
        # Observe LLMFullResponseEndFrame to unlock sticky-turn gate
        if isinstance(frame, LLMFullResponseEndFrame):
            wrapper._turn_in_flight = False
            wrapper._turn_delegate = None
        await wrapper.push_frame(frame, direction)

    async def relayed_broadcast_frame(frame_cls, **kwargs):
        await wrapper.broadcast_frame(frame_cls, **kwargs)

    delegate.push_frame = relayed_push_frame
    delegate.broadcast_frame = relayed_broadcast_frame


# --- Provider routing ---

def _active_delegate(self) -> LLMService:
    """Return currently active delegate. Does NOT call _sync_provider."""
    if self._active_provider == "zai" and self._zai_service is not None:
        return self._zai_service
    if self._or_service is not None:
        return self._or_service
    return self._zai_service  # fallback: only zai available


# --- Error handling ---

def _is_provider_error(self, exc: Exception) -> bool:
    """Check if exception is a provider-level error vs a programming error."""
    from anthropic import AuthenticationError, APIStatusError, APIConnectionError
    import httpx
    
    return isinstance(exc, (
        AuthenticationError,
        APIStatusError,
        APIConnectionError,
        httpx.TimeoutException,
        httpx.ConnectError,
    ))

async def _handle_provider_failure(self, exc: Exception, frame: FrameData, direction: FrameDirection):
    """Handle provider-level errors with rate-limited logging and fallback."""
    import time
    
    now = time.time()
    # Rate-limit: log at most once per 60 seconds
    if now - self._last_error_log_ts > 60:
        logger.error(
            "switchable_llm: provider error on %s, falling back to openrouter: %s",
            self._active_provider, exc
        )
        self._last_error_log_ts = now
    
    # Permanently disable z.ai for this process
    self._zai_disabled = True
    self._active_provider = "openrouter"
    self._turn_in_flight = False
    self._turn_delegate = None
    
    # Push ErrorFrame upstream for indication
    await self.push_frame(ErrorFrame(error=str(exc)))
    
    # Forward to OpenRouter for retry
    if self._or_service is not None:
        await self._or_service.process_frame(frame, direction)


# --- Turn-gated process_frame (Critical #2 fix) ---

async def process_frame(self, frame, direction):
    # Let the wrapper handle StartFrame/CancelFrame/InterruptionFrame lifecycle
    await super().process_frame(frame, direction)

    # Turn-start: lock delegate, sync provider
    if isinstance(frame, (OpenAILLMContextFrame, LLMContextFrame, LLMMessagesFrame)):
        if not self._turn_in_flight:
            self._sync_provider()  # mtime-gated file read (Driver #3)
            self._turn_delegate = self._active_delegate()
            self._turn_in_flight = True
            await self._ensure_delegate_started(
                self._turn_delegate,
                "zai" if self._turn_delegate is self._zai_service else "or"
            )

    delegate = self._turn_delegate if self._turn_in_flight else self._active_delegate()

    try:
        # Forward to delegate's process_frame via its queue_frame
        # (bypasses the patched push_frame — we want the delegate to
        # PROCESS the frame, not just relay it)
        await delegate.process_frame(frame, direction)
    except Exception as e:
        if self._is_provider_error(e):
            await self._handle_provider_failure(e, frame, direction)
        else:
            raise


# --- Lifecycle (Critical #4 fix: active-only start, fan-out stop) ---

async def setup(self, setup_obj):
    """Receive pipeline setup; propagate to active delegate."""
    await super().setup(setup_obj)
    self._delegate_setup = setup_obj  # save for lazy start
    # Setup the active delegate immediately
    active = self._active_delegate()
    key = "zai" if active is self._zai_service else "or"
    await active.setup(setup_obj)
    self._started_delegates = set()

async def start(self, frame):
    """Start active delegate only. Inactive started lazily on first switch."""
    await super().start(frame)
    self._saved_start_frame = frame
    active = self._active_delegate()
    key = "zai" if active is self._zai_service else "or"
    # Invoke delegate's lifecycle start
    await active.start(frame)
    self._started_delegates.add(key)

async def stop(self, frame):
    for svc in (self._or_service, self._zai_service):
        if svc is not None:
            await svc.stop(frame)

async def cancel(self, frame):
    for svc in (self._or_service, self._zai_service):
        if svc is not None:
            await svc.cancel(frame)

async def cleanup(self):
    await super().cleanup()
    for svc in (self._or_service, self._zai_service):
        if svc is not None:
            await svc.cleanup()


# --- Function registration fan-out ---

def register_function(self, name, handler, *, cancel_on_interruption=True, **kw):
    for svc in (self._or_service, self._zai_service):
        if svc is not None:
            svc.register_function(name, handler,
                                  cancel_on_interruption=cancel_on_interruption, **kw)

def unregister_function(self, name):
    for svc in (self._or_service, self._zai_service):
        if svc is not None:
            svc.unregister_function(name)


# --- Lazy delegate start on switch ---

async def _ensure_delegate_started(self, delegate, key):
    if key not in self._started_delegates:
        await delegate.setup(self._delegate_setup)
        await delegate.start(self._saved_start_frame)
        self._started_delegates.add(key)
        logger.info("switchable_llm: lazily started %s delegate", key)
```

**Acceptance criteria:**
- After delegate emits N frames, wrapper's `push_frame` is observed N times by downstream spy (U9).
- `_sync_provider` is called only on turn-start frames, NOT per-frame (U4, Driver #3).
- `broadcast_frame` from `run_function_calls` (`llm_service.py:710, 854`) reaches downstream via wrapper relay (U9, E2).
- Only active delegate has `start()` called at boot; inactive delegate started lazily on first switch (verify via log spy on `_ensure_delegate_started`).
- U4, U5, U6, U7, U8, U9 pass.
- I1, I2, I3 pass.

**Files:** `src/switchable_llm.py`.

#### Step 3 — Pipeline wiring sanity check (`src/pipeline.py`)

Verify that `src/pipeline.py:394-401` (constructor call) still works unchanged. The `SwitchableLLMService.__init__` accepts the same kwargs.

Note: `SwitchableLLMService` now inherits `LLMService` (not `OpenAILLMService`). It is still a `FrameProcessor` subclass (via `LLMService -> AIService -> FrameProcessor`), so `Pipeline.link()` and `FrameProcessor.setup()` work as before.

Update the startup guard at `src/pipeline.py:228-232` if needed: keep "at least one provider key required" semantics.

Update the log line at `src/pipeline.py:521` to include `provider=<active>` so boot logs disambiguate which delegate served the first turn.

**Acceptance criteria:**
- `make build` / pipeline boot succeeds with both keys.
- Boot log includes `provider=openrouter` (default).
- E1 reaches the LLM stage.

**Files:** `src/pipeline.py`.

#### Step 4 — Wire observability + indication

- Add an `IndicationKind.LLM_FALLBACK` (or reuse `STT_ERROR` pragmatically) so the user gets a sound/visual cue when fallback fires.
- Tag per-turn metrics via `set_core_metrics_data(MetricsData(processor=..., model=f"{provider}:{model}"))` before each turn delegation, so dashboards can split by provider.
- Surface `swit.active_provider` in `src/watch.py`'s status panel.

**Acceptance criteria:**
- O1, O2, O3, O4 pass.
- Watch dashboard shows current provider with no per-tick file I/O.

**Files:** `src/switchable_llm.py`, possibly `src/watch.py` (verification only), possibly `src/indication.py` (new kind).

#### Step 5 — Tests + docs

- Create `tests/test_switchable_llm.py` (U1-U9).
- Extend `tests/test_llm_tools.py` (I1-I3).
- Create `tests/integration/test_zai_e2e.py` (E1-E3, gated by `ZAI_API_KEY`).
- Create `tests/test_switchable_llm_observability.py` (O1-O4).
- Update `heare.env.example` if not already done by v1.
- Update the docstring on `SwitchableLLMService` to reflect that delegation now works (delete lines 21-26's apology).

**Acceptance criteria:**
- `make test` green; new test files all pass locally; integration suite skipped cleanly without `ZAI_API_KEY`.
- `ruff check` clean.
- Class docstring no longer claims z.ai is "configured but unused."

**Files:** `tests/test_switchable_llm.py`, `tests/test_llm_tools.py`, `tests/integration/test_zai_e2e.py`, `tests/test_switchable_llm_observability.py`, `heare.env.example`, `src/switchable_llm.py` (docstring).

#### Step 6 — Manual QA + commit

- `make build && make watch`. Speak a short utterance — verify openrouter handles it (default).
- `heare provider zai` (or dashboard `p` hotkey). Speak again — verify z.ai handles it (Anthropic-shape request visible in `.omc/logs/`).
- Toggle back. Verify openrouter takes the next turn.
- Trigger a tool call (e.g. "list my downloads") on each provider — verify `bash` handler runs and result is summarized.
- Commit on a dedicated branch with the title `feat: full z.ai Anthropic delegation in SwitchableLLMService`.

### 4.4 Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Pipecat `FrameProcessor` internals break with patched `push_frame` | M | H | `push_frame` is a public async method called from many places inside delegates. Patching it replaces the entry point cleanly. Internal calls to `__internal_push_frame` (name-mangled, unreachable from subclasses) go through `push_frame`. Verify with U9 + E1 + E2. If breakage occurs, fallback: set `delegate._next = wrapper._next` directly instead of patching. |
| z.ai's Anthropic endpoint diverges from anthropic-py expectations (e.g. tool block names) | M | H | E2 covers the tool roundtrip end-to-end; boot smoke test validates API key shape at init. |
| Mid-turn flip race (pre-mortem #2) | L | H | Sticky-turn gate keyed on `LLMContextFrame`/`LLMMessagesFrame` (incoming) and unlocked on `LLMFullResponseEndFrame` (relayed). U6 + E3 verify. |
| Auth/5xx log spam (pre-mortem #3) | M | M | Rate-limited error log + permanent fallback. U7 verifies. |
| Tests gated on `ZAI_API_KEY` won't run in CI | H | L | Mark `@pytest.mark.integration`; unit and observability tests stay deterministic. |
| Lazy start races with first turn | L | M | `_ensure_delegate_started` is called synchronously inside `process_frame` before forwarding the turn-start frame to the delegate. The `await delegate.queue_frame(start_frame)` completes before the context frame is forwarded. |
| `setup()` propagation double-creates input tasks | L | M | `FrameProcessor.__create_input_task` (`frame_processor.py:959-966`) is guarded by `if not self.__input_frame_task`. Second call is a no-op. Safe to call setup on delegates even if they were already setup. |

### 4.5 Verification Steps

1. **Lint:** `ruff check src/switchable_llm.py tests/test_switchable_llm.py` returns 0.
2. **Unit + observability:** `pytest tests/test_switchable_llm.py tests/test_switchable_llm_observability.py -q` passes. **U9 specifically verifies downstream frame visibility.**
3. **Integration tooling:** `pytest tests/test_llm_tools.py -q` passes (both new and pre-existing tests).
4. **E2E (optional, gated):** `ZAI_API_KEY=... pytest tests/integration/test_zai_e2e.py -q -m integration` passes; without the env var, suite is skipped with a clear reason.
5. **Manual:** Step 6 above.
6. **Reviewer:** Hand off to `omc-reference:code-reviewer` for diff review and `omc-reference:verifier` for evidence collection.

---

## 5. ADR — Architecture Decision Record `[REV — Minor #3: added consequences]`

**Decision.** Reimplement `SwitchableLLMService` as a delegating wrapper that inherits `pipecat.services.llm_service.LLMService` and holds two pre-constructed sub-services (`OpenAILLMService` for OpenRouter, `AnthropicLLMService` for z.ai). Delegate-emitted frames are relayed through the wrapper via patched `push_frame`/`broadcast_frame` methods. Provider switching uses a sticky-turn gate keyed on incoming `LLMContextFrame`/`LLMMessagesFrame` (turn start) with unlock on relayed `LLMFullResponseEndFrame`. Lifecycle uses active-only `start()` with lazy start on first switch. Function registration fans out to both delegates.

**Drivers.** (1) Frame-shape compatibility with downstream pipeline stages; (2) tool/function-call parity across providers; (3) hot-swap latency bounded by mtime-gated provider sync called only on turn-start; (4) identity propagation — delegates are not linked into the pipeline, requiring frame relay through the wrapper.

**Alternatives considered.**
- **Option B — subclass `OpenAILLMService` and intercept Anthropic-bound frames internally.** Rejected because it duplicates `AnthropicLLMService`'s streaming/tool-block decoding inside our class and re-introduces the v1 bug class.
- **Option C — fork/merge router via `ParallelPipeline`.** Rejected because `ParallelPipeline`'s AND semantics are wrong for our XOR routing requirement; building a custom XOR router exceeds the cost of Option A.

**Why chosen.** Option A is the only option that satisfies all five principles and four drivers simultaneously. Composition-over-inheritance keeps each delegate provider-shaped (Principle #4), preserves frame contracts by construction (Driver #1), isolates the provider switch to a single `if`-branch in `_active_delegate()` (Principle #2 / #3), and the frame-relay patch solves identity propagation (Driver #4) without modifying Pipecat internals.

**Consequences.**
- Positive: provider-shaped fidelity; future providers plug in by adding a third delegate; tests can mock either delegate independently; mid-turn safety via sticky-turn gate; frame relay ensures downstream visibility.
- Negative: two LLM service objects in memory (small RAM cost ~tens of MB); monkey-patching `push_frame`/`broadcast_frame` is non-standard — any Pipecat version that changes these method signatures would break the relay; `[REV — Minor #3: added]` event handler routing is indirect (delegates' internal events relay through wrapper's push_frame hooks rather than explicit event forwarding); `[REV — Minor #3: added]` identity propagation complexity adds ~30 lines of patching code with implicit coupling to FrameProcessor internals.
- Neutral: removes the apologetic class docstring at `src/switchable_llm.py:21-26`; replaces with operational documentation.

**Follow-ups.**
1. Add a third provider (Anthropic native, Vertex, or Bedrock) following the same delegate pattern.
2. Per-provider model selection at runtime (e.g. `~/.heare/provider` containing `zai:claude-3-5-haiku`).
3. Cost/latency dashboard panel splitting metrics by provider (depends on O3 metric tagging).
4. Replace the rate-limited fallback log with structured telemetry once the `indication` subsystem grows a dedicated `LLM_FALLBACK` kind.
5. Consider promoting the sticky-turn gate + frame relay utility into a Pipecat-level mixin if other multi-provider services emerge.
6. `[REV]` Evaluate replacing the `push_frame` monkey-patch with `delegate._next = wrapper` link manipulation if Pipecat's FrameProcessor API stabilizes.

---

## 6. Open Questions `[REV — resolved #3, #5; remaining items updated]`

Tracked in `.omc/plans/open-questions.md`:

- [ ] Do we need a new `IndicationKind.LLM_FALLBACK`, or is overloading `STT_ERROR` acceptable for the user-visible cue? — Affects Step 4 + O4.
- [x] `[RESOLVED — Major #4]` Does pipecat's `LLMService` base require us to implement `_call_event_handler` or `add_event_handler` explicitly? — **No.** Event handlers on the wrapper fire via inherited `FrameProcessor._call_event_handler`. Delegate-internal events relay through the wrapper's `push_frame` which triggers `on_before_push_frame` / `on_after_push_frame`. External consumers attach to the wrapper, not delegates.
- [ ] Should the boot smoke test actually call z.ai with a live `messages.create`? — **Decided: No.** Per Major #3, validate API key shape locally at boot (zero cost). Live validation deferred to first actual turn.
- [x] `[RESOLVED — Major #5]` Should `_sync_provider` be called per-frame or per-turn? — **Per-turn only.** Called on `LLMContextFrame`/`LLMMessagesFrame` when `_turn_in_flight` is False.
- [ ] Should the `push_frame` relay use monkey-patching (current plan) or direct `_next`/`_prev` link manipulation? — Monkey-patching is more explicit but couples to method signatures. Link manipulation is simpler but changes delegate internal state. Defer decision to executor based on test results.

---

## 7. Revision Tracking — Architect/Critic Findings

### Critical Findings (5) — all addressed

| # | Finding | Fix location in plan |
|---|---|---|
| C1 | `await delegate.process_frame()` calls `delegate.push_frame()` which uses `delegate._next` (None). Frames never reach downstream. | 4.2.1 Frame relay mechanism; Step 2 `_install_frame_relay`; U9 |
| C2 | Sticky-turn gate keyed on wrong frames (`LLMFullResponseStartFrame` is emitted, not received). | 4.2.2 Sticky-turn gate; Pre-mortem #2; U6 |
| C3 | `broadcast_frame` missing from delegated surface. | 4.2.1 (patched alongside `push_frame`); 4.2.3 table |
| C4 | Lifecycle fan-out double-creates tasks. | 4.2.4 Lazy start; 4.2.3 table (start = active-only) |
| C5 | No test for downstream frame visibility. | U9 (unit); E2 (e2e with downstream spy assertion) |

### Major Findings (6) — all addressed

| # | Finding | Fix location |
|---|---|---|
| M1 | Pre-mortem #2 mitigation is non-functional (wrong frame). | Pre-mortem #2 rewritten with correct trigger |
| M2 | Tool-schema conflation. | Driver #2 clarification; 4.2.3 table note |
| M3 | Boot smoke test too weak. | Step 1 `zai_api_key.startswith("sk-")` check |
| M4 | Open Question #3 is a blocker. | Resolved inline in 4.2.3 event handler row; OQ #3 marked resolved |
| M5 | `_sync_provider` consulted per-frame. | Driver #3 reworded; `process_frame` only calls on turn-start; OQ #5 resolved |
| M6 | Identity propagation missing from Drivers. | Driver #4 added |

### Minor Findings (5) — all addressed

| # | Finding | Fix location |
|---|---|---|
| m1 | Clarify `_or_service = self` deletion. | Step 1 explicitly shows delete + separate construction |
| m2 | U3 test contradiction ("raises" vs "succeeds"). | U3 reworded: "init succeeds (does not raise)" |
| m3 | ADR missing event handler routing + identity propagation consequences. | ADR Consequences section expanded |

---

## 8. Plan Summary

**Plan saved to:** `.omc/plans/zai-anthropic-full-support.md`

**Scope:**
- 3 production files modified (`src/switchable_llm.py`, `src/pipeline.py`, possibly `src/indication.py`).
- 4 test files added/extended.
- 0 new pip deps.
- Estimated complexity: MEDIUM-HIGH.

**Key Deliverables:**
1. `SwitchableLLMService` rebased on `LLMService` with frame-relay delegation.
2. Sticky-turn gate on correct trigger frames + rate-limited fallback + indication cue.
3. Full unit (U1-U9) / integration (I1-I3) / e2e (E1-E3) / observability (O1-O4) coverage.
4. ADR with all four drivers and complete consequences.

**Does this plan capture your intent?**
- "proceed" — Hand off to `/oh-my-claudecode:start-work zai-anthropic-full-support`.
- "adjust [X]" — Return to interview to modify specific sections.
- "restart" — Discard and start fresh.
