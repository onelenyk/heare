# Phase 2.2 — Live Smoke Verification

Branch: `s2s-realtime` (Phase 2.2 uncommitted; 2.1 baseline `576a0c4`)
Date: 2026-04-18

## Infrastructure verification (automated, complete)

- ✅ 555/555 tests pass (was 543 after Phase 2.1; +12 net)
- ✅ `make lint` clean
- ✅ Architect **APPROVED** on all 5 verification points:
  1. No `asyncio.Lock` misuse — dict/deque-only sync writes on single event loop
  2. `build_for_generator` threads `conversation_id` to `build()`
  3. `_handle_transcription` acquires `conversation_id` once at top
  4. Background memory update is fire-and-forget via `asyncio.create_task`
  5. Drift-guard math holds: 7 keys excluded, 4 conversation keys flow through
- ✅ TTS scrubber covers bash-standalone, JSON fragment, bashful passthrough
- ✅ Saturated-context intent-compliance test passes (2.1 regression guard)

## Architect + Critic rounds — all closed

| Round | Issues | Status |
|-------|--------|--------|
| Architect v1 | 6 blockers (lock, DB-flush contradiction, drift-test break, flag collision, TTS leak weak, saturation test missing) | All fixed in plan v2 |
| Critic v2 | 3 blockers (asyncio.Lock vs sync contract, drift math, conversation_id acquisition) | All fixed in plan v3 |
| Architect sign-off | APPROVED | Plan v3 implementation matches spec |

## Phase 2.2 capabilities delivered

- **Action outcomes in conversation context.** `on_result`/`on_error`
  callbacks update `ConversationManager._action_log` synchronously
  before logging. Next turn's prompt sees the completed action via
  `recent_actions` formatted lines.
- **Conversation summary/topics/entities/recent_turns flow into prompt.**
  Generator now sees rich context; `build_for_generator` projects 4
  conversation keys + injects `recent_actions`.
- **Background topic extraction.** `_background_memory_update`
  spawned post-TTS-push; topic extraction Claude call gated by
  `topic_extraction_enabled` flag.
- **TTS-leak scrubber.** Defense-in-depth regex sanitizer strips
  `bash`/JSON fragments if Gemini ever emits them in reply text.
- **Latency preserved.** Hot path adds exactly 1 `await
  get_or_create_active()` (~10ms). TTFT budget intact.

## Human-in-the-loop (pending user validation)

1. Say "завтра купити хліб" → bot replies
2. Wait 3s (background memory update settles)
3. Say "що я хотів зробити?" → reply should reference "хліб"
4. Say "запусти echo done" → intent submitted + completes
5. Say "чи все вдалося?" → reply should reference the echo action success

## Outcome

**Phase 2.2 infrastructure complete and architect-approved. Ready for
commit + user voice testing.**

Next: Phase 2.3 (Speaker ID soft-hint re-enable) opens its own PRD.
