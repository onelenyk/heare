# Runtime LLM Provider Switching

**Date:** 2026-05-01
**Status:** DRAFT
**Complexity:** MEDIUM
**Scope:** 5 files modified, 1 file created

---

## Context

The heare voice assistant uses `OpenRouterLLMService` (pipecat) as its sole LLM provider. The user wants to add z.ai as a second provider and switch between them at runtime (no daemon restart) using the same flag-file pattern already established for mode (`~/.heare/mode`) and mute (`~/.heare/mute.flag`).

**Key architectural facts:**
- `OpenRouterLLMService` extends `OpenAILLMService` which extends `BaseOpenAILLMService`
- The base class stores the OpenAI-compatible client as `self._client` (an `AsyncOpenAI` instance)
- All LLM calls go through `get_chat_completions()` which calls `self._client.chat.completions.create()`
- Model name is in `self._settings.model`
- `build_chat_completion_params()` is the override point for Gemini deduplication
- z.ai is OpenAI-compatible (same client library, different base_url + api_key + model)
- `identity.py` and `speaker_namer.py` use raw HTTP to OpenRouter (out of scope -- they are one-shot bootstrap/background tasks, not the conversation pipeline)

---

## Work Objectives

1. Create a `SwitchableLLMService` that reads `~/.heare/provider` on each LLM call and routes to the correct backend
2. Add z.ai configuration fields to `Settings`
3. Wire the new service into the pipeline
4. Expose provider switching via CLI and watch dashboard

---

## Guardrails

**Must Have:**
- Switch takes effect on the next LLM call (no restart)
- Falls back to openrouter if the provider file is missing or unreadable
- Gemini system-message deduplication only fires for the OpenRouter+Gemini combo
- Both clients initialized at startup (no cold-start latency on switch)
- z.ai base_url is configurable (not hardcoded)

**Must NOT Have:**
- Changes to `identity.py` or `speaker_namer.py` (out of scope)
- Any new pip dependencies
- Changes to the pipecat library itself
- Automatic retry/failover between providers (future work)

---

## Task Flow

### Step 1: Config additions (`src/config.py`)

Add new fields to the `Settings` dataclass (around line 254, near `openrouter_api_key`):

```python
# --- LLM provider switching ---
llm_provider: str = "openrouter"           # "openrouter" | "zai"
provider_file: Path = field(default_factory=lambda: HEARE_HOME / "provider")
zai_api_key: str | None = None
zai_base_url: str = "https://api.z.ai/api/anthropic"  # configurable via config.toml
zai_model: str = "claude-3-5-sonnet"        # configurable via config.toml, no hardcoded default (future: dynamic model list)
```

In `load_settings()` (around line 384, near the other `os.environ.get` calls):

```python
settings.zai_api_key = os.environ.get("ZAI_API_KEY")
```

And read the initial provider from the file (same pattern as mode_file, around line 399):

```python
if settings.provider_file.exists():
    raw = settings.provider_file.read_text().strip().lower()
    if raw in ("openrouter", "zai"):
        settings.llm_provider = raw
```

**Acceptance criteria:**
- `load_settings()` returns a `Settings` with all five new fields populated
- `ZAI_API_KEY` env var is read; `zai_base_url` and `zai_model` are overridable via `config.toml`
- Initial provider read from `~/.heare/provider` file if it exists

---

### Step 2: Create `SwitchableLLMService` (`src/switchable_llm.py`)

New file. The class subclasses `OpenAILLMService` and holds two pre-initialized `AsyncOpenAI` clients.

**Design approach -- swap `self._client` and `self._settings.model` before each call:**

The base class `get_chat_completions()` and `run_inference()` both use `self._client` and `self._settings.model`. Rather than reimplementing all the streaming/retry logic, the switchable service overrides `get_chat_completions()` to:
1. Read `~/.heare/provider` (with a short cache -- `os.path.getmtime` check, not every call)
2. Set `self._client` and `self._settings.model` to the active provider's values
3. Call `super().get_chat_completions()`

