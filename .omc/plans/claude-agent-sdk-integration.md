# claude-agent-sdk integration (drop-in for ClaudeCLI)

Status: DRAFT — awaiting user approval before handoff to `/oh-my-claudecode:start-work`.

## Context

Today every decider tick and every action call spawns a fresh `claude -p ...` subprocess via `asyncio.create_subprocess_exec` in `src/claude_cli.py`. Each spawn pays ~500–800ms for Node.js boot, CLI module load, and session resume even though we always pass `--resume SESSION_ID`. The official `claude-agent-sdk` Python package exposes `ClaudeSDKClient`, a persistent context-manager client that keeps a single Node process alive for the life of the daemon — eliminating the per-call startup tax.

The integration must be a **drop-in**: `DeciderProcessor` and `ensure_identity` call `claude_cli.call_decider(prompt)`, `claude_cli.call_action(description, on_line=...)`, `claude_cli.bootstrap_identity(prompt)`, and `claude_cli.version()`. These interfaces do not change. Behind them, a feature flag `Settings.use_agent_sdk` picks between the current `ClaudeCLI` subprocess path (default, unchanged) and a new `AgentSDKCLI` path that reuses a single `ClaudeSDKClient` session.

The decider still needs the same JSON/short-key/markdown-fence parsing it has today, and `call_action` still has to stream stdout lines through the `on_line` callback so the realtime dashboard (see `DeciderProcessor._execute_pending` → `_stdout_emit` → `EventKind.ACTION_STDOUT`) keeps showing per-line motion. Session persistence, stale-session recovery, rate limiting, retry/backoff, timeout, and per-call logging to `settings.log_dir` all need equivalents on the SDK path.

Concrete anchors in the codebase that drive this plan:
- `src/claude_cli.py:27` — `ClaudeCLI` class + all its public methods used by callers
- `src/claude_cli.py:216` — `call_decider` JSON parse → `_extract_decision` → `_normalize_decision_keys`
- `src/claude_cli.py:236` — `_DECISION_KEY_MAP` / `_DECISION_TYPE_MAP` (short-key schema)
- `src/claude_cli.py:259` — `_strip_markdown_fence` (fenced JSON unwrap)
- `src/claude_cli.py:289` — `call_action(description, on_line=...)`
- `src/claude_cli.py:298` — `bootstrap_identity`
- `src/claude_cli.py:94` — `_read_streams`: per-line drain with `\r` splitting and `on_line` callback — the dashboard streaming contract lives here
- `src/main.py:101` — `claude_cli = ClaudeCLI(settings)` — single instantiation site; the daemon owns its lifecycle
- `src/decider.py:589,810,863` — decider + heartbeat callers
- `src/identity.py:64` — bootstrap caller
- `src/config.py:48-52` — current `claude_*` settings
- `pyproject.toml` — dependency list (no `claude-agent-sdk` yet)
- `tests/test_claude_cli.py` — existing subprocess-focused coverage to preserve

## Work Objectives

1. Add the `claude-agent-sdk` Python package as a dependency and introduce a `use_agent_sdk` feature flag in `Settings` (default `False`).
2. Extract the caller-facing surface of `ClaudeCLI` into a tiny `ClaudeBackend` Protocol so `DeciderProcessor`, `ensure_identity`, and `main.py` can hold either backend without type gymnastics.
3. Implement `AgentSDKCLI`, a persistent-session backend that wraps `ClaudeSDKClient` and exposes the same `call_decider` / `call_action` / `bootstrap_identity` / `version` / `persona` surface.
4. Map SDK output to the existing `on_line` streaming contract so the dashboard (`ACTION_STDOUT` events) keeps ticking during long actions.
5. Preserve decider response parsing verbatim by reusing `ClaudeCLI._extract_decision`, `ClaudeCLI._normalize_decision_keys`, `ClaudeCLI._strip_markdown_fence` (lift them to module-level helpers in a new `src/claude_backend_common.py` so both backends call the same code).
6. Wire the factory in `src/main.py` so `claude_cli` is whichever backend the flag selects, lifecycle-managed (`aenter` / `aclose`) via the existing `try/finally` block.
7. Add unit tests for the SDK path that mock `ClaudeSDKClient` (no real daemon) and cover the same matrix the subprocess tests do: decider parse, short-key normalization, fence stripping, `on_line` streaming, retry, timeout, stale-session recovery, rate limiting. Keep the existing subprocess tests green for the `use_agent_sdk=False` default.
8. Document rollout so we can flip the flag locally, validate latency, and then promote to default in a follow-up commit.

