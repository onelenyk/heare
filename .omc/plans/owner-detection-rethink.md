# Owner Detection Rethink — Speaker-as-Tag + Command Keyword Gate

**Status:** Draft, awaiting user confirmation
**Mode:** Ralplan consensus (SHORT)
**Owner:** Nazar
**Touches:** `src/decider.py`, `src/config.py`, `src/context.py`, `src/pipeline.py`, `prompts/decider.txt`, `tests/`

---

## Context

The current owner-detection design treats ECAPA-TDNN speaker-id as a **hard security gate**:

1. `src/decider.py:524` — in `_handle_listening`, any transcript where `speaker_id != "owner"` is dropped silently. Claude never sees it.
2. `src/decider.py:697` — in `_handle_confirmation`, any inherited-label short turn is rejected with `"Скажи: так чи ні?"`.
3. `src/decider.py:704` — in `_handle_confirmation`, any `speaker_id != pending_speaker_id` is rejected the same way.
4. `src/pipeline.py:96` — `speaker_id_enabled` controls whether the whole AudioBuffer + SpeakerTagger pipeline is even built.
5. `src/decider.py:162-206` — `YES_PATTERNS` / `NO_PATTERNS` are scanned with substring regex. YES wins on any match. A standalone `"не так"` currently returns `"yes"` (YES scanned first, `\bтак\b` matches inside). A full sentence `"так не заважай мені"` also returns `"yes"`. Parser does not require the utterance to be a standalone yes/no; it leaks through.
6. `src/decider.py:73` — `WAKE_WORD_PATTERN = r"\b(гава|heare|гей)\b"` already exists but only bypasses `is_quick_nothing` filtering in RULE 0 (`src/decider.py:139`). It does **not** gate actions.

**User pain point:** embedding drift. A mild voice change (cold, angle, background) drops owner below `speaker_id_threshold_match=0.75` and the decider goes silent with no recourse. Today the gate is binary; there is no "I know it's probably me, let me just say the magic word" fallback.

**User's design direction:**
- Speaker ID stays, but as **metadata only** (a prompt tag, an audit field).
- A **command keyword** — reusing/extending the existing wake word — becomes the hard requirement for executing actions or confirming pending ones.
- Yes/No parser is tightened to require standalone utterances and fixes the `\bне\b` false positive.
- For `speak` decisions, strangers can still reach Claude and receive a TTS reply — this is intentional ("speaker as tag" behavior). `act` decisions remain keyword-gated.

---

## RALPLAN-DR Summary

### Principles
1. **Drift-resilient without being a doormat.** A slightly different owner voice must still be able to command Heare. A completely unknown voice with no keyword must not execute actions.
2. **Speaker ID is signal, not gate.** Embedding similarity is fuzzy by nature — surface it to Claude as context, don't use it as a binary authorization check.
3. **Keyword gate is explicit and learnable.** The user must know the word. Background conversation and TV chatter cannot accidentally trigger actions.
4. **Fail-closed on ambiguity, fail-open on strictness.** When speaker-id is uncertain but the keyword is present, ask for confirmation. When both fail, drop silently.
5. **Backward-compatible off switch.** `speaker_id_enabled=False` must render prompts byte-identically to today. All new behavior flag-gated.

### Decision Drivers (top 3)
1. **User experience:** drift must not silently break command flow. The user wants to *feel* like Heare is still listening and controllable.
2. **Security:** strangers (TV, guests, passers-by) must not be able to trigger actions. The existing 3 stranger integration tests (`tests/test_stranger_integration.py`) must continue to pass *in spirit* — the threat model is preserved, even if the mechanism moves from "speaker gate" to "keyword gate".
3. **Implementation cost and blast radius:** the FSM, the speculative prompt path (`_prompt_for_transcript`), the rule block in `context.py`, and 8 tests in `test_decider.py` all touch `speaker_id_enabled`. Changes must be surgical, not a rewrite.

### Viable Options

