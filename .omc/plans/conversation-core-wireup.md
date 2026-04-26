# Plan: Conversation-Core Wire-up — Live S2S Pipeline Cleanup

**Branch:** `s2s-realtime` (layered atop `16eba0e`, no remote push)
**Status:** DRAFT — pending Architect → Critic review
**Mode:** RALPLAN-DR SHORT (no auth/PII/destructive surface; cleanup + wire-up only)
**Target:** ~24 story points (6 stories, mostly wire-up + deletion)

---

## Section A — RALPLAN-DR Summary

### Principles (5)

1. **Live code is the only code that exists.** Dead modules (`DeciderProcessor`) verify nothing real and rot the audit signal. Treat them as liabilities, not artifacts.
2. **One execution path per concern.** The cancel keyword gate must live in one place (`src/language.py`), invoked from one site (`generator.py`). No parallel keyword-list + smart-detector branches.
3. **Indications fire exactly once per event.** Where `cancel_active()` already emits `INTENT_CANCELLED`, callers MUST NOT emit it again. Drift here is observable user-facing duplicate cues.
4. **Heartbeat is opt-in, not opt-out.** A no-op tick that the user has not asked for is dormant infrastructure and cost — remove unless there is an explicit product reason to keep it.
5. **Tests pin wallclock when they assert on time-of-day behaviour.** Quiet-hours flakes burn CI trust faster than the bug they hide.

### Decision Drivers (top 3)

1. **Restore behaviour parity between PRD CCS shipped stories and runtime.** CCS-05a/05b/03 currently exist on disk but are unreachable; the live cancel path drops one queued intent and never kills bash subprocesses. Fixing this is the dominant user-visible win.
2. **Reduce cognitive surface.** ~1100 lines of dead `DeciderProcessor` + ~2300 lines of dead tests slow every grep, every onboarding, every audit. Removing them is high-leverage if blast radius is contained.
3. **Keep CI green and trustworthy.** A flaky test (`test_indication.py`) makes every red CI ambiguous; fix first to unblock the rest of the cleanup.

### Mode Justification

**SHORT mode** is appropriate because:
- No auth, PII, network exposure, or destructive surface added.
- All changes are removals or single-line redirects on an already-shipped, well-tested code path.
- No pre-mortem expansion required (US-WU-05 risk is mitigated through sequencing, not architecture).
- US-WU-05 is the highest blast-radius story; its risk is contained by ordering it LAST and predicating it on US-WU-02 having already moved the only function the live path still needs.

### Per-Story Options & Rejection Rationale

| Story | Options Considered | Chosen | Rejection Rationale |
|---|---|---|---|
| US-WU-01 | A: replace `cancel_latest()` with `cancel_active()` + dedupe indication. B: leave alone, document gap. | A | B keeps the bug; the whole point of CCS-05b was that bash subprocesses, httpx, SDK kills, TTS fade-out get unreachable today. |
| US-WU-02 | A: move `_is_standalone_cancel_imperative` to `src/language.py`, swap `check_cancel` site. B: keep both in parallel. C: inline detector at call site. | A | B = two sources of truth on cancel detection (drift guarantee). C = unreusable, untestable in isolation. |
| US-WU-03 | A: delete CCS-03 dead code + mark deferred. B: build minimal AWAITING_CONFIRMATION flow in generator + wire deadline cue. | **A** | B is ~200 lines of new code and a state-machine commitment without a product driver; no destructive intents are gated today, so the timeout has nothing to protect. Defer until a confirmation flow is actually needed. |
| US-WU-04 | A: remove `HeartbeatTask` + `WarmupTask` heartbeat + `on_heartbeat_tick` stub. B: implement proactive speech via openrouter heartbeat prompt. | **A (heartbeat only)** | B has no user demand; legacy decider heartbeat was reportedly chatty. Keep `WarmupTask` (it serves edge-tts WSS keepalive, unrelated). |
| US-WU-05 | A: delete `src/decider.py` + `tests/test_decider.py`. B: delete only `DeciderProcessor` class, keep `parse_yes_no` + helpers used elsewhere. C: leave as-is. | **B (with care)** | A breaks 5 sibling test files (test_audio, test_feature_flags, test_mode_hot_reload, test_silent_timeout, test_stranger_integration, test_yes_no — each imports from `src/decider`). B requires audit of which symbols leak out. C is the status quo we are explicitly fixing. **See US-WU-05 detail.** |
| US-WU-06 | A: pin wallclock to `2026-04-24 12:00`. B: skip the test in 22:00–07:00. | A | B masks the bug. A is one line. |

All stories have ≥2 viable options or explicit invalidation rationale.

---

## Section B — PRD Body

### US-WU-01 — Wire CCS-05b kill paths into the live cancel path (MUST, 3 pts)