## Guardrails

### Must have
- `DeciderProcessor`, `ensure_identity`, `main.py`, and all callers continue calling the same method names with the same signatures and return shapes. No import changes beyond `main.py`'s factory call.
- `use_agent_sdk` defaults to `False`. A user on `main` with no config change sees byte-identical behavior.
- Decider still returns `{"type": "nothing"|"speak"|"act", ...}` with long keys after normalization.
- `call_action` still returns `{"summary": "<joined stdout text>"}` and still invokes `on_line` per emitted line so `EventKind.ACTION_STDOUT` events keep flowing.
- Persistent session lifecycle: `aenter` on daemon init (after `load_settings`), `aclose` in the `finally` block in `main.py` next to `store.close()` / pid file cleanup.
- Rate limiting preserved on both backends (share the existing `RateLimiter`).
- Retry/backoff semantics preserved (`settings.claude_max_retries`, exponential backoff).
- Timeout preserved (`settings.claude_timeout_seconds`): wrap SDK interactions in `asyncio.wait_for` the same way `_run` does.
- Stale-session recovery: if the SDK raises a `no conversation found`-equivalent error, clear the session_id, reconnect, and retry once, mirroring current `_run` behavior.
- Per-invocation debug log files under `settings.log_dir` with the same `rc=...\n--- prompt ---\n...\n--- stdout ---\n...\n--- stderr ---\n...\n` layout so `tests/logs` diffing and manual forensics keep working.
- Actions allow `Bash` and the computer tools; decider passes `allowed_tools=[]` (text-only) to prevent accidental tool use on decider ticks.
- Rollout-safe: every SDK code path is gated on `use_agent_sdk`. Removing the flag is a separate follow-up, not this work.

### Must NOT have
- No rewrite of `DeciderProcessor`, `ContextBuilder`, `HeartbeatTask`, `storage.TranscriptStore`, or `watch.py`. Changes land inside `claude_cli` plus two small factory edits.
- No change to the decider prompt template, persona template, or short-key schema.
- No new public config beyond the `use_agent_sdk` flag (plus one optional `claude_sdk_cli_path` escape hatch, default `None`, reusing `claude_cli`).
- No direct `ClaudeSDKClient` imports in `decider.py`, `identity.py`, `pipeline.py`, or `watch.py`. Only `claude_cli.py` (renamed namespace) and `main.py` touch the SDK.
- No blocking I/O on the decider critical path: if the SDK's streaming iterator stalls, the existing `asyncio.wait_for` timeout and `_safe_emit`/drainer pattern must remain uncompromised.
- No deletion of `ClaudeCLI` in this plan. Both backends coexist until we promote the SDK path.
- No behavior change for users who do not set `use_agent_sdk = true`. The subprocess path stays byte-identical.

## Task Flow

```
Story 1: deps + flag            →  add claude-agent-sdk to pyproject, wire Settings.use_agent_sdk
Story 2: extract common parsing →  lift decider parsing helpers into src/claude_backend_common.py + Protocol
Story 3: AgentSDKCLI backend    →  new class wrapping ClaudeSDKClient, same surface
Story 4: factory in main.py     →  pick backend from settings, manage lifecycle in try/finally
Story 5: SDK unit tests         →  mock ClaudeSDKClient, cover decider parse / streaming / retry / timeout
Story 6: docs + rollout notes   →  README snippet, open-questions entries, config example
```

Stories 1–2 are prerequisites. Story 3 depends on 1+2. Story 4 depends on 3. Stories 5 and 6 run in parallel after Story 4.

## Detailed TODOs

### Story 1 — Dependency and feature flag

**Files touched**
- `pyproject.toml`
- `src/config.py`