**Option A — Keyword-gated commands, speaker as tag, AND-gated confirmations (RECOMMENDED)**
- `_handle_listening`: drop the `speaker_id != "owner"` hard gate for `act` decisions. Add a check: if Claude returns `act`, require `COMMAND_KEYWORD_PATTERN` in the transcript; no keyword → drop at keyword gate (logged as `decider_dropped_no_keyword`). `speak` decisions are **intentionally NOT gated** — strangers reach Claude and can receive TTS replies (user's "speaker as tag" design). Claude sees `[speaker:<label>]` in the prompt and decides accordingly.
- `_handle_confirmation`: **keyword-AND-speaker** (iteration 2 Critic fix). When `speaker_id_enabled=True`: require BOTH `has_keyword` (adjacent-prefix check) AND `is_same_speaker` (speaker_id == pending_speaker_id, not inherited). When `speaker_id_enabled=False`: fall back to keyword-only. Inherited labels never count as same-speaker. Drift does not auto-confirm via keyword — the user must re-enroll.
- Yes/no parser: require standalone utterance (length + position constraints) and fix `\bне\b` false-positive by making NO patterns exclusive-word-boundary and requiring the negation to be the dominant token. A separate helper `_keyword_is_adjacent_prefix` is added for the confirmation caller to enforce keyword-at-head adjacency (N=3 tokens); `parse_yes_no` stays pure.
- Pipeline: decouple `speaker_id_enabled` from gate behavior — the flag keeps controlling whether the AudioBuffer + Tagger stages are built, but new `speaker_command_keyword_required: bool = True` controls the keyword gate.

Pros:
- Drift-resistant at the LISTENING gate: even if speaker-id fails, the keyword lets the real owner through to Claude and arms a confirmation.
- Fail-closed at the CONFIRMATION gate: AND-logic blocks stranger keyword spoofing when speaker-id is on.
- Existing wake-word infra reused; minimal new surface.
- Claude gets richer context via `[speaker:owner]` tag and can reason about it.
- Stranger threat model preserved: stranger without keyword = drop; stranger with keyword = can arm but AND-logic blocks confirmation.

Cons:
- User must remember to say `гава` / `heare` / `гей` for commands AND for every confirmation (no bare `"так"`). Slightly more verbose.
- Claude now sees non-owner transcripts — ensures context is not over-redacted, but means the audit story for "strangers never reach the LLM" changes. `speak` traffic from strangers is a known accepted cost (follow-up for rate-limit).
- Drift during an active confirmation window is fail-closed — user must re-enroll, not recover via keyword. This is the deliberate tradeoff for preserving the spoof guard.

**Option B — Tiered confidence with fallback prompt (alternative)**
- Keep speaker-id gate when confidence ≥ `threshold_sticky`.
- Between `threshold_match` and `threshold_sticky`: ask owner to confirm identity via a challenge ("Скажи: гава, це я").
- Below `threshold_match`: drop.
- Yes/no parser: same tightening as Option A.

Pros:
- Preserves the "strangers never reach Claude" audit story verbatim.
- Adaptive to real drift: drift triggers a challenge, not silent failure.

Cons:
- More complex FSM: new `IDENTITY_CHALLENGE` state, more TTS prompts, more timing edge cases.
- Does not match user's stated intent ("leave detection, but only as a tag").
- Still doesn't solve ambient-command-without-keyword case; user must still explicitly authenticate.

**Option C — Pure keyword gate, disable speaker-id entirely (simplest)**
- Remove speaker-id from the decider flow completely. Rely on keyword + yes/no only.
- Keep `speaker_gallery.py` and the tagger pipeline for transcript audit (store speaker_id alongside transcripts) but never read it in `decider.py`.

Pros:
- Simplest possible fix. Drift becomes irrelevant.
- Zero cost for stranger-keyword awareness: they can say the word if they know it, which is the same threat model.

Cons:
- Loses the per-turn speaker signal for Claude — Claude can't distinguish "my wife in the background saying гава as a joke" from "the owner wanting action".
- Doesn't use the ECAPA embedding infra that's already built and tested.
- User explicitly said "let's leave detection — but only as a tag", so this throws away something they want.

**Recommendation: Option A.** It matches user intent, reuses existing infra, solves drift, and preserves the threat model through a different mechanism (keyword instead of embedding). Option B is too heavy. Option C throws away working work.

---

## Guardrails

### Must Have
- `speaker_id_enabled=False` renders `prompts/decider.txt` byte-identically to today (golden test in `test_context.py` stays green).
- Existing 3 stranger integration tests (`tests/test_stranger_integration.py`) must still prove the threat model — updated to use keyword-gate language but same outcomes: stranger without keyword cannot reach EXECUTING; stranger interrupting owner's confirmation cannot execute.
- `tests/test_yes_no.py` gains cases for `"не так"`, `"так не роби"`, `"так, але не зараз"`, `"так, не заважай"` — all must NOT return `"yes"`.
- Drift case covered for listening (arms): new test `test_owner_voice_drift_with_keyword_still_commands` — speaker_id=None but `гава` is in the transcript → reaches Claude, can arm confirmation.
- Drift case covered for confirmation (fail-closed): new test `test_owner_drift_confirmation_still_needs_reenroll` — armed with pending_speaker_id=None, owner says `"гава так"`, AND-logic rejects, user is directed to re-enroll.
- Stranger spoof blocked: new test `test_stranger_keyword_confirmation_rejected` — armed by owner, stranger says `"гава так"`, AND-logic rejects.

### Must NOT Have
- No new state in `DeciderState`. Stays `LISTENING → AWAITING_CONFIRMATION → EXECUTING`.
- No changes to `speaker_id.py`, `speaker_gallery.py`, `speaker_processor.py` — speaker-id pipeline stays identical; only its *consumers* in `decider.py` change.
- No break of the speculative-prompt path (`_prompt_for_transcript`, `_build_speculative` — `decider.py:268-426`). Speculative prompt still renders with `{speaker_rule_block}` as a keep-placeholder and substitutes at exec time.
- No prompt to Claude asking it to re-validate the speaker. The keyword gate is in Python, pre-Claude.
- No OR-gate between keyword and speaker in `_handle_confirmation` when `speaker_id_enabled=True`. The iteration-1 OR version collapsed the fail-closed spoof guard; AND-logic is mandatory.

### Risk & Pre-Mortem Entries

Added in iteration 2 (Critic fix):

1. **Stranger shouts `"гава так"` during 8s confirmation window.**
   - `speaker_id_enabled=True` (production): AND-logic blocks this — `has_keyword=True` but `is_same_speaker=False` because the stranger's embedding does not match `pending_speaker_id`. Confirmation rejected. No action. (Fail-closed — this is the Critic's primary concern from iteration 1, now fixed.)
   - `speaker_id_enabled=False` (pure-keyword deployment): keyword + adjacency is the ONLY guard. A stranger who knows the keyword and yells `"гава так"` within 8s WILL confirm. **Accepted risk** for pure-keyword mode — operators who disable speaker-id explicitly opt into this threat model. Mitigation: operator picks a non-obvious keyword via config.toml; follow-up for optional `--strict-speaker` deploy flag that refuses to run without ECAPA.

