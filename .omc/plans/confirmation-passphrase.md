# Plan — Confirmation Passphrase (Additive Gate)

**Status:** implemented ✓
**Owner:** executor
**Target files:** `src/config.py`, `src/decider.py`, `tests/test_decider.py`
**Complexity:** LOW (≈30 LOC + 6 tests, no new dependencies)

---

## Context

`DeciderProcessor._handle_confirmation` currently gates confirmation on
`has_keyword AND is_same_speaker` when `speaker_id_enabled=True`. Short confirmation
utterances (<400 ms, below `speaker_id_min_duration_ms=400`) routinely resolve
`speaker_id=None`, so the AND gate fails and the user is stuck in the
`"Скажи: гава так, або гава ні"` reprompt loop.

The fix adds an **additive shared-secret path**: `<wake-word> <passphrase>` executes the
pending action. It is a second way in — **not** a replacement for the existing
yes/no + speaker-id flow. `"гава так"` must continue to work exactly as before.

## Design (additive, not replacement)

When `confirmation_passphrase` is configured:

1. **Redact before logging.** The transcript is logged AFTER redacting the passphrase
   (case-insensitive replace with `"***"`) so SQLite never stores the secret verbatim.
2. **Success-only early return.** The passphrase branch early-returns **only** on
   successful match. On no match, it falls through to the existing yes/no + speaker-id
   flow so `"гава так"` still works.
3. **Biometric override.** If speaker-id is enabled AND speaker-id positively identifies
   a NON-owner (`speaker_id is not None and speaker_id != pending_speaker_id`), the
   passphrase is rejected even on match — the daemon knows it is not the owner. When
   `speaker_id is None` (short utterance, unknown) OR matches the pending speaker, the
   passphrase is accepted (graceful degrade, the whole point of the feature).

## Work Objectives

1. Add `confirmation_passphrase: str | None = None` to `Settings`, TOML-loadable, with a
   `load_settings` warning if a non-empty value has length < 5.
2. Add `_redact_passphrase(transcript, passphrase)` helper in `decider.py`.
3. Redact the transcript before the existing `log_transcript` call.
4. Insert the **additive** passphrase gate after `log_transcript` and BEFORE
   `parse_yes_no`, with success-only early return and biometric override.
5. Six new tests covering success / stranger-reject / unknown-speaker-accept /
   fallthrough-to-так / no-keyword-fallthrough / passphrase-None-unchanged.
6. Preserve all current behaviour when `confirmation_passphrase is None`.

## Guardrails

**Must Have**
- Passphrase branch runs only when `settings.confirmation_passphrase` is a non-empty string.
- Success match = `has_keyword` (via `_keyword_is_adjacent_prefix`) AND passphrase
  substring (both sides `.strip().lower()`).
- Biometric override rejects on positively-identified stranger:
  `speaker_id_enabled AND not speaker_inherited AND speaker_id is not None
  AND speaker_id != pending_speaker_id`.
- Graceful degrade on `speaker_id is None` or owner match → execute.
- On no passphrase match: **fall through** (no return) to the existing yes/no +
  speaker-id logic, which handles `"гава так"` as today.
- Transcript redacted via `_redact_passphrase` before `log_transcript`.
- `_cancel_pending` / `_execute_pending` / `pending_decision_id` / `pending_speaker_id`
  lifecycle untouched.

**Must NOT Have**
- No replacement of the existing yes/no path — passphrase branch only early-returns on
  success or stranger-reject reprompt.
- No regex-based passphrase parsing in v1 — plain substring on normalized strings.
- No environment variable override (config.toml only).
- No logging of the passphrase itself. The `log_transcript` call receives the redacted
  string. The `logger.info` on success is phrase-free.
- No change to `speaker_id_enabled`, `speaker_command_keyword_required`, or defaults.
- No new events, no new decision types, no Pipecat-frame plumbing.

---

## Task Flow

```
[1] config.py           → add field + len<5 warning
      │
[2] decider.py          → add _redact_passphrase helper
      │                 → redact transcript before log_transcript
      │                 → insert additive passphrase gate
      │                 → extend FIXED_PHRASES
      │
[3] tests/test_decider.py → 6 new tests
      │
[4] Verify: pytest + ruff + live test
```

