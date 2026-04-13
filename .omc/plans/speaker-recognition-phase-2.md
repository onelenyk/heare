# Speaker Recognition — Phase 2 Plan

Written after Phase 1 was shipped and live-hardware tested. Load this file
first in a fresh session; everything needed is inline.

## TL;DR

Phase 1 shipped (commit `10dd1d1`) but live-hardware calibration surfaced
three issues that weren't in the original plan (`.omc/plans/speaker-recognition.md`).
**Phase 2 = Track B (live-hw fixes) first, then Track A (original Phase 2
feature set).** Track B is narrow, urgent, and unblocks Track A — do not
reverse the order.

## Context for a fresh session

- Phase 1 is complete, 292 pytest tests passing, ruff clean.
- Phase 1 commit: `git show 10dd1d1` — covers speaker_id, speaker_gallery,
  speaker_processor, decider gating, pipeline wiring, enroll-owner CLI,
  storage migration, flag-off golden-string test.
- Original consensus plan at `.omc/plans/speaker-recognition.md`
  (1,734 lines) — the architecture, risks, and ADR are still current.
- Live-hardware findings in `.omc/progress.txt` under the
  "Live-hardware calibration findings" section.
- Runtime config in `~/.heare/config.toml` (NOT in repo):
  ```toml
  speaker_id_enabled = true
  speaker_id_threshold_match = 0.65
  speaker_id_min_duration_ms = 1500
  ```
- Enrolled owner gallery in `~/.heare/speakers.json` — 9 reference
  embeddings (1 from 15s enrollment + 8 appended from live-daemon turns).

## Live-hardware findings motivating Track B

1. **Default threshold 0.75 is VoxCeleb-grade but too strict for daemon VAD
   chunks.** ECAPA accuracy drops below ~3 seconds of audio; daemon VAD
   slices average 1.5-2.5s. Offline 5s clips score 0.79-0.84 against
   enrollment; daemon 2s clips score 0.50-0.70 against the same reference.
   Calibration via on-machine config works but is brittle and requires
   a multi-reference gallery that Phase 1 doesn't support natively.
2. **Stale `_prev_id` across non-matches is a security leak.** In
   `speaker_processor.py` the tagger's `self._prev_id` only updates on a
   positive match. If owner speaks (prev_id=owner), then stranger speaks
   (sid=None, prev_id unchanged), then a short turn happens, the short
   turn inherits "owner" even though the most-recent turn was non-owner.
   In LISTENING this is benign; in AWAITING_CONFIRMATION the decider's
   inherited-reject gate catches it — but the inconsistency shouldn't
   exist. The plan's `speaker_id_sticky_seconds = 5.0` setting was defined
   but never wired in.
3. **Short confirmation words ("так", "ні") fail the gate.** They're
   below `speaker_id_min_duration_ms` so they inherit; the decider then
   rejects inherited labels as fail-closed. User can't confirm with a
   quick "так" — has to say "так, роби" or similar longer phrase. This
   is technically correct per the security model but bad UX.

## Track B — Live-hardware fixes (priority)

### SPK2-B1: Fix `_prev_id` sticky-window leak
**Why:** Security: stale owner label can survive across a non-owner turn.
**Where:** `src/speaker_processor.py` `SpeakerTaggerProcessor._tag_transcription`.
**What:**
- Add `self._prev_at: float = 0.0` back (we deleted it during deslop).
- On every non-inherited tag with `sid != None`, set `self._prev_at = time.monotonic()`.
- On every non-inherited tag with `sid == None` (non-owner or low-conf),
  clear `self._prev_id = None` AND `self._prev_at = 0.0`.
- Short-turn inheritance check now gated on:
  ```python
  if (slot.duration_ms < min_ms
      and self._prev_id is not None
      and (time.monotonic() - self._prev_at) < settings.speaker_id_sticky_seconds):
      # inherit
  else:
      # set frame.speaker_id = None — fall through as unknown
  ```
**Tests to add in `tests/test_speaker_processor.py`:**
- `test_prev_id_cleared_on_non_owner_turn`
- `test_sticky_window_expires_after_timeout` — mock `time.monotonic` to
  advance past `sticky_seconds`
- `test_short_turn_after_stranger_does_not_inherit_owner`
**Acceptance:** all 3 tests pass + existing tagger tests still pass.