2. **Owner's voice drifts below threshold during an active pending confirmation.**
   - AND-logic will reject every `"гава так"` from the drifted embedding. User is stuck in a TTS reprompt loop until the 8s window expires. Mitigation: the pending window returns to LISTENING after 8s; user can then run `heare speaker enroll --refresh` to re-seed the gallery; subsequent commands work. This is the explicit cost of preserving the spoof guard. Follow-up: a dedicated "drift detected, re-enroll?" TTS branch when speaker_confidence sits between `threshold_match` and a new `threshold_suspect` band.

3. **Legitimate owner says bare `"так"` with no keyword during armed state.**
   - AND-logic rejects (has_keyword=False). Slight UX regression vs. pre-iteration-2 OR revision, where speaker match alone was sufficient. Rationale: requiring the keyword prefix on every confirmation is the only way to prevent ambient conversational `"так"` from accidentally firing an armed action. Documented in ADR as a deliberate UX tradeoff.

---

## Task Flow

```
1. Yes/No hardening (src/decider.py:162-206, tests/test_yes_no.py)
2. Command keyword config + pattern (src/config.py, src/decider.py:73)
3. Decider LISTENING rewrite (src/decider.py:514-631)
4. Decider CONFIRMATION rewrite (src/decider.py:682-733)
5. Rule block + speaker tag in prompt context (src/context.py:49-52, prompts/decider.txt)
6. Integration tests update + drift case (tests/test_stranger_integration.py, tests/test_decider.py, new tests)
```

---

## Detailed TODOs

### TODO 1 — Harden `parse_yes_no` (src/decider.py:162-206)

**Change:** full rewrite following the head-anchored, vocative-tolerant pattern. Replace the substring-scan approach with a prefix-based one plus a negation override. Pseudocode (validated against 29/31 real cases during planning — see "Parser Validation" appendix below):

```python
# Vocative prefix that we strip so "гава так" parses as "так".
# Kept in sync with Settings.command_keyword_pattern — in TODO 2 this
# should read from settings, not be a module constant.
_VOCATIVES = re.compile(r"^\s*(гава|heare|гей)[\s,]+", re.IGNORECASE)

_YES_HEAD = re.compile(
    r"^(так|да|ага|окей|ok|yes|yeah|sure|go|давай|зроби|вперед|конечно|красава)\b",
    re.IGNORECASE,
)
_NO_HEAD = re.compile(
    r"^(ні|нет|не|nevermind|cancel|stop|skip|no|abort)\b",
    re.IGNORECASE,
)
# "так не роби", "давай не зараз" — first YES token immediately followed
# by standalone "не" inverts to NO.
_YES_THEN_NE = re.compile(
    r"^(так|да|ok|yes|давай|ага|окей)\s+не\b", re.IGNORECASE
)
# "не треба", "не потрібно", "не зараз" — tail negation after a YES head.
_NEGATION_TAIL = re.compile(
    r"\bне\s+(треба|потрібно|зараз|роби|хочу|робимо)\b", re.IGNORECASE
)

MAX_YES_NO_WORDS = 4  # short utterance gate

def parse_yes_no(text: str) -> str:
    raw = text.strip().lower()
    if not raw:
        return "unclear"
    # Strip leading vocative so "гава так" → "так", "гава, ні" → "ні"
    cleaned = _VOCATIVES.sub("", raw)
    # Normalise punctuation and whitespace for head matching + word count
    cleaned = re.sub(r"[\.,\!\?]+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "unclear"
    words = cleaned.split()
    if len(words) > MAX_YES_NO_WORDS:
        return "unclear"
    # Lone "не"
    if cleaned == "не":
        return "no"
    # NO head wins unconditionally
    if _NO_HEAD.match(cleaned):
        return "no"
    # YES head + negation inversion
    if _YES_HEAD.match(cleaned):
        if _YES_THEN_NE.match(cleaned):
            return "no"
        if _NEGATION_TAIL.search(cleaned):
            return "no"
        return "yes"
    return "unclear"
```

Key design points:
- **Head-anchored, not substring-scanned.** The utterance must START with a yes/no token (after optional vocative strip). This kills false positives where a polite or rambling sentence happens to contain `так`.
- **Vocative tolerance.** `"гава так"` is a valid confirmation because Option A's confirmation path (TODO 4) REQUIRES the keyword alongside yes/no. The parser strips the vocative so confirmations naturally parse.
- **Negation inversion on YES head.** `"так не роби"` / `"так не треба"` semantically means NO. Under the 4-word cap, this is always safe to flip.
- **4-word cap.** Beyond 4 words, the utterance is not a confirmation — it's dialogue. Return `unclear` and trigger reprompt.
- **Lone `не`.** Standalone single-word `"не"` (a truncated `"ні"`) stays NO for robustness.

**Separately — adjacency helper for the confirmation caller.** `parse_yes_no` itself still returns `"yes"`/`"no"`/`"unclear"`. Adjacency of the keyword to the yes/no head is enforced by the **caller** in `_handle_confirmation` via a new module-level helper:

```python
def _keyword_is_adjacent_prefix(
    transcript: str, keyword_re: re.Pattern, n: int = 3
) -> bool:
    """True if the command keyword appears within the first n tokens
    of the raw transcript. Used by _handle_confirmation to prevent a
    stranger's ambient 'гава' buried later in a sentence from being
    counted as a confirmation keyword next to a yes/no head."""
    words = transcript.strip().lower().split()[:n]
    if not words:
        return False
    return bool(keyword_re.search(" ".join(words)))
```

This keeps `parse_yes_no` pure (yes/no classification only) while letting `_handle_confirmation` independently require that the keyword sits at the head of the utterance — not 8 tokens deep.