---

## Detailed TODOs

### Step 1 — `src/config.py`: add `confirmation_passphrase`

**Edit 1a.** Insert field in `Settings` dataclass grouped with the speaker/keyword
confirmation-gate block (after `speaker_command_keyword_required`, before
`command_keyword_pattern`):

```python
    speaker_command_keyword_required: bool = True
    # Optional shared-secret phrase. When non-empty, saying
    # `<wake-word> <passphrase>` confirms a pending action. The passphrase
    # path is ADDITIVE — the existing yes/no + speaker-id flow still works.
    # Set via ~/.heare/config.toml only. Never logged (redacted in transcripts).
    # Recommend 2+ rare Cyrillic words for unambiguous STT recognition.
    confirmation_passphrase: str | None = None
    command_keyword_pattern: str = r"\b(гава|heare|гей)\b"
```

**Edit 1b.** In `load_settings()` (after the existing TOML overlay loop), add a warning
for short passphrases:

```python
    if settings.confirmation_passphrase is not None:
        phrase = settings.confirmation_passphrase.strip()
        if phrase and len(phrase) < 5:
            logger.warning(
                "confirmation_passphrase is very short (len=%d); "
                "recommend 5+ chars / 2+ words to avoid STT false-positives",
                len(phrase),
            )
```

**Acceptance:**
- `Settings().confirmation_passphrase is None`.
- TOML `confirmation_passphrase = "авторизую"` loads via the existing generic overlay.
- Short-passphrase warning fires once at load, not at runtime.

---

### Step 2 — `src/decider.py`: helper + redaction + additive gate

**Edit 2a — add `_redact_passphrase` helper.** Place near the existing
`_keyword_is_adjacent_prefix` helper (top of the file, module-level).

```python
def _redact_passphrase(transcript: str, passphrase: str | None) -> str:
    """Return ``transcript`` with any case-insensitive occurrence of
    ``passphrase`` replaced by ``"***"``. Returns the input unchanged if
    ``passphrase`` is None or empty. Used to keep the secret out of
    SQLite transcript logs.
    """
    if not passphrase:
        return transcript
    phrase = passphrase.strip()
    if not phrase:
        return transcript
    # Case-insensitive find/replace without regex special-char surprises.
    lower_t = transcript.lower()
    lower_p = phrase.lower()
    idx = lower_t.find(lower_p)
    if idx == -1:
        return transcript
    out_parts: list[str] = []
    cursor = 0
    plen = len(lower_p)
    while idx != -1:
        out_parts.append(transcript[cursor:idx])
        out_parts.append("***")
        cursor = idx + plen
        idx = lower_t.find(lower_p, cursor)
    out_parts.append(transcript[cursor:])
    return "".join(out_parts)
```

**Edit 2b — extend `FIXED_PHRASES`** (src/decider.py:65):

```python
FIXED_PHRASES: list[str] = [
    "okay",
    "nevermind, cancelled",
    "Скажи: так чи ні?",
    "дія не вдалася",
    "Скажи: гава так, або гава ні",
    "Скажи пароль, або гава ні",
]
```

**Edit 2c — redact before `log_transcript` (around line 721).** Replace the existing
call so the secret never hits SQLite:

```python
            # Redact passphrase before any persistence. The transcript is
            # stored verbatim in SQLite otherwise, which would leak the
            # shared secret into backups, logs, and drift-audit dumps.
            redacted_transcript = _redact_passphrase(
                transcript, self.settings.confirmation_passphrase
            )
            await self.store.log_transcript(
                redacted_transcript,
                decision_id=self.pending_decision_id,
                # ...existing kwargs unchanged...
            )
```

*(Keep every other kwarg of the existing `log_transcript` call byte-identical. The only
change is the first positional / `text` argument from `transcript` → `redacted_transcript`.)*

**Edit 2d — insert the additive passphrase gate AFTER `log_transcript` and BEFORE
`verdict = parse_yes_no(transcript)`.** This branch early-returns **only** on match
(success or positively-stranger). On no match it falls through to the existing flow.

