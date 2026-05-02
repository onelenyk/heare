# Open Questions

## proactive-agent-capability-discovery - 2026-05-01

- [ ] **Sandbox tier for v1** — Is "best-effort process-level sandbox + structural safety classifier" acceptable for v1 launch, or is a real syscall-level sandbox (seccomp/landlock or container isolation) a launch blocker? — Drives whether v1 is ~1 week of work or ~3-4 weeks. Decision needed before Step 4.
- [ ] **Per-session codegen-unlock phrase** — What exact verbal phrase unlocks codegen for a session? Suggested default: *"you can build tools for this session"* with Ukrainian/Russian equivalents. Need user-approved canonical wording before Step 4. The `proactivity_level = "high"` config flag already exists; should that alone unlock codegen, or is a verbal handshake always required?
- [ ] **Audible announcement of generated skills** — When the agent successfully writes and executes a new skill, should it say so out loud (*"I wrote a small tool to do that"*)? Voice UX argues silent + audit-on-demand via `list_generated_skills`; trust UX argues announce. Decision affects Step 4 user-facing copy.
- [ ] **Generated-skill ceiling** — Hard cap on cumulative generated-skill count per user? 100? 1000? Unbounded with prune-by-age (default 30d) only? Decision affects Step 5 lifecycle implementation.
- [ ] **Provider routing for codegen** — Codegen quality is LLM-sensitive. Should `propose_skill` invocations route through z.ai Claude specifically (higher reliability, slower, more expensive), or stay on Gemini 3.1 Flash via OpenRouter (faster, cheaper, lower codegen reliability)? Decision needed before Step 4.
- [ ] **Network allowlist grant flow** — How does a user grant a generated skill permission to hit a new external host (e.g. `api.exchangerate.host`)? Voice prompt at codegen time? Manual edit of SKILL.md frontmatter? Auto-allow with audit log? Affects Step 4 sandbox + Step 5 audit surface.

## conversation-memory - 2026-04-16

- [x] **Unmeasurable acceptance criteria** — FIXED: Converted to automated tests with measurable assertions (time.time() measurements, decider prompt content verification)
- [x] **SmartTurn V3 conflict** — FIXED: Added "SmartTurn V3 Integration Strategy" section explaining complementary operation (micro-pauses vs conversation turns)
- [x] **Missing API reduction verification** — FIXED: Added detailed verification methodology with SQL queries and step-by-step measurement plan
- [x] **TurnAggregator implementation gap** — FIXED: Added complete pseudocode with buffer management, timestamp preservation, mode change handling, memory limits
- [x] **Latency regression hand-wavy** — FIXED: Added honest tradeoff discussion (1.0s → 3.0s-4.0s in ambient mode) with acceptance rationale
- [x] **Database FK direction** — FIXED: Removed incorrect `FOREIGN KEY(session_id) REFERENCES transcripts(id)` line
- [x] **Options 2/3 underdeveloped** — FIXED: Fleshed out with concrete implementation details and use cases where they shine
- [x] **Missing conversation lifecycle** — FIXED: Added "Conversation Lifecycle Management" section covering start/end conditions
- [x] **Missing state synchronization** — FIXED: Added "State Synchronization with FSM" section
- [x] **API cost model acknowledgment** — FIXED: Clarified Groq STT (unavoidable) vs Claude decider calls (reducible), added net reduction calculation

## owner-detection-rethink - 2026-04-15

- [ ] **Should `speak` decisions also require the command keyword, or only `act`?** — Current plan gates `act` only. If Claude returns `speak`, Heare replies to anyone in the room. This matches ambient assistant intent but could be noisy in focus mode. Decision needed before TODO 3.
- [ ] **What is the user's preferred custom command keyword, if any?** — Plan defaults to existing wake words `гава|heare|гей`. User may want a dedicated "action word" like `команда` or a private phrase for security. Decision needed before TODO 2.
- [ ] **Confirmation keyword placement:** must `гава` appear alongside `так`/`ні` (e.g. `"гава так"`), or anywhere in the same turn? — Current plan uses "anywhere in transcript". A stricter variant is `гава + verdict token adjacent`. Decision affects TODO 4 implementation.
- [ ] **Should `DECIDER_DROPPED_NO_KEYWORD` events surface in the watch dashboard?** — New EventKind; a UI hook in `watch.py` would help auditing but is out of scope for this plan. Confirm if needed as a follow-up task.
- [ ] **What happens when `speaker_id_enabled=True` but no owner is enrolled?** — Today the pipeline logs a warning and disables speaker-id for the run. Under the new design the keyword gate still works, so Heare remains usable. Confirm this behavior is desired (vs a hard-fail to force enrollment).
- [ ] **Max yes/no word count** — plan picks `≤ 4` words for standalone confirmation. Need to validate against real Ukrainian confirmations like `"добре, давай"` (2 words, yes) vs `"давай, але тільки швидко"` (4 words, arguably yes). Could be surfaced to Claude instead of parsed locally.
- [ ] **`"так, але не зараз"` parser edge case** — Planner's parser validation showed this case returns `"yes"` under the proposed v4 rules (4 words, YES head, mid-negation buffered by `але`). Semantically ambiguous ("yes but not now" → could mean "yes, later" or "no, not right now"). If real-world usage surfaces this as a problem, add `r"\bале\s+не\b"` alternation to `_NEGATION_TAIL`. Tracking because parser is load-bearing for action confirmation.