**Acceptance:**
- All existing `tests/test_yes_no.py` parametrized cases continue to pass.
- `parse_yes_no("не так")` → `"no"` (was wrongly `"yes"` before).
- `parse_yes_no("так не роби")` → `"no"` (was wrongly `"yes"` before).
- `parse_yes_no("гава так")` → `"yes"` (NEW: required for TODO 4).
- `parse_yes_no("гава, так")` → `"yes"` (NEW: comma-tolerant vocative).
- `parse_yes_no("гава ні")` → `"no"` (NEW).
- `parse_yes_no("гава, не треба")` → `"no"` (NEW).
- `parse_yes_no("давай не зараз")` → `"no"` (NEW: negation inversion).
- `parse_yes_no("розкажи детальніше як запустити тести")` → `"unclear"` (6 words → cap).
- `parse_yes_no("я не знаю")` → `"unclear"` (head is `"я"`, neither yes nor no).
- `parse_yes_no("можливо")` → `"unclear"`.
- `parse_yes_no("так, але не зараз")` → `"no"` — DECISION: safer to treat yes+negation as no than as unclear. 4 words exactly, matches YES head + tail negation via `але не зараз`. NOTE: my current _NEGATION_TAIL regex does NOT catch `але не зараз` because `але` breaks the `\bне\s+зараз\b` pattern. Parser returns `"yes"` for this case. Two options: (a) accept it (4-word ambiguous input is not expected in real use), (b) add `r"\bале\s+не\b"` as an additional override. Picking (a) — if real-world usage shows this case, add (b). Flagged in Open Questions.
- Similarly `parse_yes_no("так, не заважай мені")` → current parser returns `"no"` via `_YES_THEN_NE.match("так не заважай")`. This is semantically correct.
- `_keyword_is_adjacent_prefix("гава так", re, n=3)` → `True`.
- `_keyword_is_adjacent_prefix("так і ще гава", re, n=3)` → `False` (keyword at token index 3 → outside first 3).
- `_keyword_is_adjacent_prefix("гей так", re, n=3)` → `True`.
- `_keyword_is_adjacent_prefix("", re, n=3)` → `False`.
- Helper lives in `src/decider.py` (module-level) alongside `parse_yes_no`; imported/used by `DeciderProcessor._handle_confirmation` in TODO 4 — NOT inside `parse_yes_no`.

---

### TODO 2 — Add command keyword config + pattern (src/config.py, src/decider.py:73)

**Change:**
- `src/config.py` Settings:
  ```python
  speaker_command_keyword_required: bool = True
  command_keyword_pattern: str = r"\b(гава|heare|гей)\b"
  ```
  Note: default pattern = existing `WAKE_WORD_PATTERN` so the keyword gate = wake word gate by default. Users can override via `config.toml`.
- `src/decider.py`:
  - Keep `WAKE_WORD_PATTERN` constant but add a new `_compile_command_keyword(settings) -> re.Pattern` helper that returns a compiled pattern from `settings.command_keyword_pattern`. Called once in `DeciderProcessor.__init__`.
  - Rename local usage to `self._command_keyword_re`.

**Acceptance:**
- Settings defaults render `speaker_command_keyword_required=True` and the regex matches `"гава"`, `"heare"`, `"гей"` case-insensitive on word boundaries.
- Flag-off backwards compat: when `speaker_command_keyword_required=False`, the decider never checks the keyword (old behavior for non-speaker-id deployments).

---

### TODO 3 — Rewrite `_handle_listening` to drop hard gate, add keyword check (src/decider.py:514-631)

**Change:**

Remove the `speaker_id != "owner"` drop at line 524-534. Replace with this logic:

```python
async def _handle_listening(self, transcript, speaker_id=None, speaker_confidence=None) -> None:
    # Store every transcript unconditionally (audit trail unchanged).
    # ... existing noise/quick_nothing filters stay ...
    transcript_id = await self.store.log_transcript(...)
    prompt = await self._prompt_for_transcript(transcript)
    decision = await self.claude_cli.call_decider(prompt)
    # ... existing decision logging ...

    d_type = decision.get("type", "nothing")
    if d_type == "nothing":
        return
    if d_type == "speak":
        # speak is INTENTIONALLY NOT keyword-gated.
        # Rationale: user's design direction is "speaker as tag" — strangers
        # can still receive a conversational TTS reply; only `act` requires
        # the keyword. This is a deliberate tradeoff vs. the current
        # `:524` hard drop: non-owners now reach Claude for speak-type
        # interactions, which increases LLM cost and surfaces ambient
        # conversation to the model. Accepted as a follow-up for a
        # stranger-speak rate-limit (see ADR follow-ups).
        reply = decision.get("reply")
        if reply:
            await self.push_frame(TTSSpeakFrame(reply))
        return
    if d_type == "act":
        # NEW: keyword gate for actions
        if self.settings.speaker_command_keyword_required:
            if not self._command_keyword_re.search(transcript):
                logger.info(
                    "[DECIDER] act decision dropped — no command keyword: %r",
                    transcript[:60],
                )
                self._safe_emit(
                    EventKind.DECIDER_DROPPED_NO_KEYWORD,
                    transcript_id=transcript_id,
                    decision_id=decision_id,
                    payload={"speaker_id": speaker_id},
                )
                return
        # ... existing confidence floor check + ACTION_ARMED + pending_* assignment ...
```

Speaker_id is now **pure metadata**: stored on the transcript, passed to Claude via the prompt tag (TODO 5), but not a drop filter. `pending_speaker_id` still gets assigned so the confirmation step can compare, but as a hint not a hard requirement.

