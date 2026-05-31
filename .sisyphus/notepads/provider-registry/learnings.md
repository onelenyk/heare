# Provider Registry — Learnings

## Data Sources Verified
- `src/config.py:274-288` — Settings fields for api keys, base URLs, models, timeout
- `src/agent/llm/pricing.py:18-30` — LLM price catalog (USD per 1M tokens)
- `src/watch/models.py:17-51` — Model whitelists per provider
- `src/watch/widgets.py:55-59` — Dashboard color mapping (zai=cyan, opencode=green, default=yellow)
- `src/agent/identity.py:125-187` — Identity bootstrap endpoint (DeepSeek only, hardcoded URL)
- `src/agent/llm/switchable.py:74-168` — Constructor params and provider routing logic
- `src/config.py:440-442` — Env var loading: ZAI_API_KEY, OPENCODE_API_KEY, DEEPSEEK_API_KEY

## Discoveries
- **Pricing gap**: DeepSeek and OpenCode Go models are NOT in the current `pricing.py` catalog. Only Google Gemini and Anthropic Claude models have prices listed.
- **Model ID inconsistency**: `claude-haiku-4.5` (models.py, dot notation) vs `claude-haiku-4-5` (pricing.py, dash notation) — these are different IDs. Whitelist uses `models.py` version; pricing matches `pricing.py` version exactly.
- **Identity bootstrap asymmetry**: Only `build_deepseek_bootstrap()` exists in identity.py. Z.AI and OpenCode Go have no bootstrap functions yet. This file sets up identity endpoints for future use.
- **Z.AI identity endpoint**: Derived as `{base_url}/v1/messages` = `https://api.z.ai/api/anthropic/v1/messages` following Anthropic Messages API convention.
- **SwitchableLLMService pattern**: Uses composition over inheritance — holds 3 Pipecat delegate services, patches their push_frame/broadcast_frame for relay, turn-gated switching on LLMContextFrame.
- **Provider routing**: `~/.heare/provider` file is the source of truth; `_sync_provider()` reads lazily at turn start only.

## File Created
- `src/agent/llm/providers.py` (166 lines) — zero external deps, standard lib only

## T2: Factory Functions Added
- **make_openai_service(config, api_key, **kwargs)**: Builds OpenAILLMService from ProviderConfig. Deferred pipecat import. Tested with DeepSeek + OpenCode configs.
- **make_anthropic_service(config, api_key)**: Builds AnthropicLLMService with AsyncAnthropic client pointing to config.base_url. Deferred imports (anthropic + pipecat). Tested with ZAI config.
- **make_identity_bootstrap(config, api_key, model, timeout)**: Returns async callable. Auto-routes to OpenAI-style (`/chat/completions`) or Anthropic-style (`/messages`) based on `config.api_style`. Deferred imports (json, httpx). Tested with all 3 providers.
- **Deferred imports verified**: Module-level import does NOT trigger pipecat/httpx/anthropic → admin CLI safe.
- **__all__ updated**: Added make_openai_service, make_anthropic_service, make_identity_bootstrap.
- **Tests**: 714 passed, 1 pre-existing failure (test_language_state_file_unmodified). No regressions.

## T3: SwitchableLLMService Refactored to Data-Driven
- **Removed hardcoded individual service fields** (`_zai_service`, `_oc_service`, `_deepseek_service`) — replaced with `self._delegates: dict[str, LLMService]` populated by iterating PROVIDERS.
- **Removed individual model attrs** (`_zai_model`, `_oc_model`, `_deepseek_model`) — model resolved from `PROVIDERS[key].default_model` in metrics tag and factory functions.
- **Generalized `_zai_disabled`** → `_disabled_providers: set[str]` — any provider can now be disabled on failure, not just z.ai.
- **Backward-compat properties**: `_zai_service`, `_oc_service`, `_deepseek_service`, `_zai_disabled` kept as properties reading from `_delegates` / `_disabled_providers` so existing tests (which access private attrs) pass without modification.
- **Constructor uses `locals()`** to look up api_key and model params dynamically from PROVIDERS keys:
  ```python
  for key, cfg in PROVIDERS.items():
      api_key = locals().get(cfg.api_key_attr)
      model = locals().get(f"{key}_model", cfg.default_model)
  ```
- **Factory dispatch**: `cfg.api_style == "anthropic"` → `make_anthropic_service()`, else → `make_openai_service()`.
- **`_handle_provider_failure`** is now generic — disables `self._active_provider` and falls back to `_first_available_provider()` instead of hardcoding deepseek.
- **Metrics tag**: `f"{cfg.key}:{cfg.default_model}"` from PROVIDERS registry — zero hardcoded provider strings.
- **`_ensure_delegate_started` key dispatch**: Uses `_provider_for_delegate()` instead of hardcoded `"zai"/"oc"/"ds"` short strings.
- **Removed imports**: `AnthropicLLMService`, `OpenAILLMService`, `AsyncAnthropic` — all now handled by factory functions in providers.py.
- **Tests**: 15/15 passed (test_switchable_llm.py + test_switchable_llm_observability.py), 0 regressions.

## F3: Real Manual QA — Provider Registry Verification

**Date**: 2026-05-31

### Scenarios Verified

1. **Registry import**: `all_keys()` → `['deepseek', 'zai', 'opencode']` ✅
2. **CLI choices**: `uv run python -m src.main provider --help` shows all 3 providers in argparse choices ✅
3. **Multi-provider SwitchableLLM**: 3 delegates constructed with all 3 keys, active=deepseek ✅
4. **Guard lifted**: SwitchableLLMService constructs with only ZAI_API_KEY (no DEEPSEEK_API_KEY) → 1 delegate (zai), no error ✅
5. **API _available_providers**: get_available works for all 3 / deepseek only / none mock configs ✅
6. **Tool schema enum**: `set_provider` schema uses `all_keys()` → `['deepseek', 'zai', 'opencode']` ✅
7. **Dashboard models**: models_for_provider returns from PROVIDERS registry for all 3 providers ✅
8. **Test suite**: `tests/test_api.py` — 23/23 passing ✅
9. **Pricing dict**: All registry pricing entries present in `_LLM_PRICES_USD_PER_1M` ✅

### Verdict: APPROVE
All 9/9 scenarios passed. Provider registry is fully functional.

### Notes
- Schema variable is `_TOOL_SPECS`, not `TOOL_SCHEMAS`
- Original pricing.py has extra Gemini models not in current registry (from the openrouter era — expected, not a bug)
- `set_provider` enum is resolved at import time via `all_keys()` call, so it's dynamic