## runtime-llm-provider-switching - 2026-05-01

- [ ] **z.ai base URL confirmation** -- The default `https://api.z.ai/v1` is a placeholder. Need to verify the actual z.ai API base URL before implementation. If z.ai docs are not publicly available, the user must provide it. Decision needed before Step 2.
- [ ] **z.ai default model name** -- `z1-mini` is a guess. The correct default model identifier must be confirmed from z.ai documentation or the user's z.ai dashboard. Decision needed before Step 2.
- [ ] **Thread safety of client swap** -- `_apply_provider()` mutates `self._client` and `self._settings.model` which are read by the base class. In the pipecat pipeline, `_process_context` runs in the event loop so there is no true concurrent access to the same service instance. Verify this assumption holds for all code paths (especially `run_inference` which is used by speaker_namer -- but speaker_namer uses its own HTTP client, not this service). Low risk but worth confirming.
- [ ] **Provider switching for `identity.py` and `speaker_namer.py`** -- These use raw `httpx` calls to OpenRouter. Currently out of scope. If the user later wants these to also route through z.ai, that would be a separate plan. Confirm this scoping is acceptable.
- [ ] **Automatic failover between providers** -- Current plan is explicit switching only (no retry on the other provider if one fails). If the user wants automatic failover, that is future work. Confirm this is acceptable for v1.

## claude-agent-sdk-integration - 2026-04-16

- [ ] **SDK computer-tool identifier** — The exact `allowed_tools` string for bash/computer tools must be verified against the installed `claude-agent-sdk` version. Fallback: `["Bash"]` only, with an explicit `SETTINGS.agent_sdk_allowed_tools` override. Decision needed during Story 3.
- [ ] **`SDKResultMessage.session_id` attribute name** — Need to confirm `session_id` vs `sessionId` on the installed SDK version. Keep attribute access in a single helper so a fix is a one-line change. Decision needed during Story 3.
- [ ] **Stale-session exception classification** — The SDK may raise a typed exception or a generic `RuntimeError` carrying the CLI's stderr. Start with a substring match (parity with subprocess path); upgrade to typed-class match once the SDK's exception surface is known. Decision needed during Story 3.
- [ ] **Decider tool isolation** — Plan relies on prompt-level "no tools" language to keep decider ticks text-only. If this proves leaky (model calls `Bash` on a decider tick), fallback is two `ClaudeSDKClient` instances (one per regime) at double the persistent-process footprint. Track during rollout validation in Story 6.
- [ ] **Tests without the SDK installed** — Preferred approach: pure mock harness in `tests/test_agent_sdk_cli.py` so CI does not require `claude-agent-sdk`. Alternative: `pytest.importorskip("claude_agent_sdk")`. Decision needed before Story 5 tests land.
- [ ] **Rate-limiter under-counting** — If the SDK performs internal retries, the external `RateLimiter` may under-count actual API calls. Document and defer to a later tuning pass; not a blocker for rollout.
- [ ] **When to flip `use_agent_sdk` default to `True`** — Plan leaves the default `False` after landing. Criteria for promotion: ≥1 week of dev-machine usage with no regressions, measured decider latency drop ≥300ms/call, no orphaned Node processes on shutdown. Confirm thresholds with user before cutover.
- [ ] **When to delete the subprocess `ClaudeCLI` path** — After promotion, a follow-up cleanup removes `src/claude_cli.py` and `tests/test_claude_cli.py`. Need explicit user sign-off before that deletion lands — some users may prefer keeping both paths as a kill-switch.
- [ ] **`compact_if_needed` limitation in SDK path** — `ClaudeCLI` detects context-limit errors in stderr and calls `claude --compact`. The SDK path has no equivalent: there is no stderr stream and no `--compact` flag. Current posture is to let the SDK handle context management internally (if it does at all). If long sessions hit context limits, fallback options are: (a) detect the SDK's equivalent signal and reconnect with a fresh session, (b) expose a `heare compact` CLI subcommand that calls the subprocess path once. Decision needed before the SDK path is promoted to default.
- [ ] **Persistent-process leak verification** — The SDK backend keeps a Node.js process alive for the daemon's lifetime. On SIGTERM the `async with _backend` block calls `AgentSDKCLI.__aexit__` → `ClaudeSDKClient.__aexit__`. Need to confirm the Node process actually exits (check `ps aux | grep claude`) and does not become a zombie. Add to the ≥1-week dev-machine rollout checklist. If leaks are observed, add a SIGKILL fallback in `_close_client`.