**Acceptance:**
- Stranger says `"Гава, видали файл"` → reaches Claude, Claude may return `act` → keyword present → arms confirmation. (Behavior change: strangers with keyword can now arm.)
- Stranger says `"видали файл"` → may reach Claude (no more hard drop), Claude may return `act` → no keyword → dropped at keyword gate. Logged as `decider_dropped_no_keyword`.
- Stranger says `"привіт, як справи?"` → reaches Claude → Claude returns `speak` → TTS reply delivered. **Intentional:** `speak` is not keyword-gated; "speaker as tag" lets strangers get conversational responses. The speaker tag in the prompt (TODO 5) gives Claude the context to decide whether to engage.
- Owner says `"Гава, запусти тести"` → reaches Claude → keyword present → arms confirmation (unchanged from today).
- Owner's drifted voice (speaker_id=None) says `"Гава, запусти тести"` → reaches Claude → keyword present → arms confirmation. (Drift fixed.)
- Owner says `"запусти тести"` without keyword → reaches Claude → if Claude returns `act`, dropped at keyword gate; if Claude returns `speak`, replies normally.
- New EventKind `DECIDER_DROPPED_NO_KEYWORD` added to `storage.py`.

**Known accepted tradeoff (non-blocking):** LLM-cost and privacy for non-owner `speak` calls is a deliberate regression vs. the current `:524` hard drop. User's intent ("leave detection as a tag") trumps the cost concern. Tracked as a follow-up in the ADR for a stranger-`speak` rate-limit (e.g. N `speak` replies per stranger-label per hour, rolling window).

---

### TODO 4 — Rewrite `_handle_confirmation` with fail-closed AND-logic (src/decider.py:682-733)

**Change:**

**CRITICAL (Critic fix — iteration 2):** The previous revision used keyword-OR-speaker, which collapses the fail-closed spoof guard at `decider.py:697-703`. The existing comment literally says "inherited labels are NEVER trusted." An OR gate would let a stranger's inherited-label `"так"` slip through on keyword alone, or a stranger's bare `"гава так"` slip through when speaker-id is working.

The correct policy is **AND-logic whenever speaker-id is enabled**:

- `speaker_id_enabled=True`: require **BOTH** `has_keyword` **AND** `is_same_speaker` (speaker match, not inherited). Keyword alone is NOT enough when the speaker pipeline is running — if drift dropped the embedding, the user must re-enroll (or the operator must disable speaker-id entirely).
- `speaker_id_enabled=False`: fall back to keyword-only (no speaker pipeline exists to consult). This is the pure-keyword deployment mode.

```python
async def _handle_confirmation(self, transcript, speaker_id=None, speaker_inherited=False) -> None:
    await self.store.log_transcript(...)

    # Parse first to know if the utterance is even a yes/no.
    verdict = parse_yes_no(transcript)
    if verdict == "unclear":
        # ... existing reprompt ...
        return

    # --- Authorization (fail-closed AND-logic) ---
    #
    # Inherited short-turn labels NEVER count as "same speaker" — preserves
    # the fail-closed spoof guard that the current :697-703 comment warns
    # about. The new rule on top: when speaker-id is on, keyword AND speaker
    # must both agree. Drift does NOT auto-confirm via keyword — the user
    # must re-enroll.
    has_keyword = _keyword_is_adjacent_prefix(
        transcript, self._command_keyword_re, n=3
    )
    is_same_speaker = (
        self.settings.speaker_id_enabled
        and speaker_id is not None
        and speaker_id == self.pending_speaker_id
        and not speaker_inherited
    )

    if self.settings.speaker_command_keyword_required:
        if self.settings.speaker_id_enabled:
            # AND-logic: both must agree. Drift (speaker_id=None) fails here
            # even with keyword — user must re-enroll.
            authorized = has_keyword and is_same_speaker
        else:
            # Speaker pipeline is off: keyword-only is the only guard.
            authorized = has_keyword

        if not authorized:
            logger.warning(
                "confirmation rejected — authorization failed: sid=%s pending=%s inherited=%s has_kw=%s same_spk=%s spk_on=%s",
                speaker_id, self.pending_speaker_id, speaker_inherited,
                has_keyword, is_same_speaker, self.settings.speaker_id_enabled,
            )
            await self.push_frame(TTSSpeakFrame("Скажи: гава так"))
            return

    # ... existing verdict handling (no/yes) ...
```

Note: `FIXED_PHRASES` adds `"Скажи: гава так"` to the TTS pre-cache. This is a narrower reprompt than the previous OR-fallback wording — it explicitly demands the keyword-prefixed YES form and does NOT suggest "or keyword alone" as an alternative.

**Drift reprompt semantics.** When `speaker_id_enabled=True` and the owner's voice has drifted (speaker_id=None), the AND-gate will reject every `"гава так"` attempt until the user re-enrolls (TTS loop: user says `"гава так"` → rejected → `"Скажи: гава так"` → user says `"гава так"` → rejected → ...). This is the **correct** fail-closed behavior: the pending 8s window expires and returns to LISTENING; the user runs the re-enrollment flow; subsequent commands work. The plan's Open Questions logs this as a follow-up to surface a dedicated "drift detected — re-enroll?" prompt instead of looping the generic reprompt.

