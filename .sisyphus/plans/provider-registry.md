# Provider Registry — Centralize LLM Provider Configuration

## TL;DR

> **Quick Summary**: Replace 45+ hardcoded provider strings across 14 source files with a single `ProviderConfig` registry. Adding a new provider becomes a ~5-line dataclass entry instead of editing 27 files.
>
> **Deliverables**:
> - New: `src/agent/llm/providers.py` — ProviderConfig dataclass + PROVIDERS registry
> - Modified: 14 source files refactored to use registry
> - Modified: 13 test files updated for new API
> - Fixed: 3 latent bugs discovered during audit
> - New: `tests/test_api.py` — test coverage for HTTP API
>
> **Estimated Effort**: Large (~14 source files, ~13 test files)
> **Parallel Execution**: YES — 4 waves, up to 6 tasks parallel
> **Critical Path**: Wave 1 (registry) → Wave 2 (core refactor) → Wave 3 (dashboard + tools) → Wave 4 (tests + verify)

---

## Context

### Original Request
"It unimaginable, why so many dumb solution. okay plan this migration to let then easy support Providers"

### Current Problem
Provider names are hardcoded as raw strings across **27 files** (14 source + 13 test). The string `"deepseek"` appears 45+ times in `switchable.py` alone. Adding a provider requires: 14 source files changed, 13 test files updated, 3 new config fields, 1 new identity bootstrap function, model whitelists, dashboard colors, pricing entries, and CLI enum values — all edited independently.

### Proposed Solution
A `ProviderConfig` dataclass registry in a single file. Every other file reads from it. Adding OpenRouter becomes:

```python
# In providers.py — just this:
PROVIDERS["openrouter"] = ProviderConfig(
    key="openrouter",
    display_name="OpenRouter",
    api_key_attr="openrouter_api_key",
    api_key_env="OPENROUTER_API_KEY",
    base_url="https://openrouter.ai/api/v1",
    default_model="google/gemini-3.1-flash-lite-preview-20260303",
    api_style="openai",
    service_factory=make_openai_service,
    dashboard_color="blue",
    model_whitelist=(...),
    pricing={"google/gemini-3.1-flash-lite": (0.075, 0.30), ...},
)
# + env var in config.py
# Done. Everything else is automatic.
```

### Metis Review
**Critical Gaps Found**:
1. **Bug: `main.py:110-113`** — Requires `DEEPSEEK_API_KEY` unconditionally at daemon startup. If only zai/opencode keys are configured, daemon refuses to start.
2. **Bug: `main.py:121-125`** — Identity bootstrap hardcoded to `build_deepseek_bootstrap()`. If deepseek key is absent, identity generation fails silently.
3. **Bug: `api.py:77-81`** — `_available_providers()` returns only `["deepseek"]`. zai and opencode invisible to HTTP API.
4. **Bug: `watch/data.py:86`** — `current_provider()` hardcodes `return "deepseek"` instead of reading state.
5. **Design risk**: `api_style: Literal["openai", "anthropic"]` is insufficient — DeepSeek and OpenCode both use `OpenAILLMService` but with different constructor patterns; z.ai requires a pre-built `AsyncAnthropic` client. Need `service_factory` callable instead.
6. **Design risk**: `identity_bootstrap: Callable` in dataclass won't work — it's a runtime closure. Use `identity_endpoint: str` + factory function instead.
7. **Coverage gap**: `tests/test_api.py` doesn't exist — zero test coverage for the HTTP API layer.

---

## Work Objectives

### Core Objective
Create a `ProviderConfig` registry that centralizes all provider-specific data so adding a new LLM provider requires defining a single dataclass entry instead of editing 27 files.

### Concrete Deliverables
- `src/agent/llm/providers.py` — `ProviderConfig` dataclass + `PROVIDERS` dict + helper functions
- All 14 source files refactored to use registry
- All 13 test files updated
- 4 bugs fixed (api.py, watch/data.py, main.py guard, main.py identity)
- `tests/test_api.py` — new test file

### Definition of Done
- [ ] `uv run python -c "from src.agent.llm.providers import PROVIDERS; print(list(PROVIDERS.keys()))"` → `['deepseek', 'zai', 'opencode']`
- [ ] `uv run python -c "from src.agent.llm.switchable import SwitchableLLMService"` — imports, no hardcoded provider names
- [ ] `uv run python -m src.main provider --help` — shows dynamic provider list
- [ ] `uv run pytest tests/ -q --tb=short` — all tests pass
- [ ] `grep -r '"deepseek"' src/ --include='*.py' | grep -v providers.py | grep -v test_ | grep -v '# ' | wc -l` → 0 (or near-zero, only in comments/docs)
- [ ] Daemon starts with only `ZAI_API_KEY` set (DeepSeek guard lifted)
- [ ] `GET /state` returns all 3 available providers (api.py bug fixed)

