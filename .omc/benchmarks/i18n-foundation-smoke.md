# PRD A — i18n Foundation Live Smoke Test Recipe

**Date written:** 2026-04-25
**Branch:** s2s-realtime
**Daemon-side ACs verified by:** US-I18N-09 regression (898 tests pass, ruff clean)

---

## Prerequisites

- Daemon running: `make run` or `uv run python -m src.main`
- Default config (`~/.heare/config.toml`) has `groq_language = "en"` and
  `tts_voice = "en-US-AriaNeural"` (PRD A defaults).
- Microphone + speaker active.
- Tail the log in a separate terminal:
  ```
  tail -f ~/.heare/daemon.log | grep -E '\[TIMING\]|\[TTS VOICE SWAP\]|\[LANG_MISMATCH\]'
  ```

---

## Turn 1 — English baseline

**Say:** "hello, what time is it?"

**Expected behavior:**
- Bot replies in English with the current time.
- TTS voice is `en-US-AriaNeural` (no swap, already default).

**Log check (`daemon.log`):**
```
[TIMING] generator transcript="hello, what time is it?" lang=en ttft=...ms ...
```
- `lang=en` present in TIMING line.
- No `[TTS VOICE SWAP]` line (voice unchanged from default).
- No `[LANG_MISMATCH]` warning.

**Record TTFT:** _______ ms

---

## Turn 2a — Ukrainian (first detection, hysteresis pending)

**Say:** any Ukrainian phrase with a pending intent queued first.
To create a pending intent, say "run echo test" first (English), then immediately say "скасуй".

Alternatively: say "відкрий термінал" to queue an intent, then say "скасуй".

**Expected for this first Ukrainian turn:**
- Hysteresis active — active language stays English after ONE Ukrainian detection.
- No `[TTS VOICE SWAP]` yet.

**Log check:**
```
[TIMING] generator transcript="скасуй" lang=en ttft=...ms ...
```
- `lang=en` (still English — hysteresis not yet satisfied).
- No `[TTS VOICE SWAP]` line.

---

## Turn 2b — Ukrainian (second detection, hysteresis satisfied)

**Say:** another Ukrainian phrase, e.g. "що зараз відбувається?"

**Expected behavior:**
- After 2 consecutive Ukrainian detections, active language switches to Ukrainian.
- `[TTS VOICE SWAP]` logged with `from=en-US-AriaNeural to=uk-UA-OstapNeural lang=uk`.
- Bot replies in Ukrainian.
- TTS voice is `uk-UA-OstapNeural`.

**Log check:**
```
[TTS VOICE SWAP] from=en-US-AriaNeural to=uk-UA-OstapNeural lang=uk
[TIMING] generator transcript="що зараз відбувається?" lang=uk ttft=...ms ...
```
- `[TTS VOICE SWAP]` with `from=` and `to=` fields present.
- `lang=uk` in TIMING line.
- No `[LANG_MISMATCH]` (Gemini should reply in Ukrainian).

**Record TTFT:** _______ ms

---

## Turn 3a — Russian (first detection, hysteresis pending)

**Say:** "запусти echo привет"

**Expected:**
- First Russian detection — hysteresis pending, active language stays Ukrainian.
- No `[TTS VOICE SWAP]` yet.
- `lang=uk` in TIMING line.

---

## Turn 3b — Russian (second detection, hysteresis satisfied)

**Say:** "как дела?" or "что нового?"

**Expected behavior:**
- Active language switches to Russian after 2 consecutive Russian detections.
- `[TTS VOICE SWAP]` logged with `from=uk-UA-OstapNeural to=ru-RU-DmitryNeural lang=ru`.
- Bot replies in Russian with intent tag (for turn 3a's command if still pending).
- TTS voice is `ru-RU-DmitryNeural`.

**Log check:**
```
[TTS VOICE SWAP] from=uk-UA-OstapNeural to=ru-RU-DmitryNeural lang=ru
[TIMING] generator transcript="как дела?" lang=ru ttft=...ms ...
```
- `lang=ru` in TIMING line.
- No `[LANG_MISMATCH]`.

**Record TTFT:** _______ ms

---

## TTFT latency measurement — `include_prob_metrics` impact

To measure the delta from switching `verbose_json` on/off:

1. **With `include_prob_metrics=True`** (PRD A default, always on in code):
   Run 3 English turns and record average TTFT from TIMING lines.
   Average TTFT: _______ ms

2. **Baseline comparison** (not configurable without code change; use prior
   `phase2.2-live-smoke.md` as reference):
   Prior baseline TTFT (from phase2.2): _______ ms

3. **Delta:** _______ ms
   - Acceptable if delta < 200ms (well within 2s TTFT budget).
   - If delta >= 200ms: investigate whether the cause is response-size
     parsing or network transfer (compare `ttfb_mp3` vs `ttft` in
     `[TIMING] tts` lines).

---

## Pass criteria

| Check | Pass condition | Result |
|-------|---------------|--------|
| Turn 1: `lang=en` in TIMING | Present | |
| Turn 1: no `[TTS VOICE SWAP]` | Absent | |
| Turn 2b: `[TTS VOICE SWAP] from=en-US-AriaNeural to=uk-UA-OstapNeural` | Present | |
| Turn 2b: `lang=uk` in TIMING | Present | |
| Turn 3b: `[TTS VOICE SWAP] from=uk-UA-OstapNeural to=ru-RU-DmitryNeural` | Present | |
| Turn 3b: `lang=ru` in TIMING | Present | |
| No `[TTS VOICE SWAP]` on same-language repeat | Absent | |
| No `[LANG_MISMATCH]` warnings | Absent (or documented if present) | |
| TTFT delta from `verbose_json` < 200ms | <= 200ms | |
| Bot reply language matches spoken language | Matches for en/uk/ru | |

---

## Known acceptable findings

- `[LANG_MISMATCH]` warnings do NOT block pass — this is observability only.
  If warnings appear, note them below and open a follow-up for PRD B/C.
- Hysteresis delay (~2 turns = 4-8 seconds) before voice swap is expected
  behavior, not a regression.
- Confirmation phrases (`"Скажи: гава так, або гава ні"`) remain Ukrainian-only
  until PRD C. This is intentional.

---

## Findings (fill in during live run)

- Date/time of run: _______________
- Turn 1 TTFT: _______ ms
- Turn 2b TTFT: _______ ms
- Turn 3b TTFT: _______ ms
- `verbose_json` delta: _______ ms
- `[LANG_MISMATCH]` warnings observed: yes / no
  - If yes, detail: _______________
- Any unexpected behavior: _______________
- Overall result: PASS / FAIL