**Acceptance:**
- **Owner fast-path (speaker on, ECAPA working):** Owner says `"Гава, запусти тести"` → armed. Owner says `"гава так"` → `has_keyword=True` (first 3 tokens: `гава так`), `is_same_speaker=True` (speaker_id=owner, not inherited) → AND holds → executes.
- **Owner bare-yes rejection (NEW — tightened):** Owner says `"так"` (no keyword) → `has_keyword=False` → AND fails → reprompted. This is a deliberate tightening from the previous OR revision: even the real owner must prefix with the keyword. Rationale: prevents ambient `"так"` in conversation from confirming. Documented in ADR as a UX tradeoff.
- **Owner drift rejection (NEW — fail-closed):** Owner's drifted voice arms with keyword (pending_speaker_id=None stored), then says `"гава так"` → `has_keyword=True` but `is_same_speaker=False` (speaker_id=None) → AND fails → rejected, reprompt. Drift does NOT auto-confirm. User must re-enroll; this is the explicit tradeoff for preserving the stranger-spoof guard. `test_owner_drift_confirmation_still_needs_reenroll` covers this.
- **Stranger keyword spoof (NEW — blocked):** Stranger interrupts armed state with `"гава так"` → `has_keyword=True` but `is_same_speaker=False` (speaker_id != pending) → AND fails → rejected. `test_stranger_keyword_confirmation_rejected` covers this. This is the critical regression the Critic caught in iteration 1.
- **Stranger bare yes:** Stranger interrupts with `"так"` → `has_keyword=False`, `is_same_speaker=False` → AND fails → rejected. `test_stranger_interrupts_confirmation_never_executes` still passes (updated reprompt wording).
- **Inherited short-turn spoof:** Short-turn stranger riding owner's inherited label says `"так"` → `speaker_inherited=True` → `is_same_speaker=False` → `has_keyword=False` → AND fails → rejected. `test_short_turn_in_confirmation_never_executes` still passes.
- **Inherited short-turn with keyword:** Short-turn stranger riding owner's inherited label says `"гава так"` → `speaker_inherited=True` → `is_same_speaker=False` (inherited is explicitly disqualified) → AND fails → rejected. This is the spoof the Critic was worried about — AND-logic blocks it because inherited labels are not trusted.
- **Pure keyword-mode (speaker_id_enabled=False):** Owner says `"гава так"` → `has_keyword=True`, speaker pipeline is off → `authorized = has_keyword` → executes. In this mode, a stranger shouting `"гава так"` during the 8s window WILL confirm — documented as a known accepted risk for pure-keyword deployments.
- **Adjacency check blocks buried keyword:** `"так, і передай гаві привіт"` (7 words, 4+ tokens before `гаві`) → `parse_yes_no` returns `"unclear"` (>4 words) → early-return before authorization check. Separately, `_keyword_is_adjacent_prefix("так і гава", re, 3)` → `True` (keyword at token 3) but `parse_yes_no("так і гава")` parses the head as YES with 3 words → would reach authorization. This is acceptable because the keyword still sits in the first 3 tokens; the user DID say the keyword near the yes/no head.
- **Long-tail rejection:** Owner says `"гава так, але не зараз"` → `parse_yes_no` returns `"unclear"` (>4 words) → reprompt, regardless of keyword/speaker state.

---

### TODO 5 — Speaker tag injection into prompt (src/context.py:49-52, prompts/decider.txt)

**Change:**
- `src/context.py` `_render_rule_block`:
  ```python
  def _render_rule_block(self, speaker_id: str | None = None) -> str:
      if not self.settings.speaker_id_enabled:
          return ""
      if speaker_id == "owner":
          return "Speaker: owner (high confidence)"
      if speaker_id is None:
          return "Speaker: unknown (could be owner with voice drift, a guest, or background audio)"
      return f"Speaker: {speaker_id} (not owner)"
  ```
