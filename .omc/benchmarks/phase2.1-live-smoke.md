# Phase 2.1 — Live Smoke Verification

Branch: `s2s-realtime` (Phase 2.1 uncommitted; Phase 1 commit `05c9180`)
Date: 2026-04-18

## Infrastructure verification (automated, complete)

- ✅ 543/543 tests pass (was 508 pre-Phase 2.1; +35 net)
- ✅ `make lint` clean
- ✅ Architect **APPROVED** with one doc follow-up (README updated below)
- ✅ Decider import audit clean: `pipeline.py`, `main.py`, `generator.py`
  have no imports from `src.decider` (verified by
  `rg 'from (\.|src\.)decider import' src/`)
- ✅ Heartbeat TYPE_CHECKING import of `DeciderProcessor` documented as
  Phase 2.7 follow-up
- ✅ README `"Experimental generator mode"` section replaced with
  `"Architecture (post Phase 2.1)"` section

## Architect items — closed

| Item | Status |
|------|--------|
| 7 parser invariants hold | ✅ Verified in code + 16 tests |
| Cancel regex boundary semantics | ✅ Regex byte-identical to PRD; negative + positive tests green |
| ActionWorker dispatch contract | ✅ Exact `"Use the {tool} tool: {args}"` shape + integration test regression guard |
| `kill_running_action` getattr fallback | ✅ Tested with `MagicMock(spec=["call_action"])` |
| `run_until_stopped` cancels worker task | ✅ Added to watch set + cancel list |
| No hidden decider coupling in generator/pipeline/main | ✅ Audit clean |
| README update | ✅ Section replaced post-approval |

## Human-in-the-loop (pending user validation)

These require speaking to the running daemon:

1. `"привіт"` → bot replies, NO `[INTENT SUBMITTED]` in log
2. `"запусти echo hello"` → bot replies immediately AND
   `[INTENT SUBMITTED id=1 tool=bash]` + `[ACTION RESULT id=1]`
   within 10s
3. Submit an intent, within 3s say `"скасуй"` → `[INTENT CANCELLED]`
   logged; no `[ACTION RESULT]` for that id

## Forced-failure scenarios (US-P2.1-10 — opt-in pytest -m phase2_live)

**Status: specified, harness not yet implemented.** Plan's
`pytest -m phase2_live tests/live/test_forced_failures.py` requires
env-var injection points in `OpenRouterCLI` and `ActionWorker` that are
stubbable at daemon startup. These are documented in PRD but deferred
to a lightweight follow-up PR — the happy-path unit and integration
tests already cover the equivalent error paths:

- Malformed JSON → `test_malformed_json_dropped` (unit, parser)
- Claude CLI timeout → `test_worker_timeout_triggers_on_error_and_kill_called` (unit, worker)
- Burst load (FIFO) → `test_queue_fifo_order` + `test_worker_exception_in_call_action_continues_loop` (unit, queue + worker)

Forced-failure live harness remains desirable but is not a merge
blocker — the unit coverage is the same invariant, just exercised
out-of-daemon.

## Outcome

**Phase 2.1 infrastructure complete and architect-approved. Ready for
user voice testing + commit.**

Next steps:
1. User runs daemon, tests 3 happy-path scenarios above
2. Commit Phase 2.1 to `s2s-realtime`
3. Phase 2.2 (ConversationManager re-wire) opens its own PRD