```python
            # Additive passphrase gate. Runs AFTER log_transcript (which
            # now gets the redacted transcript) and BEFORE parse_yes_no.
            # On passphrase match → execute (or reprompt if speaker-id
            # positively identifies a stranger). On no match → fall through
            # to the existing yes/no + speaker-id flow so "гава так" still
            # works exactly as today.
            if self.settings.confirmation_passphrase:
                passphrase = self.settings.confirmation_passphrase.strip().lower()
                normalized = transcript.strip().lower()
                has_keyword_for_passphrase = _keyword_is_adjacent_prefix(
                    transcript, self._command_keyword_re
                )
                if (
                    passphrase
                    and has_keyword_for_passphrase
                    and passphrase in normalized
                ):
                    # Biometric override: if speaker-id is enabled AND
                    # positively identifies a non-owner, reject even on
                    # passphrase match. `speaker_id is None` means unknown
                    # (short utterance) — graceful degrade to execute.
                    stranger_positively_identified = (
                        self.settings.speaker_id_enabled
                        and not speaker_inherited
                        and speaker_id is not None
                        and speaker_id != self.pending_speaker_id
                    )
                    if stranger_positively_identified:
                        logger.info("passphrase matched but speaker-id is stranger; rejecting")
                        self._safe_emit(
                            EventKind.ACTION_REPROMPT,
                            decision_id=self.pending_decision_id,
                        )
                        await self.push_frame(
                            TTSSpeakFrame("Скажи пароль, або гава ні")
                        )
                        return
                    logger.info("confirmation via passphrase")
                    self._safe_emit(
                        EventKind.ACTION_CONFIRMED,
                        decision_id=self.pending_decision_id,
                    )
                    await self._execute_pending()
                    return
                # No passphrase match → fall through to existing yes/no
                # + speaker-id flow. Do NOT return here.

            # Existing flow below — unchanged.
            verdict = parse_yes_no(transcript)
            ...
```

**Acceptance:**
- `confirmation_passphrase is None` → zero behavioural change.
- `log_transcript` always receives a redacted string when passphrase is set and appears
  in the transcript.
- `has_keyword + passphrase in transcript + (speaker_id None OR owner)` → execute +
  return.
- `has_keyword + passphrase in transcript + speaker_id is positively stranger` →
  reprompt + return.
- `has_keyword + no passphrase in transcript` → fall through; existing `parse_yes_no`
  flow executes `"гава так"` as today.
- Passphrase branch never appears in logs as plaintext.

---

### Step 3 — `tests/test_decider.py`: 6 new tests

Append after the existing confirmation-gate tests (near
`test_owner_drift_confirmation_fails_with_speaker_id_enabled`). Use the established
harness + `decider.push_frame = AsyncMock()` + manual-arm pattern from
`test_stranger_keyword_confirmation_rejected`.

| # | Test name | Asserts |
|---|-----------|---------|
| 1 | `test_passphrase_executes` | `confirmation_passphrase="авторизую"`, arm decider, feed `"гава авторизую"` with `speaker_id=None`, `speaker_inherited=False` → `cli.call_action.assert_awaited()`, `state == LISTENING`, `pending_action is None`. |
| 2 | `test_passphrase_stranger_rejected` | `speaker_id_enabled=True`, `pending_speaker_id="owner"`, `confirmation_passphrase="авторизую"`; feed `"гава авторизую"` with `speaker_id="stranger_01"`, `speaker_inherited=False` → `call_action.assert_not_awaited()`, `state == AWAITING_CONFIRMATION`, one pushed `TTSSpeakFrame` text == `"Скажи пароль, або гава ні"`. |
| 3 | `test_passphrase_unknown_speaker_executes` | `speaker_id_enabled=True`, `pending_speaker_id="owner"`, `confirmation_passphrase="авторизую"`; feed `"гава авторизую"` with `speaker_id=None` → `call_action.assert_awaited()` (graceful degrade on unknown). |
| 4 | `test_passphrase_fallthrough_to_tak` | `confirmation_passphrase="авторизую"`, `speaker_id_enabled=False`; feed `"гава так"` (passphrase configured but transcript has no passphrase) → executes via the existing yes/no path (`call_action.assert_awaited()`). Proves additive design: passphrase configured does NOT break `"гава так"`. |
| 5 | `test_passphrase_no_keyword_fallthrough` | `confirmation_passphrase="авторизую"`, feed `"авторизую"` (passphrase, no wake word) → passphrase branch falls through (no keyword); `parse_yes_no("авторизую")` returns None → existing `"Скажи: так чи ні?"` reprompt path fires; `call_action.assert_not_awaited()`, `state == AWAITING_CONFIRMATION`. |
| 6 | `test_passphrase_none_unchanged` | `confirmation_passphrase=None`, `speaker_id_enabled=False`; feed `"гава так"` → executes via existing yes/no path. Smoke test that the branch is fully dormant when unset. |