## indication - 2026-04-24

- [ ] **`MODE_CHANGED` on initial boot** — Should it fire when the daemon boots into a non-default mode (first read of `~/.heare/mode`)? — Affects Phase C wiring; default proposed: NO (only on transitions during a single daemon lifetime).
- [ ] **Notification grouping** — `osascript` lacks group/thread ids; should we accept text-only or add `terminal-notifier` as an optional dep for thread grouping? — Affects future-spam UX; default proposed: text-only for v1.
- [ ] **Visual panel staleness** — `watch.py` indication panel: always last 6 entries vs auto-clear after N minutes? — Affects watch.py UX; default proposed: always last 6.
- [ ] **`WAKE_WORD_DETECTED` scope** — FOCUS only or also AMBIENT? Spec says "in focus mode" but ambient might also benefit. — Affects Phase C wiring; default proposed: FOCUS only.
- [ ] **`INTENT_SUBMITTED` for sub-second tools** — Should it fire for direct/simple tools where it's just dashboard noise? — Affects perceived spam; default proposed: yes, but `info` defaults sound=false / notification=false so it only shows in visual.

## mcp-simplification - 2026-04-23

- [ ] **`.mcp.json` description field convention** — Option A adds an optional `description` field to `.mcp.json` entries for Ukrainian prompt injection. This is a heare-specific extension to the standard `.mcp.json` format. Should the field be `description`, `description_uk`, or both? If both, which takes priority for the Ukrainian prompt? Decision needed during Step 1 implementation.
- [ ] **Prompt quality without curated descriptions** — The bundled catalog had hand-written Ukrainian descriptions (e.g., "Сторінки та бази даних Notion"). After deletion, descriptions come from `.mcp.json` or fall back to the server key name (e.g., "notion"). Verify with the user whether the prompt quality regression is acceptable or if a mapping of common server names to Ukrainian descriptions should be hardcoded in `mcp_utils.py`.
- [ ] **`_ensure_workspace_mcp` seeding scope** — Currently seeds from `~/.claude.json`. After the simplification, `.mcp.json` becomes the sole source of truth. Should seeding also copy any servers from the deprecated `enable_mcp_servers` + `mcp_catalog.json` into `.mcp.json` as a one-time migration? Or is the deprecation warning sufficient?

## i18n-foundation (PRD A) - 2026-04-25

- [x] **`uk-UA-OstapNeural` vs `uk-UA-PolinaNeural`** -- RESOLVED: Pinned `uk-UA-OstapNeural` (male voice matches the existing 'Krak' persona vibe better than PolinaNeural). Executor should still verify existence with `edge-tts --list-voices | grep uk-UA` but the decision is made.
- [ ] **`frame.result.language` reliability** -- The plan reads detected language from the raw Whisper API response stored in `TranscriptionFrame.result`. This is an undocumented Pipecat internal. Confirmed: `base_stt.py:274` passes `result=response` to `TranscriptionFrame`. Whisper returns full language names (`"english"`, `"ukrainian"`), NOT ISO codes. `WHISPER_NAME_TO_ISO` normalization map handles conversion. Requires `include_prob_metrics=True` for `verbose_json` format. Executor should still verify the attribute path with a live test during US-I18N-03.
- [x] **Confirmation flow phrases in `tts_phrases.py`** -- RESOLVED: Confirmed `src/decider.py` lines 932 and 981 actively use `"Скажи пароль, або гава ні"` and `"Скажи: гава так, або гава ні"`. Plan now keeps these in `FIXED_PHRASES` with `# DO NOT REMOVE` comment. Localization deferred to PRD C.
- [ ] **English "stop" cancel false positives** -- "stop" is included in the English cancel patterns. It is a common word in programming contexts ("stop the server", "stop the process") where the user genuinely wants an action, not a cancellation. Mitigation: cancel only fires when a pending intent exists. Monitor during live smoke test. If false positives appear, remove "stop" from the English cancel set and keep only "cancel|abort|nevermind|never mind". Decision deferred to live testing.
- [x] **`groq_language="auto"` passed as `language=None` to Pipecat** -- RESOLVED: This approach will CRASH. `GroqSTTService._transcribe()` (groq/stt.py:122) has `assert self._settings.language is not None`. Plan revised: default `groq_language` changed from `"auto"` to `"en"`. A valid `Language` enum is always passed as a prior hint. Whisper still auto-detects via `verbose_json` response (`include_prob_metrics=True`); the hint biases the prior but does not override detection.

