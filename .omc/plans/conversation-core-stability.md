# Conversation Core Stability — 6-Story PRD Bundle

> Status: Draft for Architect/Critic review (Iteration 2 — applied Critic ITERATE feedback)
> Owner: Planner
> Mode: SHORT (no auth/security/migration/PII/destructive surface) with a SCOPED pre-mortem confined to Story 5
> Bundle: US-CCS-01 → US-CCS-04 → US-CCS-05a → US-CCS-05b

---

## Section A — RALPLAN-DR Summary

### Principles

1. **In-memory state must be reconstructable from disk — within a defined freshness window.** Anything held only in `deque`, `dict`, or task-local variables must have a SQLite mirror or be explicitly marked ephemeral. Daemon restart is a normal event, not a fault. *Exemption:* the `_active_task` / `_active_intent` references in `ActionWorker` (Story 5) are by design NOT mirrored to SQLite — they cannot survive process death (the OS already killed the task), so persisting them would be a lie. They are explicitly ephemeral.
2. **The user must always be able to override the agent — observably and end-to-end.** "Stop", "cancel", "відміни", "отмени" are first-class signals. Their effect must be observable end-to-end (utterance → cancel decision → task cancellation → user feedback) within ~500ms wall-clock when an action is in-flight. "Observable" means a `RecordingBackend` test in `tests/test_indication.py` style can assert the cancellation trace from a stop-word transcript to an `INTENT_CANCELLED` indication firing.
3. **The interface should be honest about time.** Hidden countdowns are bugs. If the agent is about to time out a confirmation, the user must hear/see it before it happens.
4. **Reach for the prompt before reaching for code.** When the LLM can be guided by prompt rules + a richer `recent_actions` projection, prefer that over a new tool, new state, new schema, or a new dispatch path.
5. **Each story ships independently.** No story depends on a later story to be useful. Sequencing is for *user value compounding*, not for unblocking.

### Decision Drivers (in priority order)

1. **User trust under failure** — restart resilience (Story 1), cancel that actually cancels (Stories 5a + 5b), no silent timeouts (Story 3). Priority unchanged from Iteration 1: user trust still wins.
2. **Conversational fidelity** — addressable references (Story 2), refinement vs. new search (Story 4). Closes the gap between "the LLM heard you" and "the LLM did what you meant".
3. **Low LOC, low blast radius** — prefer prompt-only fixes; add structured fields only where the LLM can't reliably parse free text; SQL changes localized to one table per story.

### Viable Options

#### Story 1 — Persist action log

| Option | Pros | Cons | Verdict |
| --- | --- | --- | --- |
| **A. Reuse existing `actions` table + `decisions` row.** Add `tool`, `args`, `result_json` columns; rebuild the in-memory deque on startup via a `SELECT … ORDER BY ts DESC LIMIT 16` filtered by `since_ts`. | Single source of truth; `actions.decision_id` already correlates to the trigger. Smallest schema delta. Freshness filter prevents stale-context pollution. | Requires `ALTER TABLE`. Must back-fill `tool`/`args` via JOIN to `decisions.intent_json` for old rows or accept NULL on reads. | **Chosen.** |
| B. New `action_log` table separate from `actions`. | No `ALTER TABLE`; clean schema. | Two tables tracking the same thing. Drift risk. Twice the write path. | Rejected. |
| C. Serialize the deque to a `meta` row as JSON on every mutation. | No DDL. | Loses queryability; full rewrite per record; not how `summary` is persisted (per-conversation row, not blob). | Rejected. |

#### Story 2 — Numbered search results + structured items

| Option | Pros | Cons | Verdict |
| --- | --- | --- | --- |
| **A. Numbered text + structured `items: list[dict]` field on the action-log entry; generator prompt teaches "the second one" / "the vegan one"; `_format_recent_actions` renders FROM `items` when present (legacy `result` blob is fallback only).** | LLM gets both a human string and a machine list. Refinement (Story 4) and Story 1 reuse the same shape. Single render path keyed off presence of `items` keeps the projection size bounded. | Slightly bigger `recent_actions` projection — bounded by an explicit per-entry char cap. | **Chosen.** |
| B. Numbered text only, no structured items. | Trivial diff. | LLM has to re-parse "1.", "2." each turn — fragile across STT noise like "the second one". | Rejected. |
| C. Per-result IDs (e.g. URL hashes) the LLM cites. | Most precise. | Overkill — the LLM does fine with 1..N when N≤5. | Rejected as premature. |

#### Story 3 — Confirmation deadline cue

| Option | Pros | Cons | Verdict |
| --- | --- | --- | --- |
| **A. Schedule a second `asyncio.create_task` at `confirmation_timeout_seconds − N` that fires `CONFIRMATION_DEADLINE` then awaits the remaining N seconds before letting `_timeout_watcher` cancel. Re-check guard immediately before notifying.** | Smallest diff to existing `_schedule_timeout_task`. Reuses indication facade verbatim. Guard re-check closes the confirm-during-warning race. | Two tasks to cancel on confirm/cancel — must clean up both. | **Chosen.** |
| B. Replace `_timeout_watcher` with a single coroutine that sleeps `T-N`, fires cue, sleeps `N`, then cancels. | One task to cancel. | Bigger refactor of an already-working watcher; harder to test cue independently. | Rejected. |
| C. Tick every second from `_timeout_watcher` and fire cue when remaining ≤ N. | Most flexible. | Wastes wakeups; cooldown coalescing already prevents repeat cues. | Rejected. |