**Changes**
- `pyproject.toml`: add `"claude-agent-sdk>=0.1.0"` to `[project].dependencies`. Leave existing deps intact.
- `src/config.py`:
  - Add `use_agent_sdk: bool = False` to `Settings` (place near the `claude_*` group around line 52).
  - Add optional `claude_sdk_cli_path: str | None = None` (falls back to `claude_cli` when `None`; exposes SDK's `cli_path` option for users on non-default installs).
  - No TOML / env override logic changes needed — `load_settings()` already walks `Settings` fields and copies TOML keys verbatim.

**Acceptance criteria**
- `uv sync` (or `pip install -e .`) resolves `claude-agent-sdk` without breaking existing deps.
- `pytest tests/test_claude_cli.py` still green (no behavior change — flag defaults to `False`).
- `from src.config import Settings; Settings().use_agent_sdk is False`.
- `~/.heare/config.toml` with `use_agent_sdk = true` loads correctly.

---

### Story 2 — Shared Protocol and extracted parsing helpers

**Files touched**
- New: `src/claude_backend_common.py`
- `src/claude_cli.py` (re-export extracted helpers, shrink class body by ~40 lines)
- `src/decider.py` (TYPE_CHECKING import update only — no runtime change)
- `src/identity.py` (TYPE_CHECKING import update only)

**Changes**
- New `src/claude_backend_common.py`:
  - Module-level constants: `DECISION_KEY_MAP`, `DECISION_TYPE_MAP` (copy-verbatim from `ClaudeCLI._DECISION_KEY_MAP` / `_DECISION_TYPE_MAP`).
  - Functions:
    - `strip_markdown_fence(text: str) -> str` — verbatim from `ClaudeCLI._strip_markdown_fence`.
    - `extract_decision(payload: Any) -> Any` — verbatim from `ClaudeCLI._extract_decision`, but call `strip_markdown_fence` from the module scope.
    - `normalize_decision_keys(decision: dict) -> dict` — verbatim from `ClaudeCLI._normalize_decision_keys`.
    - `parse_decider_response(raw: str) -> dict[str, Any]` — composes `json.loads` + `extract_decision` + `normalize_decision_keys` + the "missing type → nothing" guard. Both backends call this single function so the parse path is one implementation.
  - `ClaudeBackend` typing.Protocol:
    ```python
    class ClaudeBackend(Protocol):
        persona: str | None
        async def __aenter__(self) -> ClaudeBackend: ...
        async def __aexit__(self, *exc: Any) -> None: ...
        async def version(self) -> str: ...
        async def call_decider(self, prompt: str) -> dict[str, Any]: ...
        async def call_action(
            self,
            description: str,
            *,
            on_line: Callable[[str], None] | None = None,
        ) -> dict[str, Any]: ...
        async def bootstrap_identity(self, prompt: str) -> dict[str, Any]: ...
    ```
  - `__aenter__`/`__aexit__` are new on both backends. `ClaudeCLI` gets no-op stubs (`__aenter__` returns `self`, `__aexit__` does nothing). `AgentSDKCLI` opens/closes the persistent `ClaudeSDKClient`. This lets `main.py` use a uniform `async with _backend as claude_cli:` without `isinstance` checks.
  - `compact_if_needed` is intentionally excluded from the Protocol — see Story 4 for rationale.
- `src/claude_cli.py`:
  - Replace `_DECISION_KEY_MAP`, `_DECISION_TYPE_MAP`, `_strip_markdown_fence`, `_extract_decision`, `_normalize_decision_keys` with thin classmethods/staticmethods that delegate to `claude_backend_common`. Keep the old method names as aliases so `tests/test_claude_cli.py`'s direct calls (`ClaudeCLI._strip_markdown_fence`, `ClaudeCLI._normalize_decision_keys`) keep working without test churn.
  - `call_decider` body shrinks to `parse_decider_response(raw)`.
  - Add `async def aclose(self) -> None: return` so it satisfies `ClaudeBackend`.
- `decider.py` / `identity.py`: switch `TYPE_CHECKING` import from `from .claude_cli import ClaudeCLI` to `from .claude_backend_common import ClaudeBackend`; use `ClaudeBackend` in the annotation. Runtime behavior unchanged.

**Acceptance criteria**
- `pytest tests/test_claude_cli.py` stays green without edits.
- `ClaudeCLI._strip_markdown_fence` and `ClaudeCLI._normalize_decision_keys` still exist (tests call them directly at `tests/test_claude_cli.py:208-276`).
- `python -c "from src.claude_backend_common import parse_decider_response; print(parse_decider_response('{\"t\":\"s\",\"r\":\"hi\"}'))"` prints `{'type': 'speak', 'reply': 'hi'}`.
- `mypy src/` (if enabled in CI) reports no new errors.

---

### Story 3 — AgentSDKCLI backend

**Files touched**
- New: `src/agent_sdk_cli.py`
- `src/claude_cli.py` (add a module-level re-export for `ClaudeBackend` ergonomics only; no behavior change)

**Design**

`AgentSDKCLI` keeps the same constructor shape as `ClaudeCLI` so the factory in `main.py` can swap them 1:1.

```python
class AgentSDKCLI:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cwd = settings.workspace_dir
        self.session_file = settings.session_file
        self.timeout = settings.claude_timeout_seconds
        self.max_retries = settings.claude_max_retries
        self.persona: str | None = None
        self._session_id: str | None = None
        self._rate_limiter = RateLimiter(
            max_calls=settings.claude_max_calls_per_minute,
            window_seconds=60.0,
        )
        self._client: ClaudeSDKClient | None = None
        self._client_lock = asyncio.Lock()  # serialize reconnects only
```

Note: `_call_lock` is intentionally absent. The `DeciderProcessor` FSM at `decider.py:506` (`async with self._lock`) already serializes all `call_decider` and `call_action` calls — they are structurally mutually exclusive. The heartbeat path calls `call_decider` only when `state == DeciderState.LISTENING`, which cannot overlap with an EXECUTING action. No concurrent SDK calls are possible; adding `_call_lock` would add latency for zero safety benefit.

**Lifecycle**

`AgentSDKCLI` implements `__aenter__` and `__aexit__` as a proper async context manager (consistent with `ClaudeSDKClient`). `ClaudeCLI` gets stub `__aenter__`/`__aexit__` (no-ops) so `main.py` can use a uniform `async with claude_cli:` for both backends.

- `async def __aenter__(self) -> AgentSDKCLI`: create `ClaudeSDKClient(ClaudeAgentOptions(...))`, call its `__aenter__`, store on `self._client`. Return `self`.
- `async def __aexit__(self, *exc) -> None`: if `self._client` is not None, call its `__aexit__(*exc)` and set to None. Safe to call multiple times (idempotent).
- `async def _ensure_client(self, *, allowed_tools, append_system_prompt, model) -> ClaudeSDKClient`:
  - Under `self._client_lock`, if `self._client is None`, build `ClaudeAgentOptions(allowed_tools=allowed_tools, append_system_prompt=append_system_prompt, model=model, resume=self._session_id, cwd=str(self.cwd), cli_path=self.settings.claude_sdk_cli_path or self.settings.claude_cli)` and open the client. Return it.
  - Note on options drift: `ClaudeAgentOptions` is constructor-level; if the decider and action calls need different `allowed_tools`, we tear down and reopen the client when `allowed_tools` changes. This is the narrow reconnect path and only fires on the first mixed call; both paths stay persistent within their own regime.
  - Simpler alternative (preferred): open a single client with `allowed_tools=["Bash", "Computer"]` and pass a *prompt-level* restriction for the decider ("You have no tools. Output JSON only."). Decider already works this way today because the subprocess call doesn't restrict tools either — the prompt template is what keeps the model in JSON-only mode. This removes the reconnect path entirely.
  - Go with the single-client approach. Document the prompt-level contract in the class docstring.

**Session persistence**

- On startup, `_load_session_id_from_file()` reads `session_file` the same way `ClaudeCLI.ensure_session` does and stores it for the first `resume=` option.
- After each successful call, the SDK's final `SDKResultMessage` carries a `session_id` (the SDK exposes this via the result message's `session_id` field). Persist it the same way `_persist_session` does today so a restart resumes cleanly.
- If the SDK raises a "no conversation found" error (match on exception message substring, case-insensitive) under `_call_lock`: clear `self._session_id`, call `aclose`, reopen the client with `resume=None`, retry once. If retry also fails, propagate.