### SPK2-B2: Multi-segment audio accumulation for stable embeddings
**Why:** Root-cause fix for marginal 1.5-3s daemon clips. ECAPA stabilizes
at ~3s of audio; by accumulating sequential VAD segments from the same
speaker, we reach the stable regime without requiring users to speak
in longer bursts.
**Design choice:** the accumulator lives in `AudioBufferProcessor`, not in
the gallery or identify path. It holds an optional rolling "accumulation
buffer" that combines the last N seconds of PCM across multiple
Start/Stop turns, and exposes both the current turn's embedding AND an
accumulated embedding. The tagger uses the accumulated one when the
single-turn clip is under a threshold length.
**Where:**
- `src/speaker_processor.py` — new `_AccumBuffer` small class keeping a
  `collections.deque[bytes]` bounded by total duration (default ~4s),
  flushed on `BotStartedSpeakingFrame` (don't straddle TTS playback).
- Tagger: if `slot.duration_ms >= 3000` use single-turn; else embed the
  accumulated buffer (single embed, separate cost).
- New setting `speaker_id_accum_target_ms: int = 3000` in `src/config.py`.
**Alternative rejected:** stretching VAD `stop_secs` from 0.5 to 1.5 —
makes turn-taking sluggish; worse UX than accumulation.
**Tests:**
- `test_accum_buffer_rolls_over_duration_limit`
- `test_short_turn_uses_accumulated_embedding`
- `test_accum_buffer_flushed_on_bot_speaking`
- Integration: re-run `test_stranger_integration.py` scenarios — must
  still pass with accumulation enabled.
**Acceptance:** offline match harness with 3 x 1.5s clips stitched through
accumulation scores cos ≥ 0.75 against single-reference enrollment.

### SPK2-B3: Short confirmation word UX
**Why:** Current design rejects quick "так" because short turns inherit
and the decider rejects inherited confirmations.
**Design choice:** relax specifically for confirmation by adding a
`trusted_continuation` pathway — if the tagger sees a short turn within
`sticky_seconds` of the last owner-matched turn AND the accumulation
buffer (from B2) has sufficient recent owner content, the label is
promoted from `inherited=True` to `inherited=False` with a lower
confidence (e.g. 0.50 — above threshold_unknown but sentinel value).
The decider accepts it only in `AWAITING_CONFIRMATION` AND only when
the pending_speaker_id matches — same security envelope.
**Alternative:** add explicit wake-word requirement for confirmations
(say "Гава, так" instead of just "так"). Simpler, no new code paths,
but worse UX. Document as fallback if B2 accumulation alone doesn't
fix short-word reliability.
**Tests:**
- `test_short_confirmation_accepted_when_within_sticky_window`
- `test_short_confirmation_rejected_after_sticky_window_expires`
- `test_short_confirmation_rejected_when_gallery_owner_mismatch`
- End-to-end: say "Гава, запусти тести" then "так" within 3s — decider
  must execute.
**Acceptance:** confirming with a short "так" works when owner arms the
confirmation; stranger's "так" still rejected.

### Track B done → retest live
After B1/B2/B3 land: restart daemon, re-do the match-test session from
tonight, check that short turns pass AND stale-label leaks are gone.
Recommended ACs: owner match-rate ≥ 90% across 10 dialog-length
utterances, stranger reject-rate 100% in AWAITING_CONFIRMATION.

## Track A — Original Phase 2 feature set (after Track B)

Already spec'd in `.omc/plans/speaker-recognition.md` §3 Phase 2.
Summary of what Track A adds:

### SPK2-A1: Gallery growth with K=5 running centroid
- `src/speaker_gallery.py`: `update(speaker_id, v)` FIFO to last K=5
  per speaker; `get_centroid()` already averages (Phase 1).
- Anti-drift guard: reject `update` if `cos(new, centroid) < threshold`.
- Bring back `_lock: asyncio.Lock` that Phase 1 removed under YAGNI —
  now actually needed since heartbeat decay can write concurrent with
  tagger updates.

### SPK2-A2: Candidate auto-enrollment
- Track new unknown voices in a candidate pool.
- Promote to `speaker_2`, `speaker_3`, ... after 3 consecutive stable
  turns with mutual cos > 0.75 AND cos < 0.65 to any existing centroid
  (prevents drift-based false promotion).
- Config: `speaker_id_auto_enroll_after` (already in Phase 1 settings).

### SPK2-A3: Gallery management CLI
- `heare list-speakers` — print id, label, turn_count, updated_at, sample count
- `heare rename-speaker <id> <label>` — sanitize + save
- `heare forget-speaker <id>` — atomic remove
- Tests: argparse wiring + command dispatch.

### SPK2-A4: Drift audit on heartbeat
- Every N heartbeat ticks (default 24), log `gallery.summary()` at INFO
- New method `SpeakerGallery.summary()` → `list[dict]` with per-speaker
  metadata (turn_count, centroid-to-original cos, updated_at)
- Retires stale candidates (no turn_count update for 30 days AND
  `label is None` → deleted)
- Runs from `heartbeat.py` tick loop, not from the audio path — so the
  lock introduced in A1 is actually needed

### SPK2-A5: Non-owner context redaction
- Update `src/context.py` so non-owner transcripts render
  `recent_transcripts` as `(none)` not `(redacted)` — architect's
  Iteration-1 recommendation that Phase 1 declined. Revisit now that
  we have evidence from live tests.
- Ship with golden fixture update.

## Key constraints carrying over from Phase 1

- Feature OFF by default (`speaker_id_enabled=False` in Settings)
- Lazy speechbrain + torch imports via deferred `load_model`
- Tests MUST NOT touch the network — mock `speaker_id.embed` at module boundary
- Flag-off path must remain byte-for-byte identical on prompt rendering
  (golden fixture at `tests/fixtures/decider_prompt_flag_off.golden.txt`)
- `log_transcript` signature is stable — keyword-only speaker args with
  None defaults, do not break existing callers
- `prompts/decider.txt` flag-off rendering must not change — any new
  placeholder goes through the same `_render_rule_block` indirection

## Files touched in Phase 2

Track B:
- `src/speaker_processor.py` (tagger sticky window + accumulation buffer)
- `src/config.py` (new `speaker_id_accum_target_ms` setting)
- `src/decider.py` (short-confirmation acceptance path)
- `tests/test_speaker_processor.py` (B1, B2 tests)
- `tests/test_decider.py` (B3 tests)
- `tests/test_stranger_integration.py` (regression against scenarios)

Track A:
- `src/speaker_gallery.py` (K=5 running centroid, lock, summary, candidates)
- `src/context.py` (non-owner redaction to `(none)`)
- `src/main.py` (list/rename/forget CLI)
- `src/heartbeat.py` (drift audit hook)
- `tests/test_speaker_gallery.py` (candidate promotion, drift, lock)
- `tests/test_main_cli.py` (CLI wiring)
- `tests/test_context.py` (redaction regression)
- `tests/fixtures/decider_prompt_flag_off.golden.txt` (re-capture if
  rule block changes)

## Open questions for fresh session

1. **Should Track B be planned via ralplan or just ralph?** Narrow scope
   (~3 stories) argues for inline planning and direct ralph. But B1 is
   security-relevant, so a light architect review on the sticky-window
   state machine wouldn't hurt.
2. **Accumulation buffer flush semantics:** flush on
   `BotStartedSpeakingFrame` is obvious (don't straddle TTS). What about
   flush on `UserStartedSpeakingFrame` after >Xs of silence? Needs
   measurement.
3. **Should B3 (short confirmation UX) ship if B2 (accumulation) alone
   makes short words reliable?** If accumulation gives us a real embedding
   for short turns, we don't need the `trusted_continuation` special case.
   Test B2 alone first; only do B3 if B2's accumulation doesn't close the gap.
4. **Track A's drift audit:** log format should be JSON-serializable so
   the future web UI can parse it. Consistent with existing
   `[TIMING]` / `[SPEAKER]` log format or break out into a separate
   logger namespace?
5. **Non-owner context redaction (A5):** the plan originally declined
   architect's recommendation to use `(none)` instead of
   `(redacted — non-owner speaker)`. Revisit decision based on any real
   Claude responses we saw in today's live test (currently inconclusive
   since non-owner path was never reached in a way that tested prompt
   leakage).

## How to start in the fresh session

1. Read this file + the original plan header from
   `.omc/plans/speaker-recognition.md` for the architecture context.
2. Check `~/.heare/config.toml` — the runtime thresholds should still be
   set from tonight's calibration.
3. Decide: ralplan or direct ralph for Track B? (Probably direct ralph
   for B1, maybe a short architect review for B3 since it touches the
   security gate.)
4. Start with **SPK2-B1** (prev_id sticky window). Smallest, security-
   critical, unblocks reasoning about the whole state machine.
5. Then **SPK2-B2** (accumulation buffer). Largest piece; runs offline
   match harness to verify the confidence bump.
6. Then **SPK2-B3** OR skip it if B2 alone fixes short-word reliability.
7. Re-run the live daemon test from tonight's session to verify.
8. Only then proceed to Track A.

## Reference — what NOT to re-plan

- Architecture decisions (ECAPA on-device, off-by-default, lazy imports,
  fire-and-forget embed) are locked in from Phase 1 consensus.
- Security invariants (fail-closed on confirmation, no `speaker_label`
  in logs, label sanitization on rename) carry over and are non-negotiable.
- Golden-string flag-off regression test stays as the canary.