#### Story 4 — Refine vs. new search disambiguation

| Option | Pros | Cons | Verdict |
| --- | --- | --- | --- |
| **A. Prompt-only with recency guard.** New rule + 2 examples in `prompts/generator.txt`: "if recent_actions contains a recent successful `web_search` AND its timestamp is within the last `refinement_recency_seconds` (default 600s) AND the user uses qualifier words THEN emit a new `web_search` intent whose args = combined query". | Zero new code paths. Touches one prompt + golden fixture. Rolls back in seconds. Recency guard prevents day-old searches from being "refined". | Reliability depends on the model. Mitigated by an explicit example pair per language and by Story 2's structured `items` giving the LLM the prior query verbatim. | **Chosen.** |
| B. Pseudo-tool `refine_last_search(refinement: str)` registered in tool_registry. | Deterministic. | Adds a tool, an allowlist entry, a router special-case, and an LLM training burden. Overkill for a 1-prompt-rule problem. | Rejected — pick the lighter-weight option. |

> **Justification for picking A:** the user explicitly asked for the lighter-weight option that the LLM can reliably trigger. Story 1 + Story 2 ship the structured prior query *and its timestamp* to the prompt, which is exactly what makes prompt-only refinement reliable.

#### Story 5a — Cancel signal origin (decider fast-path)