**Call flow**

`async def _run_query(self, prompt, *, json_output, model, on_line)`:
1. `await self._rate_limiter.acquire()`
2. `for attempt in range(1, self.max_retries + 1):`
3. Under `self._call_lock`, ensure client is up.
4. `await self._client.query(prompt)` (sends the user turn).
5. Iterate `async for message in self._client.receive_response()` inside `asyncio.wait_for(..., timeout=self.timeout)`. On `asyncio.TimeoutError`: call `await iterator.aclose()` (or `await self._client.__aexit__(None, None, None)` if the iterator does not expose `aclose`) before re-raising as `ClaudeCLIError("claude-agent-sdk timed out after Xs")`. This prevents leaking the persistent Node.js process.
6. For each `AssistantMessage`, walk `message.content` blocks:
   - For each `TextBlock`, split on `\n` and `\r` (mirroring `_read_streams`). For every non-empty segment, append to `stdout_chunks` and if `on_line is not None`, call it under a try/except that logs but doesn't raise (matching the subprocess callback semantics — see `test_run_on_line_callback_exception_does_not_break_stream` for the contract).
   - Tool-use / tool-result blocks are not streamed to `on_line` but are appended to an internal `tool_events` list for debug logging.
7. On `SDKResultMessage`, capture its `session_id`, its final text (if any), and loop-exit.
8. Assemble `stdout = "\n".join(stdout_chunks).strip()`. If `json_output=True`, attempt to parse the full joined text as the "final JSON payload" (decider path). Otherwise return as-is (action path).
9. `_log_invocation` to `settings.log_dir` with the same layout as today so forensics diffs stay simple.
10. On any exception inside the loop: catch, classify, retry with exponential backoff (`2 ** attempt`), same as `ClaudeCLI._run`. Classify "no conversation found" specially for stale-session recovery.