**Test skeleton template:**

```python
async def test_passphrase_executes(harness) -> None:
    store, settings, ctx = harness
    settings.confirmation_passphrase = "авторизую"
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    decider.state = DeciderState.AWAITING_CONFIRMATION
    decider.pending_action = {"type": "act", "intent": "test", "action": "test"}
    decider.pending_speaker_id = "owner"
    decider.confirmation_deadline = time.monotonic() + 30

    await decider._handle_confirmation(
        "гава авторизую", speaker_id=None, speaker_inherited=False
    )
    cli.call_action.assert_awaited()
    assert decider.state == DeciderState.LISTENING
    assert decider.pending_action is None
```

**Acceptance:** all 6 tests pass; no existing test regresses.

---

### Step 4 — Verification

Run, in order:

1. **Unit tests:**
   ```
   pytest tests/test_decider.py -x -q
   ```
   Expect existing + 6 new tests green.

2. **Full suite:**
   ```
   pytest -x -q
   ```

3. **Lint:**
   ```
   ruff check src/config.py src/decider.py tests/test_decider.py
   ruff format --check src/config.py src/decider.py tests/test_decider.py
   ```

4. **Manual live test:**
   - Add `confirmation_passphrase = "авторизую"` to `~/.heare/config.toml`.
   - Restart daemon: `heare stop && heare start`.
   - Say `"Гава, запусти тести"` → daemon arms confirmation.
   - Say `"гава авторизую"` → action executes.
   - Arm again, say `"гава так"` → action still executes via existing path (additive proof).
   - Arm again, say `"гава привіт"` → existing reprompt path.
   - Arm again, say `"гава ні"` → cancels via existing path.

5. **Log + DB hygiene check:**
   ```
   grep -i "авториз\|passphrase" ~/.heare/logs/*.log
   sqlite3 ~/.heare/state.db "select text from transcripts order by id desc limit 20;"
   ```
   Expect: only `"confirmation via passphrase"` / `"passphrase matched but speaker-id is stranger"`
   info-level entries. SQLite transcripts show `"гава ***"` instead of the plaintext
   passphrase.

---

## Success Criteria

- [ ] `Settings.confirmation_passphrase` field exists, defaults `None`, loads from TOML.
- [ ] `load_settings` warns once if non-empty passphrase length < 5.
- [ ] `_redact_passphrase` helper present and correct (case-insensitive, multi-occurrence).
- [ ] `log_transcript` receives the redacted transcript when passphrase is set.
- [ ] Passphrase gate is ADDITIVE: early-returns only on match (success or stranger-reject).
  Falls through on no match so `"гава так"` still works.
- [ ] Stranger (positively identified) is rejected even on passphrase match.
- [ ] Unknown speaker (`speaker_id=None`) gracefully degrades to execute on match.
- [ ] All 6 new tests pass; all existing decider tests pass unchanged.
- [ ] Ruff clean on touched files.
- [ ] Live test: passphrase confirms, `"гава так"` still confirms, stranger rejected,
  SQLite shows redacted transcript.

---

## RALPLAN-DR