| Option | Pros | Cons | Verdict |
| --- | --- | --- | --- |
| **A. Decider-layer stop-word detection BEFORE generator invocation; on match, decider directly enqueues a cancel intent (or calls `IntentQueue.cancel_active()`) without waiting for an LLM round-trip. Stop-words must be standalone imperatives (single-clause, ≤4 words, optional politeness markers).** | Cuts latency from "stop"-utterance to cancellation by ~1-2s (no LLM round-trip). LLM stays out of the hot path. Standalone-imperative rule prevents "don't stop the recording" false positives. | New code path in decider. Stop-word vocabulary needs to be configurable. | **Chosen.** |
| B. Generator-emitted cancel intent (Iteration 1's design). | Single trigger source. | Adds an LLM round-trip on every stop-word, defeating the latency principle. | Rejected after Iteration 1 critique. |
| C. STT-layer wake-word detector. | Lowest latency. | Bypasses any disambiguation; "stop talking about chili" mid-sentence cancels real work. | Rejected. |

#### Story 5b — Kill paths (mechanics)

| Option | Pros | Cons | Verdict |
| --- | --- | --- | --- |
| **A. Per-backend kill paths: bash subprocess SIGTERM → 2s grace → SIGKILL via process group (`os.killpg`); httpx via `asyncio.Task.cancel()`; Claude Agent SDK best-effort cancel; TTS frame drop with 50ms fade-out on the currently-speaking frame.** | Each backend uses its native cancel idiom. Process-group kill prevents orphan child processes. Fade-out prevents audible click on TTS drop. | Several integration points. TTS fade-out is new code in the pipeline. | **Chosen.** |
| B. Cancel only the queue + TTS, leave subprocess to finish. | Trivial. | Doesn't solve the user's primary complaint. | Rejected. |

### Mode

**SHORT** with a **SCOPED pre-mortem** confined to Story 5 (5a + 5b). No auth, no PII, no destructive migration, no public-facing surface. Story 1's `ALTER TABLE` is additive (new nullable columns) on a local SQLite file — reversible by deletion of the .db. Stories 2/3/4 use standard unit + integration coverage; Story 5's pre-mortem is below in the Story 5a + 5b sections.

---

## Section B — PRD Body

### US-CCS-01 — Persist Action Log to SQLite

**Description.** Replace the process-local `collections.deque(maxlen=16)` in `ConversationManager._action_log` with a SQLite-backed projection. On daemon startup, rebuild the in-memory deque from the last 16 rows **filtered to a freshness window** (`since_ts = now - settings.conversation_idle_seconds`, default 1800s = 30 min, matching the existing conversation idle window). On every `record_action_pending / _result / _error`, write through to SQLite as a tracked task; persist the in-memory entry immediately so reads stay coherent within a turn.

**Files touched.**
- `src/storage.py` — `ALTER TABLE actions ADD COLUMN tool TEXT`, `ADD COLUMN args TEXT`, `ADD COLUMN result_json TEXT`, `ADD COLUMN intent_id INTEGER`. New methods: `upsert_action_log_entry(intent_id, tool, args, status, result, ts) -> None`, `load_recent_action_log(limit=16, since_ts: float | None = None) -> list[dict]`. Idempotent column-addition.
- `src/conversation.py` — `ConversationManager.__init__` accepts `store`; add `async def hydrate_action_log(since_ts: float | None = None) -> None` that runs once at startup and seeds `_action_log`. When `since_ts` is provided, entries with `ts < since_ts` are skipped during deque rehydration. Modify `record_action_pending / _result / _error` to also call `store.upsert_action_log_entry(...)` (fire-and-forget with logged exception swallow).
- `src/main.py` or `src/pipeline.py` (wherever `ConversationManager` is constructed) — call `await conversation_manager.hydrate_action_log(since_ts=time.time() - settings.conversation_idle_seconds)` before the pipeline starts. The `conversation_idle_seconds` default lives in `src/config.py` (`Settings.conversation_idle_seconds: float = 1800.0`).
- `tests/test_conversation.py` — new test class `TestActionLogPersistence`.
- `tests/test_storage.py` — coverage for the new columns + methods.

**Acceptance criteria.**
1. Schema: `PRAGMA table_info(actions)` returns rows for `tool`, `args`, `result_json`, `intent_id` in addition to existing columns.
2. Schema migration is idempotent: starting the daemon twice on an existing DB does not raise; starting on a fresh DB creates the columns from `_SCHEMA_SQL`.
3. `ConversationManager.record_action_pending(7, "web_search", "chili recipe")` followed by `record_action_result(7, "...")` produces exactly one row in `actions` (UPSERT on `intent_id`) with `status='done'`, `tool='web_search'`, `result_json` containing the summary.
4. `ConversationManager.hydrate_action_log()` after a simulated restart restores `recent_actions(limit=5)` to the same 5 newest entries (same `tool`, `status`, `args`, `result_json`) within ±1 entry of pre-restart state.
5. **`hydrate_action_log()` accepts a `since_ts: float | None = None` argument. When provided, entries with `ts < since_ts` are skipped during deque rehydration.**
6. **On daemon startup, the hydration call passes `since_ts = now - settings.conversation_idle_seconds` (default 1800s = 30 min). The default lives in `src/config.py:Settings.conversation_idle_seconds`.**
7. **Test verifies that after writing two `action_log` entries — one timestamped 6 hours ago and one timestamped 5 minutes ago — only the 5-min-old entry is hydrated on startup with the default `since_ts`.**
8. `pytest tests/test_conversation.py::TestActionLogPersistence -q` exits 0.
9. `pytest tests/test_storage.py -q` exits 0.
10. The in-memory deque retains its `maxlen=16` cap; SQLite may hold more rows but `load_recent_action_log(limit=16)` returns at most 16, ordered newest-first.
11. Persistence write failures (e.g. DB locked) log a warning and DO NOT raise into the caller — the in-memory deque update still succeeds.
12. Generator context (`ConversationContext.build()` → `recent_actions` field) is populated correctly on a freshly-restarted daemon, verified by an integration test that kills + restarts the manager.

**Risks / mitigations.**
- *Risk:* `ALTER TABLE` on a populated production DB. *Mitigation:* additive only, all new columns nullable; old rows surface as `tool=None` and are filtered out of the hydration result.
- *Risk:* write amplification. *Mitigation:* one row per intent (UPSERT on `intent_id`).
- *Risk:* **stale-context pollution across the idle boundary** — hydrating a 6-hour-old action log into a fresh conversation surfaces stale "recent actions" to the LLM, polluting the prompt and triggering Story 4's refinement rule on dead queries. *Mitigation:* the `since_ts` filter on `hydrate_action_log()`, defaulting to `now - conversation_idle_seconds` (30 min).

**Dependencies.** None. Foundation for Stories 2 and 4.

---

### US-CCS-02 — Numbered Search Results + Addressable References

**Description.** `_search_serper` and `_search_duckduckgo` currently join hits with `"\n\n"`. Number them `1.`, `2.`, … and additionally return a structured `items: list[dict]` (each `{n, title, url, snippet}`) inside the result dict. Plumb `items` through to the action-log entry. `_format_recent_actions` in `src/context.py` is updated to render web_search/web_fetch entries FROM `items` when present (NOT in addition to the `result` blob); the legacy `result` rendering path is kept as fallback for entries without `items`.

**Files touched.**
- `src/direct_tools.py` — `_search_serper`, `_search_duckduckgo`: build numbered output; return `{success, output, items: [...], error, spoken}`.
- `src/conversation.py` — extend `record_action_result` signature (or add a sibling) to accept an optional `items: list[dict]` and store it on the in-memory entry; persisted to `result_json` (Story 1).
- `src/context.py` — **`_format_recent_actions`** is updated to render web_search/web_fetch entries FROM `items` if present, NOT in addition to `result`. Legacy `result` rendering is the fallback for entries without `items`. The rendered entry has a documented hard cap of **1800 chars total per web_search entry**; when 5 numbered items would exceed, items are truncated **tail-first** with a `... (N more items truncated)` suffix. The existing 1500-char `tail_limit` at `src/context.py:235` is updated to **1800** to align with the new cap (both the constant and the AC must agree). The function docstring documents the items-first rendering and the truncation-tail-first strategy; both are covered by tests.
- `prompts/generator.txt` — add a rule + 2 examples: "If `recent_actions` contains a numbered web_search, the user can reference items by ordinal ('the second one', 'the third', 'друга', 'другий') or by qualifier ('the vegan one', 'веганський'). Read from the list; do NOT issue a new web_search."
- `tests/fixtures/decider_prompt_*.golden.txt` — regen affected goldens.
- `tests/test_direct_tools.py` (or equivalent) — assert numbered output + `items` shape.
- `tests/test_generator_prompt.py` — assert the new rule + examples are present.
- `tests/test_context.py` — assert the items-first render path + truncation-tail-first behavior.

**Acceptance criteria.**
1. `_search_serper("chili recipe", api_key, settings)` returns a dict where `output` matches regex `^1\.\s.+\n\n2\.\s` (numbered) and `items` is a list of length ≥1 with keys `{n, title, url, snippet}`.
2. `_search_duckduckgo("chili recipe", settings)` likewise; `n` starts at 1 and is contiguous.
3. Knowledge-graph entries (Serper `knowledgeGraph`) are inserted at position 0 with `n=0` or labeled as "answer box". The function docstring documents the chosen behavior; a test covers it.
4. `ConversationManager.recent_actions()` for a completed `web_search` includes the `items` list verbatim.
5. **`_format_recent_actions` renders web_search/web_fetch entries FROM `items` if present, NOT in addition to `result`. The legacy `result` rendering path is kept as fallback for entries without `items`.** The function docstring documents the items-first rendering AND the truncation-tail-first strategy; both are covered by tests.
6. **A `web_search` entry with 5 items × 250-char snippets renders to ≤1800 chars total and includes a `(N more items truncated)` suffix when truncation occurred.** A test asserts exactly this.
7. **The `tail_limit` constant in `src/context.py:235` is updated to 1800 (or explicitly documented as the cap for the new path); both the code constant and this AC use the same number — no drift.**
8. Generator prompt contains the literal example text `"the second one"` AND `"the vegan one"` (or non-English equivalents) — verified by a string-search test.
9. `pytest tests/ -q -k "search or generator_prompt or recent_actions"` exits 0.
10. Golden fixtures updated where prompt assembly changed; CI golden-diff check passes.
11. The empty-results path still returns the `"No results found"` `spoken` dict and an empty `items: []`.

**Risks / mitigations.**
- *Risk:* downstream code assumes `output` parses as `\n\n`-separated. *Mitigation:* grep before merge; both call sites are inside `direct_tools.py` and the action worker.
- *Risk:* prompt regression — model emits a new search anyway. *Mitigation:* example pairs in en/uk/ru; validated via `tests/test_generator_prompt.py`.
- *Risk:* the items-first render and the legacy result render diverge in length, breaking the prompt budget. *Mitigation:* the 1800-char hard cap with tail-first truncation applies uniformly to the items-first path; legacy path uses the same 1800 ceiling.

**Dependencies.** Story 1 strongly recommended (so `items` survive restart). Functionally independent — Story 2 ships value with in-memory log only.

---

### US-CCS-03 — Confirmation Deadline Countdown Cue

**Description.** `_timeout_watcher` in `src/decider.py:885` sleeps `confirmation_timeout_seconds` then fires cancel silently. Add a parallel "deadline-warning" task that fires the existing `IndicationKind.CONFIRMATION_DEADLINE` cue N seconds before timeout (default N=5, configurable). Both tasks must be cancellable atomically when the user confirms or cancels. The warning task re-checks a guard flag immediately before notifying to close the confirm-during-warning race.

**Files touched.**
- `src/decider.py` — modify `_schedule_timeout_task` to also schedule `_deadline_warning_watcher`; modify `_cancel_timeout_task` to cancel both. The warning task re-checks `self._timeout_task is not None and not self._timeout_task.cancelled()` immediately before calling `indication.notify(...)`. Read warn-lead-seconds from settings.
- `src/config.py` — add `confirmation_deadline_warning_seconds: float = 5.0` to `Settings`.
- `tests/test_decider.py` — new tests including a fuzz test for the race.
- `prompts/decider.txt` — no change.

**Acceptance criteria.**
1. With `confirmation_timeout_seconds=10` and `confirmation_deadline_warning_seconds=3`, after ARMED entry the indication facade receives exactly one `notify(IndicationKind.CONFIRMATION_DEADLINE, …)` call ~7s in (±0.5s).
2. If the user confirms at t=2s, NO `CONFIRMATION_DEADLINE` cue fires (both tasks cancelled).
3. If the user cancels at t=4s, NO `CONFIRMATION_DEADLINE` cue fires.
4. `confirmation_deadline_warning_seconds=0` disables the cue entirely (no warning task scheduled).
5. `confirmation_deadline_warning_seconds >= confirmation_timeout_seconds` is clamped at startup to `max(0, timeout - 1)` and a warning is logged.
6. **The warning task re-checks `self._timeout_task is not None and not self._timeout_task.cancelled()` immediately before calling `indication.notify(IndicationKind.CONFIRMATION_DEADLINE, ...)`. If the guard fails, the task returns without firing.**
7. **A fuzz test runs 100 iterations where confirm arrives within ±50ms of the warning fire boundary; in 0 of those iterations does a `CONFIRMATION_DEADLINE` notification reach the indication backends. The test uses a `RecordingBackend` (style of `tests/test_indication.py::RecordingBackend`) and asserts the recorded notification list is empty for confirms inside the race window.**
8. `pytest tests/test_decider.py -q -k "deadline or warning or race"` exits 0.
9. Existing `_timeout_watcher` behavior is unchanged on the timeout path: `_cancel_pending("nevermind, cancelled")` still fires when the full `T` elapses.
10. The cue respects the existing indication cooldown / quiet-hours / mode gating — verified by mocking the Indication facade and asserting `notify` (not the backend) was invoked.

**Risks / mitigations.**
- *Risk:* dangling task on lock contention. *Mitigation:* both tasks held in instance fields; `_cancel_timeout_task` cancels both unconditionally.
- *Risk:* user confuses the cue with a real prompt. *Mitigation:* uses existing `_DEFAULTS[CONFIRMATION_DEADLINE]` text "5s left / Confirmation will expire" — already designed for this.
- *Risk:* **confirm-during-warning race** — the warning task wakes up at `T-N`, checks state, then the confirm path cancels both tasks but the warning task already passed the cancel-check and is about to call `notify()`. *Mitigation:* guard-flag re-check immediately before `notify` + the 100-iteration fuzz test in AC#7.

**Dependencies.** None.

---

### US-CCS-04 — Refine vs. New-Search Disambiguation (Prompt-Only with Recency Guard)

**Description.** When the user follows a recent successful `web_search` with a qualifier ("actually for vegan", "but cheaper", "тільки веганський", "только веганский"), AND the prior `web_search` happened within the last `refinement_recency_seconds` (default 600s = 10 min), the generator should emit a new `web_search` intent whose query is `prior_query + ' ' + qualifier_phrase`, not a fresh unrelated search. Implemented as a prompt rule + examples; reliability depends on Stories 1 and 2 surfacing the prior query *and its timestamp* in `recent_actions`.

**Soft dependency note (Section C cross-ref).** Story 4's reliability is now dependent on Story 1's hydration `since_ts` filter — without it, a 6-hour-old `web_search` from a prior session could be hydrated into the action log and trigger refinement on a dead query. The recency check in the prompt rule provides defense-in-depth even if Story 1 ships imperfectly. Story 4 still ships independently because the recency check is local to the prompt rule and the timestamp is already in `recent_actions`.

**Files touched.**
- `prompts/generator.txt` — new rule block "REFINEMENT" + 3 worked examples (en/uk/ru). The rule must explicitly state the recency window in plain language: e.g. "if the prior `web_search` happened more than 10 minutes ago, treat the user's qualifier as a fresh topic and issue a new `web_search` instead of a refinement".
- `src/config.py` — add `refinement_recency_seconds: float = 600.0` to `Settings`.
- `src/context.py` — ensure `recent_actions` projection includes the timestamp of each entry (relative or absolute) so the prompt rule has the data it needs.
- `tests/test_generator_prompt.py` — assert rule + examples + recency-window wording present.
- `tests/integration/test_intent_flow.py` — new tests including the recency-fail case.
- `tests/fixtures/*.golden.txt` — regen.

**Acceptance criteria.**
1. `prompts/generator.txt` contains a section header matching `REFINEMENT` (case-insensitive).
2. The qualifier vocabulary lists at minimum: en `["actually", "but", "instead", "just", "only"]`, uk `["насправді", "але", "тільки", "краще"]`, ru `["вообще-то", "но", "только", "лучше"]`.
3. Three worked examples are present, one per language, each showing prior query + qualifier → combined query.
4. Generator is explicitly told: "Do NOT emit a generic `web_search` if the qualifier is unrelated to the prior topic" — guard rule.
5. **The refinement rule in the generator prompt ALSO requires that the prior `web_search` entry's timestamp is within the last `refinement_recency_seconds` (default 600s = 10 min). The prompt example MUST show the rule including the recency window in plain language (e.g. "within the last 10 minutes").**
6. Integration test: given a fake `recent_actions` with `web_search "chili recipe"` (timestamp 2 min ago) and a user transcript "actually for vegan", the next emitted intent has `tool == "web_search"` AND args contains both `"chili"` and `"vegan"`.
7. Same test in Ukrainian: prior `web_search "рецепт чілі"` (timestamp 2 min ago), transcript "тільки веганський", emitted args contain both terms.
8. **A test verifies that a fake `recent_actions` containing a 30-min-old `web_search` does NOT trigger refinement: the generator issues a fresh `web_search` with the new query, not a combined one.**
9. `pytest tests/test_generator_prompt.py tests/integration/test_intent_flow.py -q` exits 0.
10. Golden fixtures updated; CI golden-diff check passes.
11. Existing rule "Do NOT emit a new `web_search` for the same topic when prior results are already present" remains and is reconciled with the new rule (refinement = different topic-with-modifier within recency window, not same topic).

**Risks / mitigations.**
- *Risk:* model misclassifies a topic-change as refinement. *Mitigation:* explicit guard rule + an example showing a topic change → new search.
- *Risk:* prompt drift across model upgrades. *Mitigation:* integration test pins behavior at the intent-emission level.
- *Risk:* recency window too aggressive (user genuinely wants to refine an 11-min-old search). *Mitigation:* default 600s is configurable; revisit based on telemetry.

**Dependencies.** Stories 1 and 2 (prior query + timestamp must be visible in `recent_actions`). Functionally independent: ships value even without Story 2's structured items, but reliability is lower.

---

### US-CCS-05a — Cancel Signal Origin (Decider Fast-Path)

**Description.** The decider detects stop-words ("stop", "cancel", "halt", "відміни", "отмени", + a configurable list) BEFORE the generator is invoked. On match, the decider directly enqueues a `cancel` intent (or calls `IntentQueue.cancel_active()`) without waiting for an LLM round-trip. Stop-word detection requires the utterance to be a STANDALONE imperative (single-clause, ≤4 words, optional politeness markers like "please" / "будь ласка") to prevent false positives like "don't stop the recording".

**Files touched.**
- `src/decider.py` — new pre-generator stop-word detector. Reads `cancel_stop_words` list from `Settings`. Calls `IntentQueue.cancel_active()` directly on match.
- `src/actions.py` — new method `IntentQueue.cancel_active() -> bool` that drains the queue AND triggers `ActionWorker.cancel_in_flight()` (the latter implemented in Story 5b).
- `src/config.py` — add `cancel_stop_words: list[str] = ["stop", "cancel", "halt", "відміни", "отмени"]` (configurable via env).
- `tests/test_decider.py` — fast-path detection tests, false-positive guard tests.
- `tests/test_actions.py` — `cancel_active()` queue-drain test.

**Acceptance criteria.**
1. Stop-word utterances ("stop", "cancel", "відміни", "отмени", "halt") trigger `IntentQueue.cancel_active()` from the decider BEFORE the generator is invoked.
2. **Wall-clock latency from stop-word transcript to `cancel_active()` invocation is under the next event-loop tick (i.e. no awaits other than the call itself); LLM disambiguation is NOT in the hot path.**
3. False-positive guard: utterances like "don't stop the recording", "I won't stop", "stop is a four-letter word" do NOT trigger cancel. The detector requires standalone imperative form: single clause, ≤4 words, optional politeness markers ("please", "будь ласка", "пожалуйста").
4. The configurable `cancel_stop_words` list can be extended via env var without code changes; a test sets the env and asserts the new word triggers.
5. Stop-word during in-flight TTS or in-flight action triggers cancel; the end-to-end "transcript → `INTENT_CANCELLED` indication" trace is observable in a `RecordingBackend` test within ~500ms wall-clock.
6. `pytest tests/test_decider.py tests/test_actions.py -q -k "cancel or stop_word"` exits 0.
7. Submitting `cancel_active()` on an empty queue with no in-flight action is a no-op (logs debug, fires no indication).

**Risks / mitigations.**
- *Risk:* false positives on natural speech. *Mitigation:* standalone-imperative rule + ≤4-word constraint + curated vocabulary.
- *Risk:* missed cancels on noisy STT ("stahp", "cansel"). *Mitigation:* configurable list; future fuzzy-match revisit if telemetry shows misses.

**Dependencies.** None for the detection logic. Calls into `cancel_active()`; the actual kill paths are Story 5b — but 5a alone improves UX even if 5b only ships partially (queue drain works without subprocess kill).

---

### US-CCS-05b — Kill Paths (Mechanics)

**Description.** Implement the actual cancel mechanics behind `IntentQueue.cancel_active()` / `ActionWorker.cancel_in_flight()`: bash subprocess SIGTERM with 2s grace then SIGKILL via process group; httpx via task cancel; Claude Agent SDK call cancel (best-effort); drop pending TTS frames in the pipeline with a 50ms fade-out on the currently-speaking frame; mark intent as cancelled in the action log.

**Files touched.**
- `src/actions.py` — `ActionWorker._process_one` holds the active task in `self._active_task: asyncio.Task | None` and the active intent in `self._active_intent`. New `async def cancel_in_flight() -> bool`. Marks the intent as cancelled in the action log (Story 1's UPSERT path).
- `src/direct_tools.py` — bash subprocess execution: spawn with `start_new_session=True`; track the `asyncio.subprocess.Process` handle; on cancel, send SIGTERM to the process group via `os.killpg(os.getpgid(proc.pid), SIGTERM)`, wait 2s, then SIGKILL via `os.killpg(...)`. httpx requests cancel cleanly via `asyncio.CancelledError`.
- `src/agent_sdk_cli.py` (or wherever `kill_running_action` lives) — verify SIGTERM/SIGKILL semantics; best-effort cancel.
- `src/pipeline.py` or `src/tts_edge.py` — add a `cancel_pending()` method that drops queued TTS frames AFTER applying a 50ms fade-out to the currently-speaking frame. Followed frames discarded silently.
- `src/tool_registry.py` — register `cancel` tool with no-op SDK mapping (so the action log has a stable `tool="cancel"` entry).
- `tests/test_actions.py` — cancel-in-flight tests for direct + claude paths; orphan-process check.
- `tests/test_direct_tools.py` — bash subprocess kill test (`sleep 60`, cancel, verify exit ≤ 3s); process-group leak test.
- `tests/test_pipeline.py` (or `tests/test_tts_edge.py`) — TTS frame-drop + fade-out test.
- `tests/integration/test_intent_flow.py` — end-to-end "user says stop while web_search in flight" test.

**Acceptance criteria.**
1. **A 60s `bash sleep 60` is killed within 3s of cancel** (2s SIGTERM grace + 1s slack); the process group is terminated, no orphan child processes remain (verified via `pgrep` or equivalent in-test check).
2. **A `web_fetch` of a slow URL is cancelled within 1s of cancel submission**; `CancelledError` propagates and `on_error` is called.
3. **Queued TTS frames stop playing within 500ms of cancel**, with a 50ms fade-out on the currently-speaking frame to prevent audible click/pop.
4. Submitting `cancel` while a Claude SDK call is in flight invokes `kill_running_action()`; on success, an `INTENT_CANCELLED` indication fires.
5. After a successful cancel, `IndicationKind.INTENT_CANCELLED` is fired exactly once.
6. The cancelled intent is marked with `status='cancelled'` in the action log (Story 1's UPSERT path).
7. Bash subprocess is spawned with `start_new_session=True`; cancel signals the process group via `os.killpg`, NOT just the parent. Test asserts no orphan child process after cancel.
8. `pytest tests/test_actions.py tests/test_direct_tools.py tests/test_pipeline.py tests/integration/test_intent_flow.py -q -k "cancel or kill"` exits 0.

**Pre-mortem (SCOPED to Story 5a + 5b).**
- *Failure scenario 1: stop-word false positives.* "Don't stop the recording" cancels real work. *Mitigation:* stop-word detection (Story 5a) requires the utterance to be a STANDALONE imperative — single-clause, ≤4 words, optional politeness markers. False-positive utterances are tested explicitly.
- *Failure scenario 2: SIGKILL leaks file handles / orphan child processes.* A bash one-liner spawns `curl | jq | tee`; SIGKILL on the bash parent leaves curl/jq running. *Mitigation:* spawn with `start_new_session=True` and kill the entire process group with `os.killpg`, not just the parent. Test asserts zero orphan processes after cancel.
- *Failure scenario 3: TTS frame drop creates audible click/pop.* Hard-cutting a frame mid-utterance produces a discontinuity. *Mitigation:* a short fade-out (50ms linear ramp) on the currently-speaking frame before drop; followed frames discarded silently. Test asserts the fade-out is applied (zero-crossing or amplitude check on the final 50ms of the cut frame).

**Risks / mitigations** (summary, expanded above).
- *Risk:* race between cancel submission and action completion. *Mitigation:* `cancel_in_flight()` checks `self._active_task is not None and not self._active_task.done()` under a lightweight lock; returns False if nothing to cancel.
- *Risk:* TTS frame-drop signal blocks pipecat. *Mitigation:* drop is non-blocking; fade-out is applied as a sample-level operation on the in-flight frame, not a coroutine.

**Dependencies.** Story 5a (the cancel signal origin). 5b is the mechanics; 5a is the trigger. 5a alone improves UX even if 5b only kills bash and skips TTS frame drop initially.

---

## Section C — Sequencing & Rollout

| Order | Story | Diff size | Story points | Justification |
| --- | --- | --- | --- | --- |
| 1 | US-CCS-01 (persistence + freshness window) | M | 5 | Foundation. Story 2 stores items here; Story 4 reads prior queries + timestamps from here. Freshness window (`since_ts`) prevents stale-context pollution and is what makes Story 4's recency rule reliable end-to-end. |
| 2 | US-CCS-02 (numbered + structured + bounded render) | M | 3 | Compounds Story 1: `items` survive restart. Unlocks Story 4's reliability. Independently ships "the second one" capability. |
| 3 | US-CCS-03 (deadline cue + race guard) | S | 1 | Trivial change, biggest "oh good" reaction per LOC. Independent. The fuzz test is the load-bearing piece. |
| 4 | US-CCS-04 (refinement prompt rule + recency guard) | S | 2 | Cheapest LOC; biggest value comes after Stories 1+2. Soft-depends on Story 1's `since_ts` filter for end-to-end reliability, but ships independently because the recency check is local to the prompt rule. |
| 5 | US-CCS-05a (cancel signal origin / decider fast-path) | M | 5 | **Highest design-level risk in the bundle.** Pre-generator dispatch path is new; the standalone-imperative rule is the principal mitigation against false positives. 5a alone improves UX even if 5b ships in pieces. |
| 6 | US-CCS-05b (kill paths / mechanics) | L | 8 | Implementation grunt work behind 5a's signal. Touches actions, direct_tools, pipeline, TTS, SDK. Highest LOC, but the design-level risk lives in 5a. Can ship incrementally: bash kill first, TTS fade-out second, SDK kill last. |

**Total:** 24 story points (was 19; Story 5 split + bumped 8 → 13 to reflect added scope from the standalone-imperative detector, recency-coupled hydration, and TTS fade-out). ~2 weeks for one focused executor with verifier review per story.

**Highest-risk story.** US-CCS-05a — the design-level surface (pre-generator dispatch path, false-positive containment via standalone-imperative rule). US-CCS-05b is the implementation grunt work behind it.

**Mitigations** (Stories 5a + 5b combined).
1. Land 5a behind a feature flag `HEARE_CANCEL_FAST_PATH_ENABLED=1` for the first deploy; default off until the standalone-imperative detector is observed in production.
2. Land 5b behind a feature flag `HEARE_CANCEL_KILL_PATHS_ENABLED=1`; default off until subprocess-kill semantics are observed.
3. 5b can ship incrementally: (a) bash kill + queue drain, (b) httpx + Claude SDK kill, (c) TTS frame drop + fade-out. Each independently shippable behind the same flag.
4. Verifier (ai-slop-cleaner + verify skills) must replay the integration test on a real macOS daemon with `sleep 60` before sign-off, and run the false-positive utterance suite for 5a.
5. Add structured logging at every cancel branch (`[CANCEL origin=decider|generator queued=N inflight=tool/id killed_subprocess=bool killed_claude=bool dropped_tts_frames=N fade_out_ms=N]`).

**Soft dependency note.** Story 4's reliability is dependent on Story 1's `since_ts` filter (avoids 6-hour-old searches triggering refinement). Story 4 still ships independently because the recency check is local to the prompt rule; the timestamp is already in `recent_actions`.

**Partial-completion narrative.** Each story improves the experience even if later ones never ship: persistence with freshness alone restores conversational continuity across restarts without stale-context pollution; numbered results alone enable "the second one"; the deadline cue with race-guarded notification alone closes the silent-timeout bug; refinement with recency guard alone closes the "actually for vegan" bug; cancel fast-path alone improves stop-word latency even before kill paths land.

---

## Section D — ADR

> **ADR-CCS-001: Conversation Core Stability Bundle**

**Decision.** Ship six independently-deployable improvements to the heare voice agent's conversation core — action-log persistence with freshness window, numbered+structured search results with bounded render, race-guarded confirmation deadline cue, prompt-only refinement disambiguation with recency guard, decider-layer cancel fast-path, and per-backend cancel kill paths — sequenced for compounding user-trust value.

**Drivers** (cross-ref Section A).
1. User trust under failure (restart resilience, audible deadlines, real cancel — observable end-to-end within ~500ms).
2. Conversational fidelity (addressable references, refinement vs. new search).
3. Low LOC, low blast radius (prompt-only where sufficient; localized SQL changes; reuse of existing facades; per-backend native cancel idioms instead of a unified abstraction).

**Alternatives considered** (cross-ref Section B per story).
- Story 1: separate `action_log` table (rejected — drift), JSON-blob in `meta` (rejected — not queryable). No-freshness-filter hydration (rejected — stale-context pollution).
- Story 2: numbered text only (rejected — fragile parsing), per-result hash IDs (rejected — premature). Render `items` *in addition to* `result` (rejected — double-rendering blows the prompt budget; chose items-first with legacy-result fallback).
- Story 3: single-coroutine refactor of `_timeout_watcher` (rejected — bigger blast radius), tick-loop (rejected — wasteful). No-guard notify (rejected after Iteration 1 critique — confirm-during-warning race).
- Story 4: pseudo-tool `refine_last_search` (rejected — heavier than prompt rule). No-recency-guard (rejected — day-old searches would refine).
- Story 5a: generator-emitted cancel intent (rejected after Iteration 1 critique — adds LLM round-trip on every stop-word; defeats latency principle). STT-layer wake-word detector (rejected — bypasses disambiguation entirely).
- Story 5b: cancel only the queue + TTS, leave subprocess (rejected — primary user complaint).

**Why chosen.** The chosen options minimize new code paths and new state, reuse the existing `actions` table, the existing indication facade, the existing intent-queue dispatch path, and the existing prompt assembly. Stories 1 and 2 are the load-bearing infrastructure; Stories 3 and 4 are prompt/coroutine-level fixes; Story 5 is split into a design-level fast-path (5a) and the mechanics (5b) so the principal risk is contained in 5a's standalone-imperative detector.

**Consequences.**
*Positive.*
- Conversational continuity survives daemon restart with stale-context pollution prevented (Story 1).
- "The second one" / "the vegan one" become first-class user gestures, with bounded render budget (Stories 2+4).
- Silent confirmation timeouts become observable, with the confirm-during-warning race closed (Story 3).
- "Stop" actually stops, observably end-to-end within ~500ms — even before the LLM gets involved (Stories 5a + 5b).
- Each story is independently revertible; no story locks in another. Stories 5a + 5b can ship behind separate feature flags.

*Negative.*
- One additive `ALTER TABLE` migration (Story 1) — reversible only by deleting the .db file (acceptable for local SQLite).
- Prompt complexity grows by ~40 lines (Stories 2 and 4 + Story 5a's stop-word vocabulary documentation); golden fixtures must be regenerated.
- Story 5 introduces three new kill paths (subprocess via process-group, httpx, SDK) plus a TTS fade-out + frame-drop signal — verifier must exercise all four under a real daemon before sign-off.
- The action-log write path doubles (in-memory + SQLite); error budget for write failures is "log and continue".
- **The action log shape now diverges between in-memory (rich, structured `items`) and the legacy `result` blob — `_format_recent_actions` is the bridge; future tools should adopt the structured shape to avoid the legacy fallback path accumulating debt.**
- The `_active_task` / `_active_intent` references in `ActionWorker` are explicitly NOT mirrored to SQLite (Principle 1 exemption); die-on-restart is by design.

**Follow-ups (deferred).**
- Long-term memory (cross-conversation knowledge graph, multi-day recall).
- Scheduler / time-shifted actions ("remind me tomorrow at 9").
- Barge-in TTS (interrupt the agent mid-utterance with a new request, not just cancel).
- Per-tool cancel granularity (cancel only `web_search` while letting a long-running `bash` continue).
- Telemetry dashboard for cancel rates (origin=decider vs. origin=generator), deadline-warning fires, refinement triggers — to validate the trust-improvement hypothesis in production.
- Pseudo-tool `refine_last_search` revisited if the prompt-only Story 4 underperforms in production telemetry (>10% misclassification rate).
- Fuzzy stop-word matching for noisy STT ("stahp", "cansel") if Story 5a telemetry shows missed cancels.
- Migrate legacy action-log entries (no `items`) to the structured shape so `_format_recent_actions` can drop the fallback render path.

---

*End of plan. Ready for Architect re-review (Iteration 2).*