```python
class SwitchableLLMService(OpenAILLMService):
    """LLM service that switches between OpenRouter and z.ai at runtime."""

    def __init__(
        self,
        *,
        openrouter_api_key: str,
        openrouter_model: str,
        zai_api_key: str | None,
        zai_model: str,
        zai_base_url: str,
        provider_file: Path,
        **kwargs,
    ):
        # Initialize as OpenRouter first (the default provider)
        super().__init__(
            api_key=openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            model=openrouter_model,
            **kwargs,
        )
        # Store config for both providers
        self._or_client = self._client  # already created by super().__init__
        self._or_model = openrouter_model

        self._zai_api_key = zai_api_key
        self._zai_model = zai_model
        self._zai_base_url = zai_base_url
        self._zai_client: AsyncOpenAI | None = None  # lazy or eager init
        if zai_api_key:
            self._zai_client = self.create_client(
                api_key=zai_api_key,
                base_url=zai_base_url,
            )

        self._provider_file = provider_file
        self._active_provider = "openrouter"
        self._provider_file_mtime: float = 0.0

    def _sync_provider(self) -> str:
        """Read provider file (mtime-gated). Returns 'openrouter' or 'zai'."""
        ...  # check mtime, read file, update self._active_provider
        ...  # fallback to "openrouter" on any error

    def _apply_provider(self):
        """Swap self._client and self._settings.model to match active provider."""
        if self._active_provider == "zai" and self._zai_client is not None:
            self._client = self._zai_client
            self._settings.model = self._zai_model
        else:
            self._client = self._or_client
            self._settings.model = self._or_model

    async def get_chat_completions(self, params_from_context):
        self._sync_provider()
        self._apply_provider()
        return await super().get_chat_completions(params_from_context)

    def build_chat_completion_params(self, params_from_context):
        """Gemini dedup only when active provider is openrouter + model contains 'gemini'."""
        params = OpenAILLMService.build_chat_completion_params(self, params_from_context)
        if (
            self._active_provider == "openrouter"
            and "gemini" in self._settings.model.lower()
        ):
            # ... Gemini system-message deduplication logic (copied from OpenRouterLLMService)
            ...
        return params

    @property
    def active_provider(self) -> str:
        return self._active_provider
```

**Key decisions:**
- `_sync_provider()` uses `os.path.getmtime()` to avoid re-reading the file on every frame -- only reads when mtime changes. This is the same zero-cost pattern used by `MuteGateProcessor`.
- `build_chat_completion_params` skips `super()` on `OpenRouterLLMService` and calls `OpenAILLMService.build_chat_completion_params` directly, then conditionally applies the Gemini dedup. This avoids inheriting from `OpenRouterLLMService` (which would hardcode the base_url).
- If `zai_api_key` is None, the service still initializes but switching to zai is a no-op (stays on openrouter with a log warning).

**Acceptance criteria:**
- Service initializes with both clients when both API keys are present
- Reading `~/.heare/provider` containing "zai" causes the next `get_chat_completions` to use the z.ai client
- Reading "openrouter" (or missing/corrupt file) uses the OpenRouter client
- Gemini dedup fires only for openrouter + gemini model
- No file I/O on every call -- mtime gating works correctly
- `active_provider` property returns current provider string

---

### Step 3: Pipeline wiring (`src/pipeline.py`)

Replace lines 392-395:

```python
# Before:
llm_service = OpenRouterLLMService(
    api_key=settings.openrouter_api_key,
    model=settings.openrouter_model,
)

# After:
from .switchable_llm import SwitchableLLMService
llm_service = SwitchableLLMService(
    openrouter_api_key=settings.openrouter_api_key,
    openrouter_model=settings.openrouter_model,
    zai_api_key=settings.zai_api_key,
    zai_model=settings.zai_model,
    zai_base_url=settings.zai_base_url,
    provider_file=settings.provider_file,
)
```

Also update the import at line 216: remove `OpenRouterLLMService` import (or keep it if used elsewhere in the file -- verify).

Update the startup guard (line 227-229): keep the OpenRouter key check but make it non-fatal if zai_api_key is present (at least one provider must have a key).