### Principles
1. **Additive over replacement.** The passphrase branch early-returns **only** on
   match. On no match it falls through, so the existing yes/no + speaker-id flow is
   byte-identical to today.
2. **Biometric signal wins when available.** A positively-identified stranger is
   rejected even on passphrase match. Unknown speaker (`None`) degrades to accept —
   that is the feature.
3. **Secrets never hit storage.** Transcript is redacted before `log_transcript`;
   success logs are phrase-free.
4. **Config-gated rollout.** Default `None` = zero change for existing users.
5. **No parser rewrite.** Reuse `_keyword_is_adjacent_prefix` and `parse_yes_no`.

### Decision Drivers (top 3)
1. **Unblock the user today** without regressing the biometric-gated rejection of
   `"гава так"` by a stranger.
2. **Preserve additive semantics.** Setting a passphrase must not remove the ability
   to say `"гава так"` — it adds a second way in.
3. **Secret hygiene.** The SQLite transcript store is a first-class data sink; the
   passphrase must be redacted there, not only in structured logs.

### Options Considered

**Option A (chosen): additive passphrase gate, redact-before-log, biometric override.**
- Pros: ≈30 LOC, byte-identical when feature is off, `"гава так"` preserved,
  stranger-reject preserved via biometric override, transcript store never sees the
  secret, six tests cover the full matrix.
- Cons: substring match could theoretically false-positive on a common-word passphrase;
  mitigated by user-controlled phrase + len<5 warning.

**Option B (rejected — original plan): passphrase REPLACES the yes/no path when set.**
- Invalidated by Architect finding #1: makes `"гава так"` unreachable. Regresses the
  working path. Users who set a passphrase lose the existing confirmation gesture.

**Option C (rejected): passphrase without biometric override.**
- Invalidated by Architect finding #3: when speaker-id positively identifies a
  non-owner, the daemon already knows it is not the owner. Accepting the passphrase
  anyway turns biometrics into a bypass switch for the stranger guarantee.

**Option D (rejected): log transcript verbatim, redact only structured logs.**
- Invalidated by Architect finding #2: `log_transcript` writes to SQLite which is
  backed up, drift-audited, and exported. Redaction must happen before that write.

**Option E (deferred): regex-based passphrase with word-boundary anchoring.**
- Not required for v1; can be layered on later if false-positives appear in practice.

### Why this approach
Option A is the smallest change that (a) fixes the reported failure mode, (b) keeps
the existing `"гава так"` gesture working for users who configure a passphrase,
(c) preserves the stranger-rejection guarantee when biometrics have a positive
identification, and (d) keeps the shared secret out of persistent storage. Options B,
C, and D were each invalidated by a specific Architect finding; Option E is a future
refinement.

### ADR

- **Decision:** Add an **additive** passphrase branch to `_handle_confirmation` that
  early-returns only on match, with biometric override against positively-identified
  strangers, and redact the transcript before persistence.
- **Drivers:** unblock stuck confirmation path; preserve `"гава так"`; preserve
  stranger-rejection; keep secret out of SQLite.
- **Alternatives considered:** replacement gate (B), no biometric override (C),
  log-time-only redaction (D), regex anchoring (E).
- **Why chosen:** only approach that satisfies all three blocking findings
  simultaneously with a minimal diff.
- **Consequences:** users opting in gain a reliable short-utterance confirmation path;
  users not opting in see zero change. Slightly more complex `_handle_confirmation`
  (one extra branch, ≈25 lines). SQLite transcripts with passphrase set now contain
  `"***"` markers where the secret was spoken, which is fine for drift audit.
- **Follow-ups:** regex-hardened matching (Option E) if false-positives appear;
  hashed passphrase storage if threat model expands to shared home directories.

---

## Open Questions (none blocking)

- Should the stranger-reject reprompt also emit a distinct event kind (e.g.
  `ACTION_REJECTED_BIOMETRIC`) for analytics? Current design reuses `ACTION_REPROMPT`
  to avoid schema changes.
- Should short-passphrase warning become a hard error? Current design warns only to
  avoid breaking ergonomic test configs (e.g. `"auth"` in local dev).