### Must Have
- Adding a provider = define a dict entry + add env var — no source changes in other files
- All existing functionality preserved (hot-reload, set_provider tool, dashboard toggle, pricing)
- Backward compatible with existing `~/.heare/provider` file and state DB

### Must NOT Have (Guardrails)
- No change to runtime behavior — same 3 providers, same hot-reload, same API
- No new config file format — user config stays in `config.toml`
- No change to Pipecat pipeline stages or audio processing
- No removal of any existing feature
- Do NOT create a TOML/JSON file for provider definitions — they're code-level concerns

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest via `uv run pytest`)
- **Automated tests**: Tests-after (update existing tests, add api.py tests)
- **Framework**: pytest

### QA Policy
Every task includes agent-executed QA scenarios.
- **Smoke tests**: Import checks, registry enumeration
- **Functional**: Provider switching, tool schema generation, dashboard rendering
- **Evidence**: `.sisyphus/evidence/task-{N}-*.txt`

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — registry foundation):
├── T1: Create providers.py — ProviderConfig + PROVIDERS + helpers [deep]
└── T2: Create service_factory functions in providers.py [deep]

Wave 2 (After Wave 1 — core refactor, MAX PARALLEL):
├── T3: Refactor switchable.py — data-driven delegate construction [deep]
├── T4: Refactor config.py — use registry for env vars, validation [quick]
├── T5: Refactor build.py — use registry for constructor, guard [quick]
├── T6: Refactor main.py — dynamic CLI choices, fix identity bootstrap [quick]
├── T7: Refactor identity.py — generic bootstrap via registry [unspecified-high]
└── T8: Refactor api.py — fix _available_providers bug [quick]

Wave 3 (After Wave 2 — tools + dashboard):
├── T9: Refactor tool layer — schemas.py, direct.py, registry.py [quick]
├── T10: Refactor watch/models.py — model whitelists from registry [unspecified-high]
├── T11: Refactor watch/app.py + widgets.py + screens.py — colors from registry [visual-engineering]
├── T12: Refactor watch/data.py + __init__.py + _legacy.py [quick]
└── T13: Refactor pricing.py + storage.py + usage_recorder [quick]

Wave 4 (After Wave 3 — tests + verification):
├── T14: Update all existing test files [unspecified-high]
├── T15: Create tests/test_api.py [quick]
├── T16: Full test suite + import smoke test [deep]
└── T17: Dead code audit — ensure zero hardcoded provider strings remain [deep]