**Acceptance criteria:**
- Pipeline starts with `SwitchableLLMService` instead of `OpenRouterLLMService`
- All existing tool registration (`register_all_tools`, dynamic tools) still works (the service is still an `OpenAILLMService` subclass)
- Startup fails only if BOTH provider keys are missing

---

### Step 4: CLI command (`src/main.py`)

Add a `provider` subcommand following the exact `_cmd_mode` pattern:

```python
def _cmd_provider(args: argparse.Namespace) -> int:
    settings = load_settings()
    provider = args.provider_name
    settings.provider_file.parent.mkdir(parents=True, exist_ok=True)
    settings.provider_file.write_text(provider)
    print(f"LLM provider set to {provider}")
    return 0
```

Register in `build_parser()` (near line 740, after the mode parser):

```python
prov_p = sub.add_parser("provider", help="Set the active LLM provider (hot-reloaded)")
prov_p.add_argument("provider_name", choices=["openrouter", "zai"])
```

Add dispatch in the `cmd_map` dict (around line 790):

```python
"provider": _cmd_provider,
```

**Acceptance criteria:**
- `heare provider zai` writes "zai" to `~/.heare/provider`
- `heare provider openrouter` writes "openrouter" to `~/.heare/provider`
- Invalid values are rejected by argparse choices

---

### Step 5: Watch dashboard (`src/watch.py`)

Two changes:

**5a. Display active provider in status panel.**

In the `_build_status_panel` (or wherever mode/mute status is rendered), add a line:

```python
provider_file = settings.provider_file
active = provider_file.read_text().strip() if provider_file.exists() else "openrouter"
# Add to status display: "provider: openrouter" or "provider: zai"
```

**5b. Add `p` hotkey to toggle provider.**

In `_dispatch_key()` (line 462), add:

```python
if key == "p":
    pf = settings.provider_file
    current = pf.read_text().strip() if pf.exists() else "openrouter"
    new_provider = "zai" if current == "openrouter" else "openrouter"
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(new_provider)
    return f"provider: {new_provider}"
```

Update the hotkey legend shown in the controls panel to include `p = toggle provider`.

**Acceptance criteria:**
- Status panel shows current provider
- Pressing `p` toggles between "openrouter" and "zai" and writes to the file
- The dashboard reflects the new provider on the next refresh tick

---

### Step 6: Environment file updates

In `heare.env.example`, add:

```
# OpenRouter API Key (required if using openrouter provider)
OPENROUTER_API_KEY=your_openrouter_api_key_here

# z.ai API Key (required if using zai provider)
ZAI_API_KEY=your_zai_api_key_here
```

(Note: `OPENROUTER_API_KEY` was already used but not documented in the example file. Add it alongside `ZAI_API_KEY`.)

**Acceptance criteria:**
- Both API key vars are documented in the example file
- Comments explain which provider each key belongs to

---

## Execution Order

1. **Step 1** (config) -- no dependencies
2. **Step 2** (switchable_llm.py) -- depends on Step 1 for field names
3. **Step 3** (pipeline wiring) -- depends on Steps 1 + 2
4. **Step 4** (CLI) and **Step 5** (watch) -- independent of Steps 2/3, only depend on Step 1
5. **Step 6** (env example) -- independent, can be done anytime

Steps 4, 5, and 6 can be done in parallel after Step 1.

---

## Success Criteria

- [ ] `heare start` boots with both providers pre-initialized (no crash if ZAI_API_KEY is unset)
- [ ] Writing "zai" to `~/.heare/provider` causes the next user utterance to be processed by z.ai
- [ ] Writing "openrouter" (or deleting the file) reverts to OpenRouter
- [ ] `heare provider zai` / `heare provider openrouter` CLI works
- [ ] Watch dashboard shows active provider and `p` hotkey toggles it
- [ ] Gemini system-message deduplication only fires for OpenRouter+Gemini
- [ ] No new pip dependencies introduced
- [ ] Existing tests still pass (no regressions)

---

## Open Questions

See `.omc/plans/open-questions.md` for tracked items.
