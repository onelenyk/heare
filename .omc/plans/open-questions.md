# Open Questions

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