**call_decider**: calls `_run_query(prompt, json_output=True, model=settings.claude_decider_model, on_line=None)` and feeds `raw` through `parse_decider_response` from `claude_backend_common`.

**call_action**: calls `_run_query(description, json_output=False, model=None, on_line=on_line)` and returns `{"summary": raw}`.

**bootstrap_identity**: calls `_run_query(prompt, json_output=True, model=settings.claude_decider_model, on_line=None)`, then applies `extract_decision(json.loads(raw))` — NOT `parse_decider_response`. Reason: `parse_decider_response` runs `normalize_decision_keys` which would corrupt identity payloads whose keys overlap with the short-key schema (`"r"`, `"t"`, etc.). Mirror `ClaudeCLI.bootstrap_identity:298-312` exactly: if `result` is a string, `json.loads` it; if already a dict, return it directly.

**version**: returns `"claude-agent-sdk/<sdk_version>"` by reading `importlib.metadata.version("claude-agent-sdk")`. This is cosmetic — only used for `main.py`'s info log.

**Allowed tools**

- Options opened with `allowed_tools=["Bash"]` plus whatever Computer-tool name the SDK exposes (the SDK's README enumerates this; verify during implementation — may be `"ComputerUse"` or similar; if in doubt fall back to wildcard or add an explicit `SETTINGS.agent_sdk_allowed_tools: list[str]` with a sane default).
- Document in the class docstring that the decider prompt template is responsible for keeping decider ticks text-only.

**Acceptance criteria**
- `AgentSDKCLI` implements every method on `ClaudeBackend` with the same signatures as `ClaudeCLI`.
- `claude_cli.call_decider` and `claude_cli.call_action` work when the SDK's `ClaudeSDKClient` is mocked in tests (see Story 5 for the mock shape).
- Startup reads `~/.heare/session.json` and passes `resume=<sid>` to `ClaudeAgentOptions`.
- Successful calls update `session.json` with the latest session id returned by the SDK.
- `on_line` fires per non-empty segment during `call_action`, and raised exceptions in `on_line` do not break the stream.
- Timeout on a stalled `receive_response` raises `ClaudeCLIError("claude-agent-sdk timed out after 60s")` (mirror current text for log-greppability) and attempts up to `max_retries`.
- Stale-session error on first attempt clears the session id, reconnects, and retries once.
- `_log_invocation` writes `claude-<ms>.log` files with the same format as the subprocess backend.

---

### Story 4 — Factory in main.py + lifecycle

**Files touched**
- `src/main.py`

**Changes**

Both backends implement `__aenter__`/`__aexit__` (story 3 adds them to `AgentSDKCLI`; `ClaudeCLI` gets no-op stubs). The factory uses a uniform `async with`:

```python
if settings.use_agent_sdk:
    from .agent_sdk_cli import AgentSDKCLI
    _backend: ClaudeBackend = AgentSDKCLI(settings)
else:
    _backend = ClaudeCLI(settings)

async with _backend as claude_cli:
    # rest of run_until_stopped body
    ...
```

The `async with` must wrap the `run_until_stopped` inner body — specifically the block that starts at approximately `main.py:141` (`await run_until_stopped(...)`) — NOT the outer `_cmd_start` finally block. The SIGTERM teardown path runs through `run_until_stopped`'s own `finally` block (`main.py:173-205`). The `__aexit__` call will fire there naturally when the `async with` exits, whether via normal return, SIGTERM, or exception. This guarantees the persistent Node.js process is closed on every exit path.

Do not add a standalone `await claude_cli.aclose()` call anywhere — the `async with` exit handles it.

- Annotate `claude_cli` as `ClaudeBackend` (TYPE_CHECKING import of `ClaudeBackend` from `claude_backend_common`).
- `identity.py` and `decider.py` already accept anything that quacks like the protocol — they just need the annotation switch from Story 2.

**`compact_if_needed` — explicit out-of-scope decision**

`ClaudeCLI.compact_if_needed` runs `claude --compact` via subprocess when context-limit text appears in stderr. The SDK path has no stderr to monitor. This method is **explicitly out of scope for this plan**:
- It is not included in the `ClaudeBackend` Protocol.
- `AgentSDKCLI` does not implement it.
- If a context-limit error surfaces on the SDK path, it will be caught by the retry loop and logged; no compaction will occur.
- This is a known limitation of the SDK path, tracked as open question #7.

**Acceptance criteria**
- `use_agent_sdk = false` → daemon boots byte-identical to today (log line "claude CLI version: ..." shows `claude -p`'s version).
- `use_agent_sdk = true` → daemon boots, log shows `claude CLI version: claude-agent-sdk/<version>`, and no `claude -p` subprocess is spawned during normal operation.
- `Ctrl-C` / SIGTERM path closes the SDK session — confirmed by `ps` showing no orphaned Node process after shutdown.
- The `async with _backend` pattern compiles and runs without `isinstance` checks for both backends.

---

### Story 5 — Unit tests for AgentSDKCLI

**Files touched**
- New: `tests/test_agent_sdk_cli.py`

**Strategy**

Do not pull in the real SDK. Build a fake `ClaudeSDKClient` that is an async context manager whose `query(prompt)` records the prompt and whose `receive_response()` yields a scripted list of messages. Inject it by patching `src.agent_sdk_cli.ClaudeSDKClient` with a factory.

Shared fixtures:
```python
@pytest.fixture
def tmp_settings(tmp_path):
    s = Settings(
        workspace_dir=tmp_path / "workspace",
        session_file=tmp_path / "session.json",
        log_dir=tmp_path / "logs",
        claude_cli="claude",
        claude_timeout_seconds=10,
        claude_max_retries=3,
        claude_max_calls_per_minute=60,
        use_agent_sdk=True,
    )
    (tmp_path / "workspace").mkdir()
    (tmp_path / "logs").mkdir()
    return s


class FakeSDKMessage: ...
class FakeTextBlock:
    def __init__(self, text): self.text = text
class FakeAssistantMessage:
    def __init__(self, text_blocks): self.content = text_blocks
class FakeResultMessage:
    def __init__(self, session_id, text=""):
        self.session_id = session_id
        self.text = text


class FakeClient:
    def __init__(self, messages):
        self._messages = messages
        self.queries: list[str] = []
        self.opened = False
        self.closed = False
    async def __aenter__(self):
        self.opened = True
        return self
    async def __aexit__(self, *a):
        self.closed = True
    async def query(self, prompt):
        self.queries.append(prompt)
    async def receive_response(self):
        for m in self._messages:
            yield m
```

**Test matrix**

Parse / decider:
- `test_sdk_call_decider_valid_json`: fake yields one assistant text block `{"type":"speak","reply":"hi"}` → returns `{"type":"speak","reply":"hi"}`.
- `test_sdk_call_decider_strips_markdown_fence`: text block wraps JSON in ` ```json ... ``` ` → still parses. Proves the parse path is the shared helper.
- `test_sdk_call_decider_normalizes_short_keys_act`: short-key JSON → long-key dict.
- `test_sdk_call_decider_malformed_returns_nothing`: non-JSON text → `{"type":"nothing", ...}`.
- `test_sdk_call_decider_missing_type_key`: dict without `type` → `{"type":"nothing", ...}`.

Streaming:
- `test_sdk_call_action_streams_each_text_block`: three assistant messages each with one text block → `on_line` fires three times in order, `summary` is newline-joined.
- `test_sdk_call_action_splits_on_newlines`: a single text block `"a\nb\nc"` → `on_line` fires three times.
- `test_sdk_call_action_on_line_exception_does_not_break_stream`: mirror `test_run_on_line_callback_exception_does_not_break_stream`. Raised exception on line 1 is logged, lines 2 and 3 still fire.

Session:
- `test_sdk_session_persisted_from_result_message`: fake result message carries `session_id="sess-xyz"`; after the call, `session.json` contains it.
- `test_sdk_session_loaded_on_first_call`: pre-write `session.json` with `{"session_id":"prior"}`; first call constructs `ClaudeAgentOptions(resume="prior")` (assert via captured kwargs).
- `test_sdk_stale_session_reconnects_and_retries`: first `query()` raises a synthesized exception whose message contains "no conversation found"; second attempt succeeds. Verify `aclose` was called on the first client, a new client was built with `resume=None`, and the call returns the fresh result.

Retry + timeout:
- `test_sdk_retry_on_failure`: first two calls raise a generic transient exception; third succeeds. Patch `asyncio.sleep` so backoff is instant. Assert 3 attempts.
- `test_sdk_timeout_raises_and_kills_client`: `receive_response` yields then awaits forever inside an unsignalled `asyncio.Event`; `claude_timeout_seconds=0.05` → `ClaudeCLIError` with "timed out" in the message. Assert `aclose` was called on the hung client.

Rate limiter:
- `test_sdk_rate_limiter_called`: mock `_rate_limiter.acquire`; ensure it is awaited before `query`.

Lifecycle:
- `test_sdk_aenter_and_aclose`: `AgentSDKCLI.aenter` opens a client, `aclose` closes it; `aclose` after close is a no-op.

**Acceptance criteria**
- All new tests pass without `claude-agent-sdk` actually installed (pure mock harness) so CI without SDK auth still runs them.
- Coverage on `src/agent_sdk_cli.py` ≥ 85% lines.
- Existing `tests/test_claude_cli.py` still green — zero edits.

---

### Story 6 — Docs, rollout notes, and open questions

**Files touched**
- README section near the existing "Configuration" section (exact file TBD — do not add new markdown files).
- `.omc/plans/open-questions.md` (append only).

**Changes**
- README: short snippet showing how to flip the flag and what to expect.
  ```toml
  # ~/.heare/config.toml
  use_agent_sdk = true       # persistent SDK session (experimental)
  # claude_sdk_cli_path = "/opt/homebrew/bin/claude"  # optional override
  ```
- Open questions (see section below): add entries for SDK computer-tool name, session_id surface, and whether the decider should migrate to its own dedicated `ClaudeSDKClient` if prompt-level tool-denial proves leaky.

**Acceptance criteria**
- README diff is <30 lines.
- Open questions file contains the rollout entries.
- No new `.md` files under `docs/` or similar.

## Rollout sequence

1. Land Story 1 + 2 in one commit (`chore(claude): extract shared decider parsing, add use_agent_sdk flag`). Default is still subprocess, tests still green.
2. Land Story 3 in a second commit (`feat(claude): AgentSDKCLI backend with persistent session`). Backend exists but is unreachable until Story 4.
3. Land Story 4 + 5 together (`feat(claude): wire AgentSDKCLI factory + unit tests`). Flag is live but off by default.
4. Land Story 6 (`docs(claude): rollout notes for agent-sdk backend`).
5. Manual validation on a dev machine with `use_agent_sdk = true`:
   - Confirm daemon boots and identity bootstrap works.
   - Confirm decider round-trip latency drops (measured via the existing `[TIMING]` log in `decider.py:606`).
   - Confirm a long `call_action` streams into the dashboard per line.
   - Confirm restart resumes the same session.
6. After ≥1 week of dev-machine usage with no regressions, flip default to `True` in a follow-up commit and remove the subprocess path in a later cleanup.

## Success Criteria

- `pytest tests/` green for both `use_agent_sdk=False` (default, subprocess backend) and any new SDK tests that mock `ClaudeSDKClient`.
- Flipping `use_agent_sdk = true` locally boots the daemon, performs an identity bootstrap call, handles a decider tick, and executes one action with per-line streaming visible in the dashboard.
- Decider latency (prompt-in → decision-out) measured by the existing `[TIMING]` log drops by at least 300ms per call on a warm daemon, on a machine where `claude -p` cold-starts today.
- Daemon shutdown closes the persistent SDK session (no orphaned Node process in `ps`).
- Session resume survives a daemon restart: `session.json` session_id matches the SDK's reported session after the first call of the new run.
- Feature flag default remains `False` at the end of this plan; the flip to `True` is an explicit, separate follow-up.

## Open Questions

These go to `.omc/plans/open-questions.md` via the same append step at handoff time.

1. **SDK computer-tool identifier** — the exact `allowed_tools` string for the computer/bash tools needs verification against the installed SDK version. Fallback: single string `"Bash"` and document that computer-use actions require a config override.
2. **SDK `SDKResultMessage.session_id` field name** — confirmed in SDK README, but concrete attribute name (`session_id` vs `sessionId`) must be checked on the installed version before finalizing `_persist_session`. Keep the attribute-access in one helper so fixing this is a one-line change.
3. **Stale-session exception class** — the SDK may raise a typed exception (preferred) or a generic RuntimeError carrying the CLI's stderr text. Decide whether to pattern-match on class name or substring. Defer to implementation; start with substring match for parity with subprocess.
4. **Decider tool isolation** — if prompt-level "no tools" proves leaky (model occasionally calls Bash during a decider tick), the fallback is to maintain *two* `ClaudeSDKClient` instances, one per regime, at the cost of double the persistent-daemon footprint. Track usage during rollout.
5. **Tests without the SDK installed** — decide whether `tests/test_agent_sdk_cli.py` should `pytest.importorskip("claude_agent_sdk")` or use pure mocks so CI does not need the package. Preferred: pure mocks. Document the decision in the test module docstring.
6. **Rate-limiter double-count** — if the SDK performs internal retries, the external `RateLimiter` may under-count actual API calls. Document and defer.
7. **`compact_if_needed` on SDK path** — `ClaudeCLI.compact_if_needed` runs `claude --compact` via subprocess on context-limit errors. The SDK path has no stderr to monitor and no equivalent. Tracked as a known limitation of `use_agent_sdk=true`; if context limits become frequent, implement a standalone `claude --compact` subprocess call in `AgentSDKCLI` as a fallback (does not require the persistent session).
8. **SDK persistent process verification** — Confirm that `claude-agent-sdk 0.1.59` actually keeps a single Node.js process alive across multiple `query()` calls (not per-call spawning). Verify by running `ps` during a session. If it spawns per call, the latency premise of the plan collapses and the flag default should stay `False` indefinitely.