Wave FINAL (After Wave 4):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real QA — start daemon with various provider combos (unspecified-high)
└── F4: Scope fidelity check (deep)
```

**Critical Path**: T1 → T3 → T9 → T14 → T16 → F1-F4
**Max Concurrent**: 6 (Waves 2-3)

---

## TODOs

### Wave 1: Registry Foundation

- [x] 1. Create `providers.py` — ProviderConfig dataclass + PROVIDERS registry

  **What to do**:
  - New file: `src/agent/llm/providers.py`
  - Define `ProviderConfig` dataclass with fields:
    ```python
    @dataclass(frozen=True)
    class ProviderConfig:
        key: str                          # "deepseek", "zai", "opencode"
        display_name: str                 # "DeepSeek", "Z.AI", "OpenCode Go"
        api_key_attr: str                 # Settings dataclass attr name
        api_key_env: str                  # Env var name (DEEPSEEK_API_KEY)
        base_url: str                     # API endpoint
        default_model: str                # Default model ID
        api_style: str                    # "openai" | "anthropic"
        dashboard_color: str              # Textual color name
        timeout: float = 30.0
        model_whitelist: tuple[str, ...] = ()
        # (model_id, input_price_per_1m, output_price_per_1m)
        pricing: tuple[tuple[str, float, float], ...] = ()
        identity_endpoint: str = ""       # URL for identity bootstrap, empty = use generic
        identity_model: str = ""          # Model for identity bootstrap
    ```
  - Define `PROVIDERS: dict[str, ProviderConfig]` with all 3 current providers
  - Define helper functions:
    - `get_available(settings) -> list[str]` — which providers have API keys
    - `get_active(settings) -> str` — current active provider
    - `get_config(key) -> ProviderConfig` — lookup
    - `all_keys() -> list[str]` — all registered provider keys
  - DeepSeek pricing entries from current `pricing.py`
  - z.ai pricing entries (claude models)
  - OpenCode pricing entries (minimax models)
  - Model whitelists from `watch/models.py` (copy, don't remove yet)

  **Must NOT do**:
  - Do NOT delete anything from existing files yet
  - Do NOT import from switchable.py or build.py (keep providers.py dependency-free)

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**: Wave 1, can run with T2. Blocks Wave 2.

  **References**:
  - `src/agent/llm/pricing.py:18-30` — Current LLM price entries to migrate
  - `src/watch/models.py:17-51` — Current model whitelists to migrate
  - `src/config.py:277-288` — Current provider config fields

  **QA Scenarios**:
  ```
  Scenario: Registry imports and enumerates
    Tool: Bash
    Steps:
      1. uv run python -c "from src.agent.llm.providers import PROVIDERS, get_available; print(list(PROVIDERS.keys()))"
    Expected Result: ['deepseek', 'zai', 'opencode']
    Evidence: .sisyphus/evidence/task-1-registry-import.txt

  Scenario: get_available works with mock settings
    Tool: Bash (python -c)
    Steps:
      1. Mock settings with only deepseek key, call get_available()
      2. Assert returns ['deepseek']
    Expected Result: Correct filtering
    Evidence: .sisyphus/evidence/task-1-get-available.txt
  ```

  **Commit**: YES
  - Message: `feat(providers): add ProviderConfig registry`
  - Files: `src/agent/llm/providers.py`

- [x] 2. Create `service_factory` functions in providers.py

  **What to do**:
  - In `providers.py`, define factory functions that construct the correct Pipecat service:
    ```python
    def make_openai_service(config: ProviderConfig, api_key: str, **kwargs) -> OpenAILLMService:
        return OpenAILLMService(
            api_key=api_key,
            base_url=config.base_url,
            settings=OpenAILLMService.Settings(model=config.default_model),
            **kwargs,
        )

    def make_anthropic_service(config: ProviderConfig, api_key: str) -> AnthropicLLMService:
        return AnthropicLLMService(
            api_key=api_key,
            settings=AnthropicLLMService.Settings(model=config.default_model),
            client=AsyncAnthropic(api_key=api_key, base_url=config.base_url),
        )
    ```
  - Store factory in `ProviderConfig.service_factory` field
  - Also add `make_identity_bootstrap(config, api_key, model, timeout)` factory function

  **Must NOT do**:
  - Do NOT import from pipecat at module level (defer to avoid portaudio dependency)
  - Do NOT use closures that capture mutable state

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**: Wave 1, can run with T1. Blocks T3 (switchable.py refactor).

  **QA Scenarios**:
  ```
  Scenario: Factory functions create services
    Tool: Bash (python -c)
    Steps:
      1. Call make_openai_service with deepseek config
      2. Assert isinstance(result, OpenAILLMService)
    Expected Result: Correct service type
    Evidence: .sisyphus/evidence/task-2-factories.txt
  ```

  **Commit**: YES
  - Message: `feat(providers): add service factory functions`
  - Files: `src/agent/llm/providers.py`

### Wave 2: Core Refactor (6 tasks, ALL parallel)

- [x] 3. Refactor `switchable.py` — data-driven delegate construction

  **What to do**:
  - Remove all hardcoded `_or_service`, `_zai_service`, `_oc_service`, `_deepseek_service` fields
  - Replace with `_delegates: dict[str, LLMService]` built by iterating `PROVIDERS`
  - Constructor: iterate `PROVIDERS`, check `getattr(settings, config.api_key_attr)`, build delegate via `config.service_factory`
  - `_all_delegates()` → return `list(self._delegates.values())`
  - `_delegate_for(provider)` → return `self._delegates.get(provider)`
  - `_provider_for_delegate(delegate)` → reverse lookup in `_delegates`
  - `_first_available_provider()` → first key in `_delegates`
  - `_active_delegate()` → `self._delegates[self._active_provider]`
  - `available` list → keys of `_delegates`
  - `_all_services()` → same as `_all_delegates()`
  - Remove `self._or_model`, `self._zai_model`, etc — use `config.default_model` lookup
  - Lifecycle methods (setup/start/stop/cancel/cleanup) already iterate — just use `_all_services()`
  - `register_function`/`unregister_function` already iterate — no change needed
  - Remove `_zai_disabled` — make it generic `_disabled_providers: set[str]`
  - Fallback logic: on error, disable current provider, switch to first available
  - Metrics tag: use `config.display_name` + `config.default_model`

  **Must NOT do**:
  - Do NOT change the public API — same constructor signature (but now generic)
  - Do NOT break the frame relay mechanism

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**: Wave 2, depends on T1+T2. Can run with T4-T8.

  **QA Scenarios**:
  ```
  Scenario: SwitchableLLMService imports without hardcoded provider names
    Tool: Bash
    Steps:
      1. grep -c '"deepseek"\|"zai"\|"opencode"' src/agent/llm/switchable.py
    Expected Result: 0 (or only in comments/docs)
    Evidence: .sisyphus/evidence/task-3-switchable-clean.txt

  Scenario: Constructor works with all 3 providers
    Tool: Bash (python -c)
    Steps:
      1. Create service with all 3 keys set
      2. Assert active_provider is first available
      3. Assert len(_all_delegates()) == 3
    Expected Result: 3 delegates, active is deepseek
    Evidence: .sisyphus/evidence/task-3-constructor.txt
  ```

  **Commit**: YES
  - Message: `refactor(switchable): data-driven provider construction`
  - Files: `src/agent/llm/switchable.py`

- [x] 4. Refactor `config.py` — use registry for env vars and validation

  **What to do**:
  - In `load_settings()`, replace hardcoded env var reads with loop over `PROVIDERS`:
    ```python
    for key, cfg in PROVIDERS.items():
        setattr(settings, cfg.api_key_attr, os.environ.get(cfg.api_key_env))
    ```
  - Provider file validation: use `all_keys()` instead of hardcoded `("deepseek", "zai", "opencode")`
  - Update `llm_provider` docstring: "deepseek" → reference to registry default
  - Remove any remaining OpenRouter references in comments

  **Must NOT do**:
  - Do NOT remove existing `Settings` fields (they're still needed as dataclass attrs)
  - Do NOT change the config.toml format

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Wave 2, depends on T1.

  **QA Scenarios**:
  ```
  Scenario: load_settings reads all provider env vars
    Tool: Bash
    Steps:
      1. DEEPSEEK_API_KEY=test ZAI_API_KEY=test OPENCODE_API_KEY=test uv run python -c "
         from src.config import load_settings; s = load_settings()
         assert s.deepseek_api_key == 'test'
         assert s.zai_api_key == 'test'"
    Expected Result: All env vars loaded
    Evidence: .sisyphus/evidence/task-4-config-env.txt
  ```

  **Commit**: YES
  - Message: `refactor(config): use provider registry for env vars`
  - Files: `src/config.py`

- [x] 5. Refactor `build.py` — use registry for constructor + lift DeepSeek guard

  **What to do**:
  - Replace hardcoded `SwitchableLLMService(zai_api_key=..., opencode_api_key=..., deepseek_api_key=...)` with kwargs built from registry:
    ```python
    kwargs = {}
    for key, cfg in PROVIDERS.items():
        kwargs[cfg.api_key_attr] = getattr(settings, cfg.api_key_attr, None)
        kwargs[f"{key}_model"] = getattr(settings, f"{key}_model", cfg.default_model)
        kwargs[f"{key}_base_url"] = getattr(settings, f"{key}_base_url", cfg.base_url)
    llm_service = SwitchableLLMService(state=state, **kwargs)
    ```
  - **BUG FIX**: Replace DeepSeek-only guard with generic check:
    ```python
    available = [cfg for cfg in PROVIDERS.values() if getattr(settings, cfg.api_key_attr)]
    if not available:
        raise RuntimeError(f"No LLM provider configured. Set one of: ...")
    ```
  - Update log line to use `llm_service.active_provider` + config lookup
  - Ensure `provider_getter` lambda still works

  **Must NOT do**:
  - Do NOT change `SwitchableLLMService` constructor signature yet (T3 handles that)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Wave 2, depends on T1. Can run with T3-T8.

  **QA Scenarios**:
  ```
  Scenario: Daemon can start with only ZAI_API_KEY (no DEEPSEEK_API_KEY)
    Tool: Bash (python -c)
    Steps:
      1. Unset DEEPSEEK_API_KEY, set ZAI_API_KEY
      2. Import build_pipeline, assert no RuntimeError about deepseek
    Expected Result: Imports, guard passes
    Evidence: .sisyphus/evidence/task-5-guard-fixed.txt
  ```

  **Commit**: YES
  - Message: `fix(build): lift DeepSeek-only guard, use provider registry`
  - Files: `src/pipeline/build.py`

- [x] 6. Refactor `main.py` — dynamic CLI choices + fix identity bootstrap

  **What to do**:
  - CLI provider choices: `choices=PROVIDERS.all_keys()` instead of hardcoded list
  - **BUG FIX (main.py:110-113)**: Replace `if not settings.openrouter_api_key and not settings.zai_api_key and not settings.deepseek_api_key` with generic check using `get_available()`
  - **BUG FIX (main.py:121-125)**: Replace `build_deepseek_bootstrap()` hardcode with registry lookup:
    ```python
    active_cfg = PROVIDERS.get(llm_service.active_provider)
    identity_factory = make_identity_bootstrap(active_cfg, api_key, model)
    identity = await ensure_identity(identity_factory, settings)
    ```
  - `_cmd_provider()`: use `all_keys()` for validation

  **Must NOT do**:
  - Do NOT change CLI command names or behavior
  - Do NOT remove any subcommands

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Wave 2, depends on T1.

  **QA Scenarios**:
  ```
  Scenario: CLI help shows dynamic provider list
    Tool: Bash
    Steps:
      1. uv run python -m src.main provider --help 2>&1
    Expected Result: Shows deepseek, zai, opencode (from registry)
    Evidence: .sisyphus/evidence/task-6-cli-choices.txt

  Scenario: Daemon starts with only ZAI_API_KEY (bug fix verification)
    Tool: Bash (python -c)
    Steps:
      1. Mock env with only ZAI_API_KEY, import _cmd_start
      2. Verify no RuntimeError
    Expected Result: No crash
    Evidence: .sisyphus/evidence/task-6-guard-fix.txt
  ```

  **Commit**: YES
  - Message: `fix(main): dynamic provider CLI choices, lift deepseek guard, generic identity`
  - Files: `src/main.py`

- [x] 7. Refactor `identity.py` — generic bootstrap via registry

  **What to do**:
  - Keep existing `build_deepseek_bootstrap()` as a specific implementation
  - Add generic `make_identity_bootstrap(config, api_key, model, timeout)` that delegates to the right endpoint
  - Use `config.identity_endpoint` to determine URL
  - For OpenAI-compatible endpoints (DeepSeek, OpenCode): use `chat/completions` format
  - For Anthropic endpoints (z.ai): use Anthropic Messages API format
  - Error messages should use `config.display_name` not hardcoded "deepseek"

  **Must NOT do**:
  - Do NOT remove `build_deepseek_bootstrap()` — keep as implementation, not as the sole entry point
  - Do NOT break existing identity generation flow

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 2, depends on T1.

  **QA Scenarios**:
  ```
  Scenario: Generic bootstrap works with deepseek config
    Tool: Bash (python -c)
    Steps:
      1. Create factory with deepseek config, mock API key
      2. Assert factory is callable, returns coroutine
    Expected Result: Factory created
    Evidence: .sisyphus/evidence/task-7-identity.txt
  ```

  **Commit**: YES
  - Message: `refactor(identity): generic bootstrap via provider registry`
  - Files: `src/agent/identity.py`

- [x] 8. Fix `api.py` — `_available_providers()` bug

  **What to do**:
  - Replace hardcoded `if self.config.deepseek_api_key:` with registry loop:
    ```python
    def _available_providers(self):
        from src.agent.llm.providers import PROVIDERS
        return [
            cfg.key for cfg in PROVIDERS.values()
            if getattr(self.config, cfg.api_key_attr)
        ]
    ```
  - This fixes the bug where zai and opencode were invisible

  **Must NOT do**:
  - Do NOT change API endpoint signatures

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Wave 2, depends on T1.

  **QA Scenarios**:
  ```
  Scenario: GET /state returns all available providers
    Tool: Bash (curl)
    Steps:
      1. With all 3 keys set, GET /state
      2. Assert "providers" contains ["deepseek", "zai", "opencode"]
    Expected Result: All 3 listed
    Evidence: .sisyphus/evidence/task-8-api-fix.txt
  ```

  **Commit**: YES
  - Message: `fix(api): _available_providers uses registry, shows all configured`
  - Files: `src/api.py`

### Wave 3: Tools + Dashboard (5 tasks, ALL parallel)

- [x] 9. Refactor tool layer — schemas, direct, registry use providers

  **What to do**:
  - `schemas.py`: Replace hardcoded `"enum": ["deepseek", "zai", "opencode"]` with `"enum": PROVIDERS.all_keys()`
  - `direct.py`: Replace `if provider not in ("deepseek", "zai", "opencode")` with `if provider not in PROVIDERS`
  - `direct.py`: Error messages: use registry for valid provider names
  - `registry.py`: Update `set_provider` description to reference registry (or keep generic)
  - All provider-specific spoken responses ("Перейшов на deepseek") → keep for now, just ensure validation is generic

  **Must NOT do**:
  - Do NOT remove any tool handlers
  - Do NOT change tool function signatures

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Wave 3, depends on T1.

  **QA Scenarios**:
  ```
  Scenario: set_provider schema has dynamic enum
    Tool: Bash (python -c)
    Steps:
      1. from src.agent.tools.schemas import TOOL_SCHEMAS
      2. assert set(TOOL_SCHEMAS['set_provider'][0]['provider']['enum']) == {'deepseek', 'zai', 'opencode'}
    Expected Result: Enum from registry
    Evidence: .sisyphus/evidence/task-9-tool-schema.txt
  ```

  **Commit**: YES
  - Message: `refactor(tools): use provider registry for schemas and validation`
  - Files: `src/agent/tools/schemas.py`, `src/agent/tools/direct.py`, `src/agent/tools/registry.py`

- [x] 10. Refactor `watch/models.py` — model whitelists from registry

  **What to do**:
  - Remove `DEEPSEEK_MODELS`, `ZAI_MODELS`, `OPENCODE_MODELS` tuples
  - Replace `DEFAULT_MODEL` dict with `{cfg.key: cfg.default_model for cfg in PROVIDERS.values()}`
  - Replace `models_for_provider()` with `PROVIDERS[key].model_whitelist`
  - Replace `read_custom_models()` defaults with dynamic `{key: [] for key in PROVIDERS}`
  - `read_current_model()`: use `PROVIDERS[provider].default_model`

  **Must NOT do**:
  - Do NOT change custom_models.json format
  - Do NOT remove custom model support

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 3, depends on T1.

  **QA Scenarios**:
  ```
  Scenario: models_for_provider returns whitelist from registry
    Tool: Bash (python -c)
    Steps:
      1. from src.watch.models import models_for_provider
      2. deepseek_models = models_for_provider(mock_settings, 'deepseek')
      3. Assert len(deepseek_models) > 0
    Expected Result: Models from registry, not hardcoded
    Evidence: .sisyphus/evidence/task-10-watch-models.txt
  ```

  **Commit**: YES
  - Message: `refactor(watch/models): model whitelists from provider registry`
  - Files: `src/watch/models.py`

- [x] 11. Refactor watch dashboard UI — colors from registry

  **What to do**:
  - `widgets.py`: Replace hardcoded color map `{"zai": "cyan", "opencode": "green"}` with `PROVIDERS[key].dashboard_color`
  - `widgets.py`: AIBar provider display uses `PROVIDERS[provider].display_name`
  - `app.py`: `action_toggle_provider()` — replace hardcoded cycle with `get_available()` list cycling
  - `app.py`: Binding help text update
  - `screens.py`: Use `PROVIDERS[provider].display_name` in modal title
  - `__init__.py`: Provider color from registry

  **Must NOT do**:
  - Do NOT change widget layout or behavior
  - Do NOT remove any hotkeys

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: []

  **Parallelization**: Wave 3, depends on T1.

  **QA Scenarios**:
  ```
  Scenario: Dashboard imports and shows provider colors from registry
    Tool: Bash (python -c)
    Steps:
      1. from src.watch.widgets import AIBar
      2. from src.agent.llm.providers import PROVIDERS
      3. assert PROVIDERS['zai'].dashboard_color == 'cyan'
    Expected Result: Colors from registry
    Evidence: .sisyphus/evidence/task-11-dashboard-colors.txt
  ```

  **Commit**: YES
  - Message: `refactor(watch): provider colors and names from registry`
  - Files: `src/watch/app.py`, `src/watch/widgets.py`, `src/watch/screens.py`, `src/watch/__init__.py`

- [x] 12. Refactor `watch/data.py` + `_legacy.py` — fix bug + use registry

  **What to do**:
  - **BUG FIX (watch/data.py:86)**: `current_provider()` — replace `return "deepseek"` with actual state read
  - `watch/data.py`: Use `PROVIDERS` for provider display names
  - `_legacy.py`: Replace hardcoded 2-provider toggle with registry-based cycling
  - `_legacy.py`: Provider colors from registry

  **Must NOT do**:
  - Do NOT remove `_legacy.py` (still used as fallback)
  - Do NOT change snapshot format

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Wave 3, depends on T1.

  **QA Scenarios**:
  ```
  Scenario: current_provider reads from state, not hardcoded
    Tool: Bash (python -c)
    Steps:
      1. Mock state with provider="zai"
      2. Call current_provider()
      3. Assert returns "zai"
    Expected Result: Reads from state
    Evidence: .sisyphus/evidence/task-12-data-fix.txt
  ```

  **Commit**: YES
  - Message: `fix(watch/data): current_provider reads state, not hardcoded deepseek`
  - Files: `src/watch/data.py`, `src/watch/_legacy.py`

- [x] 13. Refactor `pricing.py` + remaining files

  **What to do**:
  - `pricing.py`: Replace `_LLM_PRICES_USD_PER_1M` dict with entries built from registry:
    ```python
    _LLM_PRICES_USD_PER_1M = {}
    for cfg in PROVIDERS.values():
        for model_id, in_price, out_price in cfg.pricing:
            _LLM_PRICES_USD_PER_1M[model_id] = (in_price, out_price)
    ```
  - `storage.py`: No schema changes needed (provider is TEXT column), but verify no hardcoded provider names in queries
  - `usage_recorder.py`: No changes needed (already generic via `provider_getter` lambda)
  - `voice/indication/core.py`: No changes needed (mode_provider is about Mode, not LLM provider)
  - `state.py`: No changes needed

  **Must NOT do**:
  - Do NOT change database schema

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Wave 3, depends on T1.

  **QA Scenarios**:
  ```
  Scenario: pricing.py built from registry
    Tool: Bash (python -c)
    Steps:
      1. from src.agent.llm.pricing import _LLM_PRICES_USD_PER_1M
      2. assert 'deepseek-chat' in _LLM_PRICES_USD_PER_1M
    Expected Result: Prices loaded from registry
    Evidence: .sisyphus/evidence/task-13-pricing.txt
  ```

  **Commit**: YES
  - Message: `refactor(pricing): load model prices from provider registry`
  - Files: `src/agent/llm/pricing.py`

### Wave 4: Tests + Verification (4 tasks)

- [x] 14. Update all existing test files

  **What to do**:
  - `test_switchable_llm.py`: Update helper functions to use registry-based constructor. Remove hardcoded provider names from assertions. Add test for generic construction.
  - `test_switchable_llm_observability.py`: Update metrics assertions.
  - `test_usage_recorder.py`: Provider-related assertions.
  - `test_llm_tools.py`: Update set_provider validation tests.
  - `test_watch_models.py`: Update model whitelist assertions — now from registry.
  - `test_watch_widgets.py`: Update color assertions — now from registry.
  - `test_watch_app.py`: Update toggle test.
  - `test_watch_data.py`: Update current_provider test.
  - `test_direct_tools.py`: Update set_provider validation.
  - `test_storage.py`: Verify no changes needed.
  - `test_pricing.py`: Verify prices still resolve.
  - `test_identity.py`: Update bootstrap tests.
  - `test_config.py`: Verify env var loading.
  - `test_indication.py`: Verify no changes needed.
  - `test_zai_e2e.py`: Verify still works.

  **Must NOT do**:
  - Do NOT delete tests — only update to match new API
  - Do NOT reduce test coverage

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**: Wave 4, depends on Waves 1-3.

  **QA Scenarios**:
  ```
  Scenario: All tests pass after update
    Tool: Bash
    Steps:
      1. uv run pytest tests/ -q --tb=short
    Expected Result: 0 failures
    Evidence: .sisyphus/evidence/task-14-tests.txt
  ```

  **Commit**: YES (one commit for all test updates)
  - Message: `test: update all tests for provider registry refactor`
  - Files: `tests/*.py` (all test files)

- [x] 15. Create `tests/test_api.py`

  **What to do**:
  - New test file: `tests/test_api.py`
  - Test `_available_providers()` with various key configurations
  - Test `GET /state` endpoint
  - Test `POST /provider` with valid/invalid providers
  - Test `POST /mode`, `POST /mute`, `POST /model`, `POST /cancel`
  - Use aiohttp test utilities or mock the app

  **Must NOT do**:
  - Do NOT require a running daemon for tests (mock state/config)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**: Wave 4, can run with T14, T16.

  **QA Scenarios**:
  ```
  Scenario: test_api.py has >5 test functions
    Tool: Bash
    Steps:
      1. uv run pytest tests/test_api.py --collect-only -q | wc -l
    Expected Result: >= 5 test items
    Evidence: .sisyphus/evidence/task-15-api-tests.txt
  ```

  **Commit**: YES
  - Message: `test: add HTTP API test coverage`
  - Files: `tests/test_api.py`

- [x] 16. Full test suite + import smoke test

  **What to do**:
  - Run `uv run pytest tests/ -v --tb=short`
  - Fix any remaining failures
  - Import smoke test: all src modules import without error
  - Forbidden pattern check: no bare `"deepseek"` / `"zai"` / `"opencode"` strings outside providers.py

  **Must NOT do**:
  - Do NOT skip failing tests — fix them

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**: Wave 4, depends on T14+T15.

  **QA Scenarios**:
  ```
  Scenario: Full test suite passes
    Tool: Bash
    Steps:
      1. uv run pytest tests/ -q --tb=short 2>&1
    Expected Result: all passed, 0 failures
    Evidence: .sisyphus/evidence/task-16-full-tests.txt

  Scenario: Zero hardcoded provider strings outside providers.py
    Tool: Bash
    Steps:
      1. grep -rP '"(deepseek|zai|opencode)"' src/ --include='*.py' | grep -v providers.py | grep -v '#'
    Expected Result: 0 matches (or only in comments)
    Evidence: .sisyphus/evidence/task-16-dead-strings.txt
  ```

  **Commit**: NO (verification only)

- [x] 17. Dead code audit + cleanup

  **What to do**:
  - Search for any remaining hardcoded provider strings: `grep -r '"deepseek"\|"zai"\|"opencode"' src/ --include='*.py'`
  - Verify all found matches are: in `providers.py`, in comments, or in test fixtures (acceptable)
  - Check for any stale import of removed functions
  - Verify `.env.example` is accurate (remove stale OPENROUTER_API_KEY if still present)
  - Check README.md for stale provider references
  - Verify `~/.heare/provider` file is still read correctly

  **Must NOT do**:
  - Do NOT delete anything without verifying it's truly dead

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**: Wave 4, depends on T16.

  **QA Scenarios**:
  ```
  Scenario: No dead imports or stale strings
    Tool: Bash
    Steps:
      1. grep -r 'build_openrouter_bootstrap\|_or_service\|_or_model' src/ --include='*.py' | grep -v __pycache__
      2. grep -r 'from src.agent.llm.switchable import.*openrouter' src/ --include='*.py'
    Expected Result: All clean
    Evidence: .sisyphus/evidence/task-17-audit.txt
  ```

  **Commit**: YES (if cleanup needed)
  - Message: `chore: remove stale provider references and dead imports`
  - Files: various (as found)

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Verify: `providers.py` exists with all 3 providers, all 4 bugs fixed, no hardcoded provider strings outside providers.py, "add provider = one dataclass entry" holds true.
  Output: `Registry [OK/FAIL] | Bugs [4/4] | Hardcoded strings [0/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run `ruff check src/` + `uv run pytest -q`. Review `switchable.py` for clean generics (no provider-specific if/elif). Check for circular imports.
  Output: `Lint [PASS/FAIL] | Tests [N pass/N fail] | Generics [CLEAN/N] | VERDICT`

- [x] F3. **Real QA** — `unspecified-high`
  From clean state: daemon starts with only ZAI_API_KEY. Dashboard toggle cycles all 3. set_provider tool switches correctly. GET /state returns all providers. No keys → clear error message.
  Output: `Scenarios [N/N pass] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  Per-task diff review. Verify 1:1 spec-to-code. Check "Must NOT do" compliance. Confirm adding a new provider is truly single-file.
  Output: `Tasks [N/N compliant] | Regression [CLEAN/N] | VERDICT`

---

## Commit Strategy

| Wave | Tasks | Pattern |
|------|-------|---------|
| 1 | T1-T2 | `feat(providers): ...` — new registry file |
| 2 | T3-T8 | `refactor|fix(core): ...` — per-component refactor + bug fixes |
| 3 | T9-T13 | `refactor|fix(watch|tools): ...` — dashboard + tool layer |
| 4 | T14-T17 | `test: ...` — test updates + new coverage |

---

## Success Criteria

### Verification Commands
```bash
# Registry works
uv run python -c "from src.agent.llm.providers import PROVIDERS; print(list(PROVIDERS.keys()))"
# Expected: ['deepseek', 'zai', 'opencode']

# All imports work
uv run python -c "from src.agent.llm.switchable import SwitchableLLMService; print('OK')"

# No hardcoded provider strings outside providers.py
grep -rn '"(deepseek|zai|opencode)"' src/ --include='*.py' | grep -v providers.py | grep -v '#'

# All tests pass
uv run pytest tests/ -q --tb=short

# Daemon starts without DEEPSEEK_API_KEY
ZAI_API_KEY=test uv run python -c "from src.pipeline.build import build_pipeline; print('guard lifted')"
```

### Final Checklist
- [ ] `providers.py` exists with all 3 provider configs
- [ ] Adding a new provider = add dataclass entry + env var — zero other file changes
- [ ] All 4 bugs fixed (api.py, watch/data.py, main.py guard, main.py identity)
- [ ] `switchable.py` has zero provider-specific if/elif chains
- [ ] All tests pass (1022+)
- [ ] Dashboard toggle works for all 3 providers
- [ ] `set_provider` tool schema auto-generates from registry
- [ ] Daemon starts with any single provider key (not just deepseek)

---

## Appendix: What "Adding a Provider" Looks Like After

```python
# ===== BEFORE: 27 files to edit =====

# ===== AFTER: 2 files =====

# 1. providers.py — one dataclass entry:
PROVIDERS["openrouter"] = ProviderConfig(
    key="openrouter",
    display_name="OpenRouter",
    api_key_attr="openrouter_api_key",
    api_key_env="OPENROUTER_API_KEY",
    base_url="https://openrouter.ai/api/v1",
    default_model="google/gemini-3.1-flash-lite-preview-20260303",
    api_style="openai",
    service_factory=make_openai_service,
    dashboard_color="blue",
    model_whitelist=(
        "google/gemini-3.1-flash-lite-preview-20260303",
        "google/gemini-3.1-flash",
        "anthropic/claude-sonnet-4-6",
    ),
    pricing=(
        ("google/gemini-3.1-flash-lite", 0.075, 0.30),
        ("google/gemini-3.1-flash", 0.15, 0.60),
        ("anthropic/claude-sonnet-4-6", 3.00, 15.00),
    ),
    identity_endpoint="https://openrouter.ai/api/v1/chat/completions",
)

# 2. config.py — one Settings field:
openrouter_api_key: str | None = None

# 3. .env.example — one line:
OPENROUTER_API_KEY=

# DONE. CLI, dashboard, tools, API, switchable — all auto-discover it.
```