**Description.** The live generator's cancel-keyword branch (`src/generator.py:403-415`) calls `IntentQueue.cancel_latest()` which only pops one queued intent. The CCS-05b implementation (`actions.py:164-238` + `actions.py:376` `ActionWorker.cancel_in_flight`) supports bash-subprocess kill, httpx cancellation, agent-SDK kill, and TTS fade-out — but is unreachable. Replace the call. Eliminate double-fire of `INTENT_CANCELLED` (the new path emits it from `actions.py:233`, the generator branch must not emit it again).

**Files touched.**
- `src/generator.py` — line ~404: `cancel_latest()` → `await cancel_active()`. Lines 408-415: remove the manual `ind.notify(INTENT_CANCELLED, ...)` block (now duplicated by `actions.py:233`).
- `tests/test_generator.py` — add live-path cancel tests (assert `cancel_active` invoked, assert `cancel_in_flight_callback` runs, assert single `INTENT_CANCELLED` fires).
- `tests/integration/test_intent_flow.py` (or sibling) — add bash subprocess kill within 3s end-to-end check.

**Acceptance criteria (8).**
1. `src/generator.py:404` calls `await self.intent_queue.cancel_active()` (no `cancel_latest()` call remains in generator).
2. Generator's old `ind.notify(IndicationKind.INTENT_CANCELLED, ...)` block at lines 408-415 is removed.
3. New unit test: cancel utterance with empty queue + active in-flight → `ActionWorker.cancel_in_flight()` invoked exactly once.
4. New unit test: cancel utterance with 2 queued intents + 1 in-flight → all 3 cancelled, `INTENT_CANCELLED` indication fires exactly once (no double-fire).
5. New integration test: spawn a `bash` intent that sleeps ≥10s; utter "stop"; assert subprocess killed within 3s wallclock.
6. New unit test: cancel with empty queue and no in-flight → `cancel_active` returns False, no indication fires (parity with current `actions.py:228`).
7. `cancelled_id` logging at `generator.py:407` is preserved or replaced with equivalent log record (so CI scrapers depending on `[INTENT CANCELLED id=...]` keep working OR the format change is documented).
8. Full test suite green except `test_indication.py` flake (fixed in US-WU-06).

**Risks + mitigations.**
- *Risk:* Old log-format consumers parsing `[INTENT CANCELLED id=N]` break. *Mitigation:* preserve the log line by emitting it from a wrapper around `cancel_active()` OR document log-format change in PR body.
- *Risk:* `cancel_active()` returns False when nothing was cancelled — silent UX regression if a user says "stop" with idle queue. *Mitigation:* AC #6 explicitly verifies, and current behaviour at `generator.py:404-407` is identical (no indication if `cancel_latest()` returns None).

**Dependencies.** None (self-contained).

---

### US-WU-02 — Promote `_is_standalone_cancel_imperative` and use it on the live hot path (MUST, 4 pts)

**Description.** The smart cancel detector with negation guard, ≤4-word residual rule, and "do not stop" / "stop sign" / "не зупиняйся" carve-outs lives in `src/decider.py:229-...` but is invoked only from the dead `DeciderProcessor`. The live generator uses the cruder `check_cancel` (regex word-boundary keyword match, `src/language.py:43-51`). Move the smart detector to `src/language.py` next to `check_cancel`, and update the generator call site to use it. After this, `check_cancel` may be removed if no caller remains.

**Files touched.**
- `src/language.py` — paste/move `_is_standalone_cancel_imperative` + its `_CANCEL_NEGATION_TOKENS`, `_CANCEL_POLITENESS_PREFIXES`, `_CANCEL_FILLER_PREFIXES`, `_CANCEL_CONTEXT_TOKENS`, `_CANCEL_MAX_RESIDUAL_WORDS` constants. Promote to public name `is_standalone_cancel_imperative` (drop leading underscore — it is API now).
- `src/generator.py:403` — replace `check_cancel(transcript, self._active_lang)` with `is_standalone_cancel_imperative(transcript, self.settings.cancel_stop_words)`. Note: language param is dropped because the detector matches against the multilingual `cancel_stop_words` list directly (settings already covers en/uk/ru tokens).
- `src/decider.py` — delete the moved function + constants from here (US-WU-02 land), or leave as a re-export shim if US-WU-05 is delayed (preference: re-export to avoid breaking `tests/test_decider.py` line 2110 import before deletion).
- `tests/test_language.py` — port the imperative-form tests, negation-guard tests, ≤4-word constraint tests, fixture-driven tests from `cancel_stopwords.txt`, ralph false-positive cases ("don't stop the recording", "the stop sign is red", "не зупиняй це"), and the "no no no stop" carve-out. **Do not delete the originals from `tests/test_decider.py` until US-WU-05** — they will live for one commit alongside.
- `tests/test_generator.py` — add: cancel utterance variants ("please stop", "будь ласка зупинись", "no no no stop") trigger fast-path; non-cancel variants ("don't stop the recording", "stop sign is red") do not.
- `src/config.py` lines 238-240 — update doc comment that references `src/decider.py:_is_standalone_cancel_imperative` to point at `src/language.py`.