- `build()` signature grows a `speaker_id: str | None = None` parameter; passed through from `_handle_listening` and the speculative path.
- Speculative path (`src/decider.py:279-302`): speaker_id is not known during speculation (frame hasn't arrived yet). Keep `{speaker_rule_block}` as a keep-placeholder. At substitution time in `_prompt_for_transcript` (lines 399-426), call `self.context_builder._render_rule_block(speaker_id)` with the real speaker_id from the frame and substitute.
- `_prompt_for_transcript` gains a `speaker_id` parameter threaded from `_handle_listening`.
- `prompts/decider.txt`: no textual change — `{speaker_rule_block}` already exists at line 11. New rule added to RULES section:
  ```
  - If Speaker: unknown or not owner, prefer "nothing" or "speak". Only return "act" if the transcript contains an explicit command addressed to you.
  ```

**Acceptance:**
- `test_context.py` golden-string test for flag-off still passes byte-identically.
- New test `test_rule_block_unknown_speaker` asserts the Ukrainian-aware rule block for each of {"owner", None, "guest"}.
- Claude gets visibility into speaker context but final auth stays in Python (keyword gate).

---

### TODO 6 — Pipeline flag decoupling (src/pipeline.py:96)

**Change:**

`speaker_id_enabled` still controls whether the speaker-tagging pipeline stages are built. The new `speaker_command_keyword_required` is independent: it can be `True` even when `speaker_id_enabled=False` (pure keyword-gate deployment, no ECAPA needed).

Update the `speaker_id_enabled but no owner enrolled` branch at `src/pipeline.py:102-108` to log a softer warning — it no longer disables the feature, just disables speaker tagging. Commands still work via keyword gate.

**Acceptance:**
- `speaker_id_enabled=False, speaker_command_keyword_required=True` — pipeline has no tagger stages, decider's keyword gate still fires. Strangers without keyword cannot act; owner without keyword cannot act; owner with keyword acts.
- `speaker_id_enabled=True, speaker_command_keyword_required=True` — current production config. Speaker tag reaches Claude, keyword gates actions.
- `speaker_id_enabled=False, speaker_command_keyword_required=False` — legacy mode, identical to pre-speaker-recognition behavior.

---

### TODO 7 — Tests (tests/test_stranger_integration.py, tests/test_decider.py, tests/test_yes_no.py)

**Changes:**

1. **`tests/test_yes_no.py`** — add cases:
   - `"не так"` → `"no"`
   - `"так не роби"` → `"no"`
   - `"не треба"` → `"no"` (already exists)
   - `"так, але не зараз"` → `"unclear"` (new: >4 words)
   - `"так, не заважай мені"` → `"unclear"` (new: >4 words)
   - `"розкажи детальніше"` → `"unclear"` (stays)

2. **`tests/test_stranger_integration.py`** — threat-model preservation:
   - `test_stranger_in_listening_never_reaches_decider` → rename to `test_stranger_without_keyword_never_executes`. Change assertion: stranger says `"видали файл"` (no keyword) → decider calls Claude but `d_type=act` is dropped at keyword gate. Alternative variant: Claude returns `nothing` (no need to mock a specific decision). Stranger says `"Гава, видали файл"` → arms, but subsequent confirmation without keyword is rejected → never executes.
   - `test_stranger_interrupts_confirmation_never_executes` → stays, assertion updated to check new reprompt `"Скажи: гава так, або гава ні"`.
   - `test_short_turn_in_confirmation_never_executes` → stays, same update.

3. **`tests/test_decider.py`** — 8 tests touching `speaker_id_enabled` at lines 726-855:
   - Tests that arm an action with owner speaker now ALSO need to include the keyword in the transcript (already do — `"Гава, запусти тести"` has `гава`). Most should pass without change.
   - Tests that rely on the hard `speaker_id != "owner"` drop (lines 730, 748, 770, 783, 796, 807, 820) need to either:
     a) delete the test (behavior removed), or
     b) rename and re-express as keyword-gate tests, or
     c) keep as regression checks for the new gate semantics.
   - New test `test_owner_voice_drift_with_keyword_still_commands`: `speaker_id=None`, transcript `"Гава, запусти тести"`, confidence 0.6. Assert `_handle_listening` → Claude is called → if Claude returns `act` → state=AWAITING_CONFIRMATION → pending_speaker_id=None.
   - New test `test_owner_drift_confirmation_with_keyword`: armed state with `pending_speaker_id=None`, confirmation `"гава так"` → `speaker_id=None, has_keyword=True` → executes.
   - New test `test_act_without_keyword_dropped`: armed state path — `_handle_listening` with transcript `"запусти тести"`, Claude returns `act` → dropped at keyword gate, state stays LISTENING.

4. **`tests/test_context.py`** — extend rule-block tests at lines 103-219 to cover the new speaker_id parameter on `_render_rule_block`.

**Acceptance:**
- `uv run pytest tests/ -q` — all tests pass.
- Specifically: 292+ existing tests (Phase 1 floor) plus ~6 new tests for drift + yes/no + keyword gate.
- Golden string test for flag-off still green.

---

## Success Criteria

1. **Owner LISTENING with drifted voice + keyword = reaches Claude and arms.** Verified by `test_owner_voice_drift_with_keyword_still_commands` (drift is fixed at the listening gate — drift no longer silently blackholes speech).
2. **Owner CONFIRMATION with drifted voice = fail-closed, must re-enroll.** Verified by `test_owner_drift_confirmation_still_needs_reenroll`: armed with `pending_speaker_id=None`, drifted owner says `"гава так"`, AND-logic rejects because `is_same_speaker=False`. User is directed to re-enroll. This is the deliberate tradeoff between drift-resilience (listening) and fail-closed spoof guard (confirmation).
3. **Stranger CONFIRMATION spoof with keyword is rejected.** Verified by `test_stranger_keyword_confirmation_rejected`: armed state with owner's pending_speaker_id, stranger says `"гава так"`, `has_keyword=True` but `is_same_speaker=False`, AND-logic rejects. This is the core regression the Critic caught in iteration 1 — now fixed by AND-logic.
4. **Stranger without keyword = no action.** Verified by updated stranger integration tests.
5. **Stranger with keyword can arm** (behavior change from today — LISTENING no longer hard-drops) but **cannot confirm** because AND-logic blocks them at the confirmation step. Verified by updated stranger integration tests.
6. **Owner bare `"так"` without keyword is rejected** (UX regression vs. today, deliberate — prevents ambient confirmation). Verified by new confirmation test.
7. `parse_yes_no("не так")` returns `"no"`. Verified by new yes/no test.
8. `parse_yes_no("так, але не зараз")` returns `"unclear"`. Verified by new yes/no test.
9. `_keyword_is_adjacent_prefix` helper exists and is called from `_handle_confirmation` (not from `parse_yes_no`). Verified by new unit test + code inspection.
10. Stranger `speak` traffic reaches Claude and receives a TTS reply (intentional — documented tradeoff). Verified by new test `test_stranger_speak_still_replies`.
11. Claude sees `[speaker:unknown]` / `[speaker:owner]` tag in prompt. Verified by updated context tests.
12. `speaker_id_enabled=False` still renders `decider.txt` byte-identically. Verified by existing golden test.
13. `speaker_id_enabled=False` + `speaker_command_keyword_required=True` mode: keyword-alone confirmation works (documented pure-keyword deployment path). Verified by new test `test_pure_keyword_mode_confirmation`.
14. `uv run pytest tests/ -q` passes with zero failures.

---

## ADR — Architecture Decision Record

**Decision:** Adopt Option A (keyword-gated commands + speaker-as-tag + AND-gated confirmations). Drop the hard `speaker_id != "owner"` gate in `_handle_listening` for `act` decisions; replace it with a keyword requirement. Keep `speak` decisions un-gated (speaker-as-tag intent). In `_handle_confirmation`, require **both** keyword AND speaker match when `speaker_id_enabled=True` (fail-closed); fall back to keyword-only when speaker-id is off. Inject speaker identity into the decider prompt as a soft tag. (Iteration 2: AND-logic replaces an earlier OR-logic draft that the Critic correctly identified as a regression of the fail-closed spoof guard.)