## decider-on-openrouter (v2: topic extraction) - 2026-04-25

- [ ] **Which OpenRouter model for topic extraction?** — Plan defaults to `google/gemini-2.0-flash-exp:free` (zero cost, fast). May want `google/gemini-3.1-flash-lite-preview-20260303` (same as generator) for consistency. Decision needed before Story 3 config, but easy to change later.
- [x] **Should `response_format: {"type": "json_object"}` be used?** — RESOLVED: No. The prompt instructs the model to return a bare JSON array; defensive parsing (`_extract_first_json_array()`) handles malformed output. Skipping `response_format` avoids the top-level-object requirement that some providers enforce in JSON-object mode.

## zai-anthropic-full-support - 2026-05-01 (revised v2)

- [ ] **Indication kind for fallback** — Do we need a new `IndicationKind.LLM_FALLBACK`, or is overloading `STT_ERROR` acceptable for the user-visible cue when z.ai falls back to OpenRouter? Affects Step 4 + observability test O4.
- [x] **`LLMService` base abstract surface** — RESOLVED (v2 Major #4): Event handlers on the wrapper fire via inherited `FrameProcessor._call_event_handler`. Delegate-internal events relay through the wrapper's `push_frame` which triggers `on_before_push_frame` / `on_after_push_frame`. External consumers attach to the wrapper, not delegates. No fan-out needed.
- [x] **Boot smoke test cost** — RESOLVED (v2 Major #3): Validate API key shape locally at boot (zero cost). Live validation deferred to first actual turn.
- [x] **Per-turn metric tagging** — RESOLVED (v2): Use `set_core_metrics_data(MetricsData(processor=..., model=f"{provider}:{model}"))` before each turn delegation. Pipecat's existing metrics infrastructure picks up the tag.
- [x] **`_sync_provider` call frequency** — RESOLVED (v2 Major #5): Called only on turn-start frames (`LLMContextFrame`/`LLMMessagesFrame`) when `_turn_in_flight` is False. NOT called per-frame.
- [ ] **Frame relay method: monkey-patch vs link manipulation** — Should the `push_frame` relay use monkey-patching (current plan) or direct `_next`/`_prev` link manipulation? Monkey-patching is more explicit but couples to method signatures. Link manipulation is simpler but changes delegate internal state. Defer decision to executor based on test results.

## conversation-core-wireup - 2026-04-25

- [ ] **US-WU-03: Delete CCS-03 dead code (Option A) or build minimal AWAITING_CONFIRMATION flow (Option B)?** — Plan recommends A. Architect/Critic to confirm. Affects ~200 LOC commitment if B is chosen.
- [ ] **US-WU-04: Are there `IndicationKind.HEARTBEAT_TICK` consumers in `src/indication_backends/` or external dashboards?** — Determines whether the enum value is removed entirely or kept dormant alongside `CONFIRMATION_DEADLINE`.
- [ ] **US-WU-05: Are sibling decider-tests (`test_audio`, `test_feature_flags`, `test_mode_hot_reload`, `test_silent_timeout`, `test_stranger_integration`, `test_yes_no`) testing live `GeneratorProcessor` behaviour via `create_decider_processor` shim, or are they dormant tests of dormant code?** — Determines whether each is migrated to `create_generator_processor` or deleted in a follow-up bulk pass. Blocks decision on whether `src/decider.py` can be deleted entirely (instead of partially).
- [ ] **US-WU-01: Should the `[INTENT CANCELLED id=N]` log line format be preserved exactly for downstream log scrapers, or is reformat acceptable?** — One-line diff either way; needs ops sign-off before merging the live cancel-path swap.
- [ ] **US-WU-02: After moving `_is_standalone_cancel_imperative` into `src/language.py`, should `check_cancel` (regex keyword match) be deleted or kept as a back-compat alias?** — Affects API surface of `src/language.py`. Plan currently recommends delete if no remaining callers.