**Acceptance criteria (10).**
1. `is_standalone_cancel_imperative` is importable from `src.language` and behaves identically to the version in `src/decider.py` for all existing fixture inputs (regression: every test in `tests/test_decider.py:2110+` and `cancel_stopwords.txt` passes against the new location).
2. `src/generator.py:403` calls `is_standalone_cancel_imperative(transcript, self.settings.cancel_stop_words)`.
3. `check_cancel(...)` in `src/language.py` is either deleted (preferred) or has zero remaining callers in `src/`. If kept, justify in PR body.
4. New generator test: utterance "please stop" with default `cancel_stop_words` → cancel fires.
5. New generator test: utterance "don't stop the recording" → cancel does NOT fire.
6. New generator test: utterance "the stop sign is red" → cancel does NOT fire.
7. New generator test: utterance "не зупиняй це" → cancel does NOT fire (negation guard for `не`).
8. New generator test: utterance "no no no stop" → cancel fires (special carve-out preserved).
9. Negation-guard tests run against the live hot path (i.e., they instantiate the real `GeneratorProcessor` or its detector via the `src.language` module path).
10. `src/config.py` doc comment about the detector points at `src/language.py`, not `src/decider.py`.

**Risks + mitigations.**
- *Risk:* Subtle behaviour drift if some constant gets reformatted on the move. *Mitigation:* AC #1 — full fixture replay against the new location, byte-for-byte equivalent.
- *Risk:* `cancel_stop_words` setting is empty/None for some user → detector silently never triggers. *Mitigation:* add a startup log line `cancel_stop_words=N words loaded` so an empty config is observable; existing detector returns False on empty `stop_set` (decider.py:291), preserved.
- *Risk:* Regex/token tests in `test_decider.py:2110+` start failing once the function is moved (import path change). *Mitigation:* leave a re-export shim `from .language import is_standalone_cancel_imperative as _is_standalone_cancel_imperative` in `src/decider.py` until US-WU-05 deletes that file.

**Dependencies.** None at runtime; coordinates with US-WU-05 (US-WU-05 must wait on this).

---

### US-WU-03 — Decide CCS-03 fate: DELETE (recommended) or DESIGN confirmation flow (SHOULD, 2 pts for chosen path)

**Recommendation: Option A — Delete.**

**Description.** CCS-03 (deadline-warning cue) lives at `src/decider.py:1042-1080` and fires `IndicationKind.CONFIRMATION_DEADLINE` 5 seconds before a confirmation timeout. The live `GeneratorProcessor` has **no AWAITING_CONFIRMATION state** at all — there is no timeout to warn about. The cue, the watcher task, the race-guard, and the related tests in `test_decider.py` are unreachable.

**Option A (chosen): Delete.**
- Delete `_deadline_warning_task`, `_deadline_warning_watcher`, the timeout-task scheduling at lines 1030-1044, and the cleanup at lines 1046-1058 from `src/decider.py` (these go away entirely with US-WU-05 anyway, but explicitly mark them deleted as part of US-WU-03 to record the product decision).
- Update `.omc/progress.txt` (or equivalent shipped-stories ledger) to mark CCS-03 status: `deferred — depends on confirmation flow which is not yet implemented`.
- `IndicationKind.CONFIRMATION_DEADLINE` stays in `src/indication.py` (callers may want it in future); document that it has no live emitter today.

**Option B (rejected): Build minimal AWAITING_CONFIRMATION flow in `GeneratorProcessor`.**
- Would require: new state field on the processor, intent-classification of destructive ops (`bash` with `rm`/`sudo` patterns from generator prompt forbidden list, `delete_profile`, etc.), passphrase confirmation gate, new timeout task, new state transitions. ~200 lines of code + ~150 lines of tests.
- *Why rejected:* No product driver. No user has asked for confirmation gates. The shipped passphrase flow already exists for owner enrollment but is orthogonal to action confirmation. Adding state machine surface here is premature.

**Files touched (Option A).**
- `src/decider.py` — delete `_deadline_warning_task` field, `_deadline_warning_watcher` method, scheduling at 1030-1044, cleanup at 1046-1058. (Subsumed by US-WU-05; US-WU-03 ships as a documentation-only commit if US-WU-05 lands first.)
- `.omc/progress.txt` and any `prd-*-completed.json` referencing CCS-03 — annotate as deferred.

**Acceptance criteria (5).**
1. `.omc/progress.txt` has an entry stating CCS-03 is deferred pending a confirmation flow.
2. PR body explicitly justifies why Option A was chosen over Option B.
3. `IndicationKind.CONFIRMATION_DEADLINE` enum value remains in `src/indication.py` (kept for future use; documented no-emitter).
4. After this story, no live code path schedules `_deadline_warning_task` (verified by grep returning zero matches in `src/`).
5. Architect/Critic gets to argue Option B in review; if they accept, US-WU-03 converts to a separate plan and is descoped from this PRD.