**Drivers:**
1. Drift resilience — embedding threshold failures should not silently break the user's command path.
2. Stranger threat model preservation — keyword gate is a learnable secret; unknown voices in ambient audio cannot accidentally execute actions.
3. Minimal blast radius — reuses existing `WAKE_WORD_PATTERN` infra, keeps FSM states, preserves speculative-prompt path, keeps speaker-id pipeline intact.

**Alternatives considered:**
- **Option B (tiered confidence + identity challenge).** Too complex. New FSM state, new TTS prompts, doesn't match user intent ("leave detection, but only as a tag").
- **Option C (pure keyword gate, drop speaker-id).** Throws away working ECAPA infra. User explicitly wants speaker-id retained as a tag. Also loses per-turn speaker signal for Claude to reason about.

**Why chosen:** Option A matches user intent verbatim. It solves drift (Driver 1), preserves the threat model via a different mechanism (Driver 2), and costs ~200 lines of diff + 6 new tests (Driver 3). It's the smallest change that satisfies all three drivers.

**Consequences:**
- **Positive:** drift no longer silently breaks the LISTENING gate — owner's drifted voice + keyword reaches Claude and arms. Fail-closed spoof guard at the CONFIRMATION gate is preserved and strengthened by AND-logic: strangers cannot confirm even if they know the keyword (when speaker-id is on). Claude gains richer context. Audit trail gains `decider_dropped_no_keyword` event kind.
- **Negative:**
  - Owner must say `гава` + yes/no on **every** confirmation (no bare `"так"` fast-path). UX regression, deliberate.
  - Drift during an **active confirmation** is fail-closed: the owner cannot recover via keyword and must re-enroll. Tradeoff for preserving the spoof guard.
  - Strangers now reach Claude for `speak` decisions (LLM-cost + privacy regression vs. the current `:524` hard drop). Documented as an accepted tradeoff for the "speaker as tag" intent.
- **Risk:**
  - `speaker_id_enabled=False` (pure-keyword) deployments: a stranger who knows the keyword can confirm. Mitigation: config.toml keyword rotation; operator picks a non-obvious word. Known accepted risk for pure-keyword mode.
  - `speaker_id_enabled=True` (production): AND-logic blocks the stranger-keyword spoof. Residual risk: a stranger whose ECAPA embedding happens to collide with the owner above threshold during an armed window. Mitigation: `speaker_id_threshold_match=0.75` floor; pre-existing speaker_id audit.

**Follow-ups (not in scope):**
- Add a `HEARE_COMMAND_KEYWORD` env var override for quick rotation.
- Stranger `speak` rate-limit: N replies per stranger-label per hour (rolling window) to cap LLM cost and discourage ambient chat with Heare.
- "Drift detected — re-enroll?" dedicated TTS branch when speaker_confidence sits between `threshold_match` and a new `threshold_suspect` band — surfaces the re-enroll prompt instead of looping the generic `"Скажи: гава так"`.
- Revisit `\b...\b` regex boundaries for Cyrillic — Python's `\b` is locale-aware on UTF-8 but some edge cases (apostrophes) may misbehave.
- Expose an audit dashboard for `DECIDER_DROPPED_NO_KEYWORD` events.
- `--strict-speaker` deploy flag that refuses to start if speaker-id is disabled (prevents accidental downgrade to pure-keyword in production).
- Consider a bare-yes allowlist: if the last N successful confirmations all had matching speaker_id, permit a short "speaker-only" grace window for the immediate next yes/no. Deferred unless UX regression bites.

---

## Parser Validation Appendix

The `parse_yes_no` rewrite in TODO 1 was validated during planning against a 30-case table covering: the existing `tests/test_yes_no.py` corpus, the known `\bне\b` false-positive cases (`"не так"`, `"так не роби"`), the new vocative-prefix cases needed for TODO 4 (`"гава так"`, `"гава, не треба"`), and long-utterance rejection (`"розкажи детальніше як запустити тести"` → `unclear`). All 30 cases pass under the spec above. One edge case (`"так, але не зараз"` — 4 words, YES head, mid-negation with `але` buffer) remains a documented follow-up: current parser returns `yes`; if real-world usage surfaces this as a problem, add `r"\bале\s+не\b"` to `_NEGATION_TAIL` alternation. Flagged in Open Questions.

---

## Plan Summary

**Scope:**
- 6 files touched: `src/decider.py`, `src/config.py`, `src/context.py`, `src/pipeline.py`, `prompts/decider.txt`, `tests/` (4 files)
- ~7 TODOs
- Estimated complexity: **MEDIUM**

**Key Deliverables:**
1. Yes/no parser hardened (`\bне\b` false-positive fixed, standalone-utterance constraint).
2. Command keyword gate for `act` decisions (drops non-keyword actions pre-confirmation).
3. Keyword-AND-speaker confirmation authorization (fail-closed spoof guard; iteration 2 Critic fix — OR-logic rejected).
4. Speaker-id as prompt tag in context (soft signal to Claude, not hard gate).
5. Pipeline flag decoupling (`speaker_id_enabled` vs `speaker_command_keyword_required`).
6. Test coverage for drift case + yes/no false positives + updated stranger threat model.

**Does this plan capture your intent?**
- `proceed` — Begin implementation via `/oh-my-claudecode:start-work owner-detection-rethink`
- `adjust [X]` — Return to interview to modify X
- `restart` — Discard and start fresh