**Risks + mitigations.**
- *Risk:* User actually does want destructive-op confirmation; we deferred prematurely. *Mitigation:* Architect review + explicit deferral note in `progress.txt` + open question logged in `.omc/plans/open-questions.md`.

**Dependencies.** Logically subsumed by US-WU-05 (which deletes the file containing the dead code). US-WU-03 ships the *decision* + ledger update; US-WU-05 ships the *deletion*.

---

### US-WU-04 — Heartbeat: REMOVE (recommended) or IMPLEMENT proactive speech (SHOULD, 3 pts for chosen path)

**Recommendation: Option A — Remove `HeartbeatTask` and the `on_heartbeat_tick` no-op.**

**Description.** `HeartbeatTask` (`src/heartbeat.py:18-55`) ticks every N minutes and calls `processor.on_heartbeat_tick()`. The live `GeneratorProcessor.on_heartbeat_tick` (`src/generator.py:560-562`) is a no-op. So the heartbeat fires `IndicationKind.HEARTBEAT_TICK` (presence-of-life cue at `heartbeat.py:45`) and nothing else. `WarmupTask` is unrelated — it keeps edge-tts WSS warm; it stays.

**Option A (chosen): Remove HeartbeatTask + the no-op + the config + main.py wiring.**
- Drop `HeartbeatTask` class from `src/heartbeat.py` (keep `WarmupTask`).
- Drop `on_heartbeat_tick` no-op from `src/generator.py:560-562`.
- Drop `heartbeat_interval_minutes` from `src/config.py` Settings (verify no other callers).
- Drop `heartbeat = HeartbeatTask(...)` construction at `src/main.py:544` and the `heartbeat,` argument to `run_until_stopped`.
- Drop `IndicationKind.HEARTBEAT_TICK` if no other emitter (verify with grep) OR keep it dormant alongside `CONFIRMATION_DEADLINE` (consistent with US-WU-03 treatment).

**Option B (rejected): Implement proactive speech.**
- Would require: heartbeat prompt template, openrouter call with appropriate context, decision logic for whether reply is non-empty, anti-chatty rate limiting (legacy decider's heartbeat was reportedly chatty per audit).
- *Why rejected:* No stated user need. Risk of regression to chatty behaviour. Cost without demand.

**Files touched (Option A).**
- `src/heartbeat.py` — delete `HeartbeatTask` class.
- `src/generator.py:556-562` — delete `on_heartbeat_tick` and `shutdown` (the latter is also a no-op; verify nothing in `main.py` calls it; if it does, keep `shutdown` for the call but drop `on_heartbeat_tick`).
- `src/config.py` — drop `heartbeat_interval_minutes` Setting.
- `src/main.py:544, 553-557` — drop the `HeartbeatTask` construction and `heartbeat,` arg.
- `src/indication.py` — verify and document `HEARTBEAT_TICK` status.
- Tests: any test referencing `HeartbeatTask` or `heartbeat_interval_minutes`.

**Acceptance criteria (6).**
1. `grep -r "HeartbeatTask\|heartbeat_interval_minutes\|on_heartbeat_tick" src/ tests/` returns zero matches (in live code) after this story.
2. `WarmupTask` still constructed in `main.py` and runs (no regression to edge-tts warmup).
3. `IndicationKind.HEARTBEAT_TICK` either removed (preferred if no other reference) or documented as dormant.
4. `pytest tests/` passes; any heartbeat-specific tests are deleted with their target.
5. `heartbeat_interval_minutes` no longer appears in `.env.example`, README, or docs.
6. PR body documents the rejection of Option B.

**Risks + mitigations.**
- *Risk:* `HeartbeatTask` was actually doing something subtle via `IndicationKind.HEARTBEAT_TICK` (e.g., a watchdog UI). *Mitigation:* grep `HEARTBEAT_TICK` consumers across `src/indication_backends/` and dashboards before deletion. If a backend depends on it, decide between (a) keep cue, drop the no-op processor call, (b) delete the cue too if backend is also dormant.
- *Risk:* `run_until_stopped` signature change ripples. *Mitigation:* trivial — drop one positional arg.

**Dependencies.** Must ship before US-WU-05 because US-WU-05 deletes `DeciderProcessor` and `heartbeat.py:11-12` still has a `TYPE_CHECKING` import of it. If we keep `HeartbeatTask`, the typing import has to be retargeted to `GeneratorProcessor`. If we drop `HeartbeatTask` (Option A), the typing import disappears with it — cleaner.

---

### US-WU-05 — Delete `DeciderProcessor` + its dead tests (MUST, 8 pts — HIGHEST RISK)

**Description.** `src/decider.py` is ~1333 lines. The `DeciderProcessor` class (lines 406-1310) is never instantiated by the live pipeline. `tests/test_decider.py` is ~2289 lines testing the dormant class. Deletion is high-leverage — but blast radius is wider than just these two files.

**BLAST-RADIUS AUDIT (critical — surfaced during planning):**

The following live code currently imports from `src/decider.py`:
- `src/heartbeat.py:12` — `TYPE_CHECKING` import of `DeciderProcessor`. (Resolved by US-WU-04 Option A: file changes, import goes away.)

The following **test files** currently import from `src/decider.py` and would break if the module is removed wholesale:
- `tests/test_audio.py:35` — `from src.decider import create_decider_processor` (3 instantiations: lines 263, 324, 375).
- `tests/test_feature_flags.py:17` — `from src.decider import create_decider_processor` (4 instantiations).
- `tests/test_mode_hot_reload.py:15` — `from src.decider import create_decider_processor` (3 instantiations). Comment line 1: `"BCDE-002: mode hot-reload takes effect on a running DeciderProcessor."`
- `tests/test_silent_timeout.py:16` — `from src.decider import create_decider_processor` (4 instantiations).
- `tests/test_stranger_integration.py:27` — `from src.decider import create_decider_processor` (1 instantiation).
- `tests/test_yes_no.py:6` — `from src.decider import parse_yes_no`.

These tests are **also** testing the dormant class. They look live (they exercise audio, feature flags, mode hot-reload, silent-timeout, stranger detection, yes/no parsing) but they exercise these features *via DeciderProcessor*, which is not in the production pipeline. Two interpretations:

  - **Interpretation A:** These tests are also dead. Delete them with `tests/test_decider.py`. The features they cover (mode hot-reload, silent timeout, stranger detection, audio routing) need *new* tests against `GeneratorProcessor`. **This dramatically widens scope** — likely the features themselves are partially dead in `GeneratorProcessor` and need wiring before they can be tested.
  - **Interpretation B:** These tests are exercising real behaviour and `create_decider_processor` is the only test entry point that wires them up. Refactor each to use `create_generator_processor`.

The audit doc claims "92 tests" but blast radius is more like 92 (test_decider.py) + N (each sibling file's count). **The architect must decide A vs B before US-WU-05 ships.**

**Recommendation in this plan:** Option B for US-WU-05 — **a partial deletion**:
- Delete `DeciderProcessor` class body (`src/decider.py:406-1310`) and its tests in `tests/test_decider.py`.
- **Keep** `create_decider_processor`, `parse_yes_no`, and any other module-level helpers that sibling test files import — until each sibling test file is converted (or deleted) under a separate cleanup story.
- This means `src/decider.py` shrinks from 1333 to ~150 lines (helpers + factory shim) and `tests/test_decider.py` is deleted in full.
- Open follow-up: file 6 sibling-test conversion stories, OR if those sibling tests turn out to be dead-test-of-dead-code too, delete in a follow-up bulk pass.

**Files touched.**
- `src/decider.py` — delete `_build_decider_processor_class` body and `DeciderProcessor` class (lines 406-1310). Keep module-level helpers (`parse_yes_no`, constants, etc.) until sibling tests migrate. The `_is_standalone_cancel_imperative` function moves out under US-WU-02; what remains is module-level helpers + the factory.
- `tests/test_decider.py` — delete entirely.
- `src/heartbeat.py:11-12` — the `TYPE_CHECKING` import of `DeciderProcessor` — resolved by US-WU-04 Option A.
- `src/main.py` — verify no import of `DeciderProcessor`; the `decider=processor` kwarg at line 555 to `run_until_stopped` is a misnomer (passing the live `GeneratorProcessor` under a legacy parameter name) — rename to `processor=processor` or similar; not blocking.

**Acceptance criteria (10).**
1. `src/decider.py` no longer contains `class DeciderProcessor`. (`grep "class DeciderProcessor" src/decider.py` returns no match.)
2. `tests/test_decider.py` is deleted.
3. `pytest tests/` passes (the 5 sibling test files still pass because `create_decider_processor` and `parse_yes_no` shims remain).
4. `grep -r "DeciderProcessor" src/` returns zero matches in production paths (TYPE_CHECKING blocks excluded if any remain).
5. CI run-time decreases by at least the time previously spent on `tests/test_decider.py` (verify with two CI runs).
6. The 5 sibling test files (`test_audio`, `test_feature_flags`, `test_mode_hot_reload`, `test_silent_timeout`, `test_stranger_integration`, `test_yes_no`) still pass — OR are explicitly migrated/deleted in follow-up stories tracked in `.omc/plans/open-questions.md`.
7. `src/main.py:555` legacy kwarg name `decider=` is either renamed to `processor=` or documented as a hold-over alias; the runtime instance passed is the `GeneratorProcessor`.
8. Open question logged: "Are sibling decider tests (audio/feature_flags/mode_hot_reload/silent_timeout/stranger_integration/yes_no) testing live behaviour or dormant behaviour? Architect to decide A vs B."
9. `src/decider.py` total LOC drops by ≥85% (from 1333 to ≤200).
10. Branch builds clean with no unused-import warnings.

**Risks + mitigations.**
- *Risk:* Sibling test files turn out to exercise real `GeneratorProcessor` behaviour indirectly via `DeciderProcessor` shim, and we silently lose feature coverage on `GeneratorProcessor`. *Mitigation:* AC #8 — open question forces explicit architect decision. Do not delete `create_decider_processor` until that decision lands.
- *Risk:* `parse_yes_no` and other helpers are entangled with `DeciderProcessor` state. *Mitigation:* delete only the class body; keep module-level helpers; if entangled, surface in PR review.
- *Risk:* Git rename detection garbles diff readability for the moved `_is_standalone_cancel_imperative` (US-WU-02). *Mitigation:* land US-WU-02 commit first, then US-WU-05 commit on top.
- *Risk:* CI catches a hidden production import we missed. *Mitigation:* AC #4 grep + run full suite before merge.

**Dependencies.** Hard dependency on US-WU-02 (function moved out of decider.py first). Hard dependency on US-WU-04 (heartbeat.py's `TYPE_CHECKING` import resolved). Soft dependency on US-WU-03 (CCS-03 deadline-warning code lives inside the deleted region; US-WU-03's deletion is folded into this story).

---

### US-WU-06 — Fix `test_indication.py::test_notify_dispatches_to_all_enabled_backends` flake (MUST, 1 pt)

**Description.** Test at `tests/test_indication.py:80-92` constructs `Indication(_settings(), [sound, visual, notif])` without a pinned `wallclock`. Default quiet hours are 22:00–07:00 local, so the test fails between those hours. Sibling test at line 117 already pins wallclock (`outside_quiet = dt.datetime(2026, 4, 24, 12, 0)` + `wallclock=lambda: outside_quiet`). Apply the same pattern.

**Files touched.**
- `tests/test_indication.py:84` — change `Indication(_settings(), [sound, visual, notif])` to `Indication(_settings(), [sound, visual, notif], wallclock=lambda: dt.datetime(2026, 4, 24, 12, 0))`.

**Acceptance criteria (4).**
1. Line 84 of `tests/test_indication.py` constructs `Indication` with a pinned `wallclock=lambda: dt.datetime(2026, 4, 24, 12, 0)`.
2. Test passes when CI clock is set to e.g. `2026-04-25 23:30:00` (verify by faking system time or running test twice with `freezegun` outside default quiet hours and inside).
3. No other test in `tests/test_indication.py` or sibling indication test files has the same bug (audit complete).
4. Story ships in a single isolated commit (one-line diff) so it can be cherry-picked or reverted without entanglement.

**Risks + mitigations.**
- *Risk:* `OWNER_AUTO_ENROLLED` indication is treated as SUCCESS level and the comment at line 89 says `Defaults: sound=True, visual=True, notification=False` — adding wallclock might unexpectedly change which backends fire if quiet-hours interact with SUCCESS. *Mitigation:* sanity-check by reading `src/indication.py` quiet-hours logic; SUCCESS-level indications are typically not quiet-hours-gated, but verify before edit.

**Dependencies.** None. Ships first.

---

## Section C — Sequencing & Rollout

### Order (justified)

| # | Story | Why this slot |
|---|---|---|
| 1 | **US-WU-06** | One-line fix, unblocks CI signal so subsequent stories' test runs are interpretable. Lowest risk, highest signal-clearing value. |
| 2 | **US-WU-01** | Single-line wire-up + new tests. Self-contained. Restores CCS-05b kill paths to live — biggest user-visible behaviour fix. Independent of all other stories. |
| 3 | **US-WU-02** | Move + swap detector. Touches both `src/language.py` and `src/generator.py`. Ships before US-WU-04 because the order doesn't matter between them, but US-WU-02 must precede US-WU-05 (it removes the function from `decider.py`). |
| 4 | **US-WU-04** | Decide and remove heartbeat. Must precede US-WU-05 because `heartbeat.py:11-12` has `TYPE_CHECKING` import of `DeciderProcessor`; removing `HeartbeatTask` (Option A) drops this import cleanly so US-WU-05's deletion doesn't strand it. |
| 5 | **US-WU-03** | Decision + ledger update. Subsumed deletion-wise by US-WU-05 but ships earlier as the *decision* commit (so the rationale lands separately from the bulk deletion). |
| 6 | **US-WU-05** | Highest blast radius. Ships LAST so all dependent stories have stabilised. Predicated on US-WU-02 (function moved out), US-WU-04 (heartbeat import gone), US-WU-03 (CCS-03 code marked deferred). |

### Highest-risk story

**US-WU-05** is highest-risk because:
- 1100+ lines deleted from `src/decider.py`.
- 5 sibling test files import from `src/decider`. Wholesale module deletion would break them.
- Audit may have undercounted the scope by claiming "92 tests" — actual scope likely larger if sibling tests also turn out to be dormant.
- Mitigated by: (1) partial deletion strategy (keep helpers + factory shim), (2) ordering it last, (3) explicit open-question for architect on sibling-test interpretation, (4) ≥85% LOC drop AC ensures the goal is achieved without forcing architecturally-disruptive sibling rewrites in this PRD.

### Rollout / Verification

- All 6 stories ship as separate commits on `s2s-realtime` (no remote push until user requests).
- After each commit: `pytest tests/` green, baseline timings recorded for US-WU-05's CI-time AC.
- Smoke test after US-WU-01 + US-WU-02: live S2S session — utter "stop" mid-action, verify bash subprocess killed within 3s and indication fires once.
- After US-WU-05: full test suite + grep audit (`DeciderProcessor`, `HeartbeatTask`, `heartbeat_interval_minutes`).

---

## Section D — ADR

### Decision

Wire CCS-05a/05b kill paths into the live `GeneratorProcessor` cancel path, promote the smart cancel detector to `src/language.py`, defer CCS-03 (no live confirmation flow), remove dormant `HeartbeatTask`, and partially delete `DeciderProcessor` (class body + tests) while preserving module-level helpers used by sibling test files pending architect direction.

### Drivers

1. Restore PRD-CCS shipped behaviour to the live runtime (CCS-03/05a/05b are dead in production).
2. Reduce dormant-code surface (~1100 lines decider class + ~2300 lines tests) to make the codebase auditable.
3. Eliminate one CI flake to restore signal trust ahead of bigger cleanups.

### Alternatives Considered

- **US-WU-03:** Build minimal AWAITING_CONFIRMATION flow in `GeneratorProcessor` to make CCS-03 reachable. *Rejected:* ~200 lines of new state-machine code without a product driver; no destructive intents are gated today.
- **US-WU-04:** Implement proactive heartbeat speech via openrouter prompt. *Rejected:* No user demand; legacy heartbeat reportedly chatty; cost without value.
- **US-WU-05 wholesale deletion:** Delete `src/decider.py` entirely + all sibling tests. *Rejected as too-aggressive:* 6 sibling test files import from `src/decider`; their interpretation as live-vs-dormant tests requires architect decision and likely separate cleanup stories.
- **US-WU-05 do-nothing:** Status quo. *Rejected:* the whole point of this PRD is to remove the dead surface.

### Why Chosen

- The chosen path **fixes user-visible behaviour first** (US-WU-01/02), then **decides** product questions (US-WU-03/04), then **removes** dormant code (US-WU-05) — least-to-most blast radius, with the CI-flake fix (US-WU-06) up front to keep signals readable.
- For US-WU-03 and US-WU-04 specifically: deferring/removing dormant features preserves optionality (the `IndicationKind` enums remain, so a future confirmation-flow PRD can re-emit) while removing today's cost.
- For US-WU-05: partial deletion is conservative — it achieves the LOC goal (≥85% drop in `src/decider.py`) without forcing a same-PRD rewrite of 6 sibling test files. Architect can decide whether to convert them or delete them in follow-up.

### Consequences

**Positive:**
- CCS-05a/05b kill paths reach production: bash subprocesses, httpx, agent SDK kill, TTS fade-out all become reachable on "stop"/"cancel" utterances.
- One source of truth for cancel detection (`src/language.py`).
- ~3300 LOC removed (1100 src + 2300 tests).
- One flake gone.
- CI runs faster.
- Cancel detection on the live path gains negation guard, ≤4-word residual rule, and Cyrillic carve-outs that today only the dormant decider had.

**Negative:**
- Sibling decider-tests question is deferred — debt logged, not paid.
- `IndicationKind.CONFIRMATION_DEADLINE` and possibly `HEARTBEAT_TICK` become enum values with no live emitter (small dead-code residue, intentional).
- Six follow-up cleanups potentially queued (one per sibling test file) — debt is bounded and tracked.

### Follow-ups (deferred)

- **Confirmation flow design** — Owner of: TBD. Trigger: a destructive-intent product requirement. Out of scope here.
- **Proactive speech** — Owner of: TBD. Trigger: a stated user need + chatty-rate-limit design. Out of scope here.
- **Long-term memory** — Already deferred elsewhere. Unaffected by this PRD.
- **Sibling decider-test conversion** — 6 test files (`test_audio`, `test_feature_flags`, `test_mode_hot_reload`, `test_silent_timeout`, `test_stranger_integration`, `test_yes_no`). Architect to decide live-vs-dormant; potential bulk-delete or migrate-to-`create_generator_processor` story.
- **Log-format compatibility** — `[INTENT CANCELLED id=N]` may shift format under US-WU-01; downstream consumers (dashboards, log scrapers) to verify in follow-up.
- **`run_until_stopped(decider=...)` kwarg rename** — cosmetic; legacy name passes a `GeneratorProcessor` instance under a stale name.

---

## Open Questions (to also append to `.omc/plans/open-questions.md`)

- [ ] **US-WU-03:** Architect/Critic — accept Option A (delete CCS-03) or argue Option B (build confirmation flow)? — Affects ~200 LOC commitment.
- [ ] **US-WU-04:** Are there any `IndicationKind.HEARTBEAT_TICK` consumers in `src/indication_backends/` or external dashboards? — Affects whether enum value is removed or kept dormant.
- [ ] **US-WU-05:** Are sibling decider-tests (`test_audio`, `test_feature_flags`, `test_mode_hot_reload`, `test_silent_timeout`, `test_stranger_integration`, `test_yes_no`) testing live `GeneratorProcessor` behaviour via shim, or are they dormant tests of dormant code? — Determines whether they are migrated or deleted in follow-up.
- [ ] **US-WU-01:** Should the `[INTENT CANCELLED id=N]` log line format be preserved exactly for downstream log scrapers, or is reformat acceptable? — One-line diff either way; needs ops sign-off.
- [ ] **US-WU-02:** After moving `_is_standalone_cancel_imperative` into `src/language.py`, should `check_cancel` (regex keyword match) be deleted or kept as a back-compat alias? — Affects API surface of `src/language.py`.

---

## Consensus Execution Conditions (locked by ralplan iteration 0)

The Architect (PROCEED-WITH-EDITS) and Critic (APPROVE-WITH-EXECUTION-CONDITIONS) agreed: the plan is sound and ready for execution, but **four AC tightenings MUST be applied** at the executor's PR time — they are not blockers requiring re-planning, but the executor cannot mark the relevant story as passing without them.

### EC-1 — US-WU-01 single-emit guarantee (resolves Architect tension T1)

After the swap to `cancel_active()`, the `INTENT_CANCELLED` indication must fire **exactly once** per user cancel utterance. Single emit site is `src/actions.py:233`. The current `src/generator.py:408-415` notify block must be deleted in the same commit.

> **Replace AC#4 with:** "Unit test uses `Mock`/spy on `IndicationProvider.notify` and asserts `call_count == 1` for `IndicationKind.INTENT_CANCELLED` over the cancel-path with 2 queued + 1 in-flight intents. The generator's manual `INTENT_CANCELLED` notify block (lines 408-415) is deleted."

### EC-2 — US-WU-02 `check_cancel` deletion is MUST (resolves T2)

Drift is guaranteed if both `check_cancel` and `_is_standalone_cancel_imperative` survive. The plan's "deleted (preferred)" wording allows ambiguity; consensus locks deletion.

> **Replace AC#3 with:** "`check_cancel` and the `CANCEL_PATTERNS` dict are deleted from `src/language.py`. `grep -rn 'check_cancel\|CANCEL_PATTERNS' src/` returns zero matches. The `'either deleted (preferred) or has zero remaining callers'` escape hatch is removed — deletion is required."

### EC-3 — US-WU-05 shim semantics are explicit (resolves T3)

The plan keeps `create_decider_processor` as a shim but doesn't specify behavior post-class-deletion. Consensus picks: **raise `RuntimeError` with a migration message**. Sibling tests must either pass on the new path or be explicitly skip-marked.

> **Add AC#9:** "`create_decider_processor(...)` raises `RuntimeError('DeciderProcessor removed; sibling tests pending GeneratorProcessor migration')`. Each of the 6 sibling test files (`test_audio.py`, `test_feature_flags.py`, `test_mode_hot_reload.py`, `test_silent_timeout.py`, `test_stranger_integration.py`, `test_yes_no.py`) either (a) all tests still pass without touching `create_decider_processor`, or (b) the affected tests are marked `pytest.mark.skip(reason='pending DeciderProcessor → GeneratorProcessor migration')`. Bare `ImportError` or `AttributeError` at collection time is REJECT."

### EC-4 — US-WU-04 env-var clean-up is in scope (resolves T4)

The real env var name is `HEARE_HEARTBEAT_MIN` (`src/config.py:401`, not `HEARTBEAT_INTERVAL_MINUTES` as Architect first cited). The plan's `Files touched` for US-WU-04 omits the env-override block. Consensus locks deletion + deployment-owner callout.

> **Add to US-WU-04 `Files touched`:** "`src/config.py:401-406` — delete the `HEARE_HEARTBEAT_MIN` env override block."
>
> **Add AC#7:** "Deployed `.env` files containing `HEARE_HEARTBEAT_MIN=N` produce no error and no startup log warning (silently ignored, since the env block is gone). PR body explicitly calls out `HEARE_HEARTBEAT_MIN` as a removed env var so deployment owners can clean their `.env`."
>
> **Add AC#8:** "`grep -rn HEARE_HEARTBEAT_MIN src/ tests/` returns zero matches."

### Notes

- All four conditions are local AC edits — no story is restructured or resequenced.
- Critic explicitly noted these are "execution-time conditions, not blockers requiring re-planning".
- The Architect's steelman antithesis (cleanup-first ordering would invert the risk) was acknowledged but not adopted; the plan's wireup-first sequence with US-WU-05 last is defended by the partial-deletion strategy + checkpoint after US-WU-02.
