## Revision 2 -- addressing Architect+Critic feedback

1. **B1 fixed**: `language=None` crash eliminated. `groq_language` default changed from `"auto"` to `"en"` (Language.EN hint). Whisper auto-detects regardless; the hint biases prior only. `pipeline.py` always passes a valid `Language` enum.
2. **B2 fixed**: `include_prob_metrics=True` now required in US-I18N-02 AC so `verbose_json` is used and `response.language` is populated.
3. **B3 fixed**: `WHISPER_NAME_TO_ISO` normalization map added to `src/language.py`. `detect_language_from_frame` lowercases + maps full names to ISO codes. Unit tests added.
4. **B4 applied**: 2-turn hysteresis added as US-I18N-03b (folded into US-I18N-03). Generator tracks `_last_detected_lang` and `_pending_lang_count`; language only switches after 2 consecutive non-current detections. 2 unit tests specified.
5. **C1 fixed**: Gemini language-compliance logging added to US-I18N-03. Cyrillic-ratio heuristic chosen (no new dependency). `[LANG_MISMATCH]` WARNING logged on mismatch. Risk Register row 2 updated.
6. **C2 fixed**: `lang=<detected>` field added to `[TIMING]` log line AC in US-I18N-03.
7. **C3 fixed**: `[TTS VOICE SWAP]` log line AC added to US-I18N-05.
8. **M1 resolved**: Confirmation phrases kept in `FIXED_PHRASES` with `# DO NOT REMOVE` comment. Removal deferred to PRD C.
9. **M2 applied**: Story dependency order added before user stories.
10. **M3 resolved**: Voice IDs pinned: `uk-UA-OstapNeural`, `ru-RU-DmitryNeural`, `en-US-AriaNeural`. Open question removed.
11. **M4 applied**: "Known Tradeoff Tensions" section added under RALPLAN-DR Summary.

---

# RALPLAN -- heare PRD A: i18n Foundation (Multilingual Voice Loop)

Convert the prompt baseline to English, detect the user's language per turn
from STT metadata, instruct the generator to respond in the same language,
swap the TTS voice to match, and replace the Ukrainian-only cancel keyword
gate with a per-language pattern table. After this PRD, a user can speak
Ukrainian, English, or Russian and get a same-language voice reply.

> **Sequencing:** This is PRD A of three. PRD B (voice-friendly action result
> contract, `{output, spoken}` per direct tool) and PRD C (per-language
> persona variants, locale polish) are explicitly out of scope.

---

## RALPLAN-DR Summary

### Principles

1. **No new hot-path latency.** Language detection must come from data
   already in the pipeline (Whisper STT metadata on `TranscriptionFrame`),
   not from an extra LLM call or network round-trip.
2. **Single source of truth for language detection.** The detected language
   flows from the STT frame through the pipeline as a single `lang` string
   (`"en"`, `"uk"`, `"ru"`). No component re-derives it independently.
3. **Graceful degradation to English on unknown language.** If the detected
   language is not in the supported set `{en, uk, ru}`, treat it as English
   for TTS voice selection and generator instruction.
4. **English baseline, multilingual output.** Prompts are authored in
   English (the lingua franca of LLM training data). The generator is
   instructed to *respond* in the detected language. This maximizes prompt
   comprehension across all models while preserving native-language replies.
5. **Minimal blast radius.** Touch only the files required for the
   language loop. Do not modify tool registry, direct tools, action worker,
   or speaker recognition code.

### Decision Drivers (top 3)

1. **Why English baseline prompts?** Gemini Flash (the production model)
   has stronger instruction-following on English prompts than Ukrainian ones.
   Ukrainian-language *output* quality is preserved by the explicit
   instruction "respond in the user's language." This also unblocks
   English- and Russian-speaking users without prompt duplication.
2. **Why per-turn detection (not sticky session language)?** Users in this
   household switch languages mid-conversation (Ukrainian/Russian code-
   switching is common). A sticky session language would require an explicit
   "switch language" command, adding friction. Per-turn detection from
   Whisper metadata is zero-cost (already computed by the STT service).
3. **Why static voice map (not dynamic discovery)?** Edge-tts voice names
   are stable across versions and the supported language set is fixed at 3.
   Dynamic discovery adds an async startup call, error handling, and
   caching for zero practical benefit. A 3-entry dict is correct.

### Viable Options

#### A. Language detection source

**Option A1: Whisper `detected_language` from STT response metadata** -- chosen

Pipecat's `BaseWhisperSTTService.run_stt()` (base_stt.py:274) stores the raw
Whisper API response as `result` on the `TranscriptionFrame`. When
`include_prob_metrics=True` is passed to the STT service, the response uses
`response_format="verbose_json"` (groq/stt.py:129), which includes a
`language` field. **Important:** Whisper returns full language names
(`"english"`, `"ukrainian"`, `"russian"`), not ISO-639-1 codes. A
normalization map in `src/language.py` converts these to `"en"`, `"uk"`,
`"ru"`.

The `language` parameter passed to `GroqSTTService(...)` serves as a *prior
hint* (biases Whisper's detection) but does NOT override detection -- the
response's `language` field still reflects the actually detected language.

However, in the current Pipecat Groq integration, `TranscriptionFrame.language`
is populated with `self._settings.language` (the configured hint), NOT the
detected language. Therefore we must read the raw `result` attribute.

- Pros: zero latency, already computed, per-turn granularity
- Cons: requires reading an undocumented `result` attribute on the frame;
  requires `include_prob_metrics=True` for `verbose_json` response format;
  may break if Pipecat changes the response shape

**Option A2: Sticky `groq_language` setting (no detection)**

- Pros: simplest; no frame introspection needed
- Cons: user must restart daemon to switch language; no code-switching support
- *Rejected:* violates the core use case (multilingual household)

**Option A3: LLM-based language detection (extra generator call)**

- Pros: most accurate; works with any STT backend
- Cons: adds 200-500ms latency per turn; doubles LLM cost
- *Rejected:* violates Principle #1 (no new hot-path latency)

**Fallback strategy for A1:** If `frame.result` is missing or has no
`language` attribute, fall back to `settings.groq_language` (default `"en"`).
This means the system degrades to today's behavior rather than breaking.

#### B. Prompt strategy

**Option B1: Single English prompt with `{user_language}` instruction** -- chosen

One `prompts/generator.txt` in English. Contains a line:
*"The user is speaking {user_language}. Respond in the same language."*
The generator renders `{user_language}` from the detected language code.

- Pros: one file to maintain; model sees English instructions (best
  instruction-following); language switch is per-turn automatic
- Cons: model may occasionally respond in English when instructed to
  speak Ukrainian (mitigated by explicit instruction + few-shot examples
  in multiple languages + Gemini response language compliance logging)

**Option B2: N per-language prompt files (`generator_en.txt`, `generator_uk.txt`, ...)**

- Pros: each prompt is native-language; examples match the target language
- Cons: 3x maintenance burden; divergence risk; adding a language means
  copying and translating the entire prompt
- *Rejected:* maintenance cost is disproportionate for 3 languages;
  the model handles cross-lingual instruction well

### Known Tradeoff Tensions

1. **Per-turn detection vs sticky-with-hysteresis.** Resolved: per-turn
   detection with 2-turn hysteresis buffer. The active language only
   switches after 2 consecutive turns detect the same non-current language.
   This prevents single-turn flicker from STT misdetections while keeping
   code-switching responsive (2 turns = ~4-8 seconds of consistent speech).

2. **Single English prompt vs per-language prompt files.** Resolved: single
   English prompt with multilingual few-shot examples. Gemini Flash has
   strongest instruction-following on English prompts. Language compliance
   is monitored via Cyrillic/Latin script-ratio heuristic logging
   (`[LANG_MISMATCH]` at WARNING level) to catch regressions without
   adding latency.

3. **Including English "stop" in cancel patterns.** Resolved: included
   with conservative boundary regex (`(?:^|[\s.,!?—])stop(?:$|[\s.,!?—])`).
   False-positive risk exists in programming contexts ("stop the server")
   where the user may genuinely want an action. Mitigation: cancel only
   fires when there is a pending intent to cancel; "stop" in isolation
   (no pending intent) triggers a normal generator response. Document
   this risk and monitor during live smoke test.

---

## Scope

### Files to TOUCH (modify)

| File | Lines affected | Change summary |
|------|---------------|----------------|
| `src/config.py:145-166` | `tts_voice` default, `groq_language` docstring | Change `tts_voice` default to `"en-US-AriaNeural"`. Keep `groq_language` default as `"en"` (prior hint to Whisper; auto-detection flows from `verbose_json` response). Update `groq_language` docstring: "STT prior hint -- biases Whisper detection but does not override it". |
| `src/pipeline.py:80-83` | STT construction | Convert `settings.groq_language` to `Language` enum via `Language(settings.groq_language)`. Pass as `language=` hint. Pass `include_prob_metrics=True` to enable `verbose_json` response format (required for `result.language`). |
| `src/generator.py:30,37-39` | `FALLBACK_PHRASE`, `_CANCEL_RE` | Replace `FALLBACK_PHRASE` with English. Replace `_CANCEL_RE` single regex with `check_cancel()` from `src.language`. Extract detected language from `TranscriptionFrame`. Pass `user_language` to context builder. Add `lang=<detected>` to `[TIMING]` log line. Add 2-turn hysteresis for language switching. Add Gemini response language compliance logging. |
| `src/context.py:23,106-134` | `_EXCLUDED_FROM_GENERATOR_CTX`, `build_for_generator` | Add `user_language` to `build_for_generator` signature and output. |
| `src/tts_edge.py:87-108` | `EdgeTTSService.__init__`, `run_tts` | Add `set_voice(voice: str)` method to allow runtime voice swapping per turn. |
| `src/tts_phrases.py:12-20` | `FIXED_PHRASES` | Add English and Russian fallback phrases alongside existing Ukrainian ones. Keep confirmation phrases with `# DO NOT REMOVE` comment. |
| `src/main.py:428` | Greeting | Make startup greeting language-aware (use configured default language). |
| `prompts/generator.txt` | Entire file | Rewrite in English with `{user_language}` placeholder. Keep all existing placeholders. Translate examples to English. Add multilingual positive/negative examples. |
| `prompts/persona.txt:3` | "You speak Ukrainian" | Remove hardcoded "You speak Ukrainian" line. |
| `prompts/identity-bootstrap.txt:1-3` | "You speak Ukrainian" | Remove hardcoded Ukrainian language instruction. |
| `tests/test_generator_prompt.py` | Most tests | Update golden expectations: English baseline, `{user_language}` placeholder, multilingual examples. |
| `tests/test_generator.py:255-314,440-449` | Cancel keyword tests | Update to test multilingual cancel patterns. |
| `tests/test_config.py:10,17` | Default assertions | Update `tts_voice` and `groq_language` default assertions. |
| `tests/test_context.py` | `build_for_generator` tests | Add `user_language` to expected keys. |

### Files to CREATE (new)

| File | Purpose |
|------|---------|
| `src/language.py` | `SUPPORTED_LANGS`, `LANG_TO_VOICE`, `CANCEL_PATTERNS`, `WHISPER_NAME_TO_ISO`, `detect_language_from_frame()`, `voice_for_language()`, `check_cancel()`, `detect_script_language()` helper functions. Single module for all i18n constants and utilities. |
| `tests/test_language.py` | Unit tests for `src/language.py`: voice mapping, cancel patterns, language extraction, Whisper name normalization, fallback behavior. |

### Files to LEAVE ALONE (out of scope -- belong to PRD B or C)

- `src/tool_registry.py` -- PRD B (action result contract)
- `src/direct_tools.py` -- PRD B (spoken output per tool)
- `src/actions.py` -- PRD B (action result localization)
- `src/speaker_processor.py` -- no i18n dependency
- `src/speaker_gallery.py` -- no i18n dependency
- `src/speaker_namer.py` -- no i18n dependency
- `src/indication*.py` -- no i18n dependency
- `src/workflow.py` -- no i18n dependency
- `src/storage.py` -- no i18n dependency

---

## Story Dependencies

Recommended execution order based on the dependency graph:

```
US-I18N-01 (language module, standalone)
    |
    +---> US-I18N-06 (context builder gains user_language param)
    |         |
    |         +---> US-I18N-02 (config + STT changes, needs Language enum knowledge from 01)
    |         |         |
    |         |         +---> US-I18N-03 (generator wires detection through, depends on 01+02+06)
    |         |
    |         +---> US-I18N-04 (prompt rewrite, depends on 06's new placeholder)
    |
    +---> US-I18N-05 (TTS voice swap, depends on 01's voice_for_language)
    |
    +---> US-I18N-07 (fallback phrases, standalone but logically after 01)
              |
              +---> US-I18N-08 (pipeline threading, depends on 02+05)
                        |
                        +---> US-I18N-09 (regression, depends on all above)
                                  |
                                  +---> US-I18N-10 (smoke test, depends on 09 passing)
```

**Linear order:** 01 -> 06 -> 02 -> 03 -> 05 -> 04 -> 07 -> 08 -> 09 -> 10

---

## User Stories

### US-I18N-01 -- Language utilities module (`src/language.py`)

**As** the i18n subsystem
**I need** a single module that owns language constants, voice mapping,
cancel patterns, Whisper name normalization, and the frame-language
extraction function
**So that** no other module duplicates language logic

**Acceptance criteria:**

- `src/language.py` exists with:
  - `SUPPORTED_LANGS: set[str] = {"en", "uk", "ru"}`
  - `DEFAULT_LANG: str = "en"`
  - `LANG_NAMES: dict[str, str] = {"en": "English", "uk": "Ukrainian", "ru": "Russian"}`
  - `LANG_TO_VOICE: dict[str, str] = {"en": "en-US-AriaNeural", "uk": "uk-UA-OstapNeural", "ru": "ru-RU-DmitryNeural"}`
  - `WHISPER_NAME_TO_ISO: dict[str, str] = {"english": "en", "ukrainian": "uk", "russian": "ru"}`
    Maps Whisper's full language names (returned in `verbose_json` response
    format) to ISO-639-1 codes used internally.
  - `CANCEL_PATTERNS: dict[str, re.Pattern]` with entries:
    - `"en"`: `re.compile(r"(?i)(?:^|[\s.,!?—])(cancel|stop|abort|nevermind|never mind)(?:$|[\s.,!?—])")`
    - `"uk"`: `re.compile(r"(?i)(?:^|[\s.,!?—])(скасуй|відміни|стоп|не треба)(?:$|[\s.,!?—])")`
    - `"ru"`: `re.compile(r"(?i)(?:^|[\s.,!?—])(отмени|отмена|стоп|не надо)(?:$|[\s.,!?—])")`
  - `def detect_language_from_frame(frame, fallback: str = "en") -> str`:
    Reads `frame.result.language` if available (Whisper `verbose_json`
    response). The value is a full language name (e.g. `"english"`,
    `"ukrainian"`). Lowercases it, looks it up in `WHISPER_NAME_TO_ISO`.
    Returns the ISO code if found and in `SUPPORTED_LANGS`, otherwise
    returns `fallback`. If `frame.result` or `.language` is missing/None,
    returns `fallback`.
  - `def voice_for_language(lang: str) -> str`:
    Returns `LANG_TO_VOICE.get(lang, LANG_TO_VOICE["en"])`.
  - `def check_cancel(text: str, lang: str) -> bool`:
    Checks `CANCEL_PATTERNS.get(lang)` first; if no match AND lang is
    not `"en"`, also checks English patterns (English cancel words are
    universal fallback). Returns True if any pattern matches.
  - `def detect_script_language(text: str) -> str`:
    Lightweight Cyrillic vs Latin script-ratio heuristic. Counts Cyrillic
    and Latin characters in `text`. If Cyrillic ratio > 50%, returns
    `"cyrillic"`; if Latin ratio > 50%, returns `"latin"`; otherwise
    returns `"unknown"`. Used by the Gemini response language compliance
    check (not for primary language detection).
- `tests/test_language.py` has >= 14 tests:
  - (a) `voice_for_language` returns correct voice for en/uk/ru
  - (b) `voice_for_language` returns English voice for unknown lang "fr"
  - (c) `detect_language_from_frame` extracts "uk" from mock frame with
    `result.language = "ukrainian"` (Whisper full name)
  - (d) `detect_language_from_frame` extracts "en" from mock frame with
    `result.language = "english"`
  - (e) `detect_language_from_frame` returns fallback when frame has no
    `result` attribute
  - (f) `detect_language_from_frame` returns fallback when
    `result.language` is unsupported (e.g. `"japanese"`)
  - (g) `detect_language_from_frame` returns fallback when
    `result.language` is None
  - (h) `check_cancel("cancel this", "en")` returns True
  - (i) `check_cancel("скасуй", "uk")` returns True
  - (j) `check_cancel("отмени", "ru")` returns True
  - (k) `check_cancel("hello", "en")` returns False
  - (l) `check_cancel("cancel", "uk")` returns True (English fallback)
  - (m) `check_cancel("стоп", "uk")` returns True (in uk patterns)
  - (n) `detect_script_language("Привіт світ")` returns `"cyrillic"`;
    `detect_script_language("Hello world")` returns `"latin"`;
    `detect_script_language("")` returns `"unknown"`

### US-I18N-02 -- Config defaults and STT with language hint + verbose_json

**As** the pipeline operator
**I need** the STT to use `verbose_json` response format (for language
detection) with a sensible language hint, and the default TTS voice
to match the new English default
**So that** new installations support multilingual detection out of the box

**Acceptance criteria:**

- `src/config.py:145` -- `tts_voice: str = "en-US-AriaNeural"` (was
  `"uk-UA-PolinaNeural"`)
- `src/config.py:166` -- `groq_language: str = "en"` (was `"uk"`).
  Docstring updated: *"STT prior hint -- biases Whisper's language
  detection but does not override it. The actual detected language is
  read from the verbose_json response. Set to any ISO-639-1 code
  (e.g. 'en', 'uk', 'ru')."*
- `src/pipeline.py:80-83` -- convert `settings.groq_language` to a
  `Language` enum: `Language(settings.groq_language)` (e.g. `"en"` ->
  `Language.EN`). Pass as `language=` hint to `GroqSTTService(...)`.
  Also pass `include_prob_metrics=True` to `GroqSTTService(...)` to
  enable `verbose_json` response format, which populates
  `response.language` with the detected language.
  Note: `include_prob_metrics` is a `BaseWhisperSTTService` parameter
  (base_stt.py:137), inherited by `GroqSTTService` via `**kwargs`.
- `tests/test_config.py` updated:
  - `assert s.tts_voice == "en-US-AriaNeural"` (line ~10)
  - `assert s.groq_language == "en"` (line ~17)
- `tests/test_pipeline.py` updated if it asserts on the `language`
  kwarg passed to `GroqSTTService`. Also verify `include_prob_metrics=True`
  is passed.
- Existing users with `groq_language = "uk"` in `~/.heare/config.toml`
  continue to work (pinned language hint to Whisper, detection still works).
- **Risk note:** `include_prob_metrics=True` switches the Groq API
  response format from `"json"` to `"verbose_json"`, which may add
  ~10-50ms of response-size delta. Measure during live smoke test
  (US-I18N-10). If TTFT regresses beyond 2s budget, investigate whether
  the delta is from response parsing or network transfer.

### US-I18N-03 -- Generator extracts, hysteresis-buffers, and propagates detected language

**As** the GeneratorProcessor
**I need** to read the detected language from each `TranscriptionFrame`,
apply 2-turn hysteresis to prevent voice flicker, pass the active
language to the context builder, use it for cancel-keyword matching,
set the TTS voice before speaking, log the language in TIMING, and
verify Gemini's response language compliance
**So that** each turn is processed in the user's detected language with
stability against single-turn misdetections

**Acceptance criteria:**

- `src/generator.py` imports `detect_language_from_frame`, `check_cancel`,
  `voice_for_language`, `detect_script_language`, `LANG_NAMES` from
  `src.language`.

- **Language detection + hysteresis (2-turn buffer):**
  - `GeneratorProcessor.__init__` gains:
    - `self._active_lang: str` initialized from `settings.groq_language`
      (if `"auto"` or missing -> `"en"`).
    - `self._pending_lang: str | None = None` -- the tentatively detected
      non-current language.
    - `self._pending_lang_count: int = 0` -- how many consecutive turns
      have detected `_pending_lang`.
  - In `_handle_transcription` (line ~328):
    1. After extracting `transcript`, call
       `raw_lang = detect_language_from_frame(frame, fallback=self._active_lang)`.
    2. Apply hysteresis:
       - If `raw_lang == self._active_lang`: reset `_pending_lang = None`,
         `_pending_lang_count = 0`. Use `self._active_lang`.
       - If `raw_lang != self._active_lang` and `raw_lang == self._pending_lang`:
         increment `_pending_lang_count`. If `_pending_lang_count >= 2`:
         set `self._active_lang = raw_lang`, reset pending state.
       - If `raw_lang != self._active_lang` and `raw_lang != self._pending_lang`:
         set `_pending_lang = raw_lang`, `_pending_lang_count = 1`.
    3. Use `self._active_lang` (NOT `raw_lang`) for all downstream:
       cancel matching, context builder, TTS voice, TIMING log.

- **Cancel matching:**
  - Replace `if _CANCEL_RE.search(transcript):` (line ~376) with
    `if check_cancel(transcript, self._active_lang):`.
  - Delete `_CANCEL_RE` from `src/generator.py` (line 37-39). The cancel
    logic is now in `src/language.py`.

- **Context propagation:**
  - Pass `user_language=self._active_lang` to
    `self.context_builder.build_for_generator(...)`.

- **TTS voice swap:**
  - Before pushing TTS frames, call `self._set_tts_voice(self._active_lang)`
    (see US-I18N-05 for TTS voice swapping mechanism).

- **TIMING log line:**
  - Extend the existing `[TIMING] generator transcript="..." ttft=...ms
    chunks=... intents=...` log line (line ~477) to include
    `lang=<self._active_lang>` after `transcript=`. Example:
    `[TIMING] generator transcript="hello" lang=en ttft=120ms chunks=3 intents=0`

- **Gemini response language compliance logging:**
  - After the generator assembles the full reply text (before TTS push),
    run `detect_script_language(reply_text)` from `src.language`.
  - Determine expected script: if `self._active_lang` in `{"uk", "ru"}`,
    expected script is `"cyrillic"`; if `"en"`, expected is `"latin"`.
  - If the detected script does not match expected AND the reply is
    non-empty AND longer than 5 characters (skip short "ok" responses):
    log `[LANG_MISMATCH] expected=<active_lang> detected_script=<script> transcript="<first 80 chars>" reply="<first 80 chars>"`
    at WARNING level to `daemon.log`.
  - No retry on mismatch (latency budget). This is observability only.

- `FALLBACK_PHRASE` (line 30): change from `"Хвилинку, щось не так."` to
  `"One moment, something went wrong."` (English baseline; the generator
  will normally respond in the detected language, but the fallback fires
  when the LLM itself fails, so English is the safe default).

- `tests/test_generator.py`:
  - Update `test_cancel_keyword_pops_pending_intent` (line 255): use a
    mock frame with `result.language = "ukrainian"` and transcript "скасуй".
    Set `_active_lang = "uk"` on the processor (or process 2 Ukrainian
    turns first to trigger hysteresis).
  - Add `test_cancel_keyword_english`: mock frame with
    `result.language = "english"`, transcript "cancel" -> cancels pending.
  - Add `test_cancel_keyword_russian`: mock frame with
    `result.language = "russian"`, transcript "отмени" -> cancels pending.
  - Update `test_cancel_keyword_negative_cases` (line 298): verify
    "стоп-кадр" does NOT trigger cancel (boundary check).
  - Update `test_cancel_keyword_positive_edge_cases` (line 440): add
    English and Russian edge cases alongside existing Ukrainian ones.
  - Add `test_language_propagated_to_context`: verify the `user_language`
    key appears in the context dict passed to the prompt template.
  - Add `test_hysteresis_single_detection_no_swap`: process one turn with
    `result.language = "ukrainian"` while `_active_lang = "en"`. Assert
    `_active_lang` is still `"en"` (no swap on single detection).
  - Add `test_hysteresis_two_consecutive_detections_swap`: process two
    consecutive turns with `result.language = "ukrainian"` while
    `_active_lang = "en"`. Assert `_active_lang` is now `"uk"`.
  - Add `test_lang_field_in_timing_log`: verify `[TIMING]` log line
    contains `lang=` field.
  - Add `test_lang_mismatch_logging`: mock a generator response in Latin
    script while `_active_lang = "uk"`. Verify `[LANG_MISMATCH]` WARNING
    is logged.

### US-I18N-04 -- Prompts rewritten in English with `{user_language}`

**As** the generator prompt
**I need** to be written in English with a `{user_language}` placeholder
**So that** the LLM understands instructions clearly across all models
and responds in the detected language

**Acceptance criteria:**

- `prompts/generator.txt` rewritten:
  - All instructional text in English.
  - `{user_language}` placeholder added. Rendered as the full language
    name (e.g. "Ukrainian", "English", "Russian") -- the context builder
    maps `"uk"` -> `"Ukrainian"`, etc.
  - Instruction: *"The user is speaking {user_language}. Always respond
    in {user_language}. If the detected language is uncertain, respond
    in English."*
  - Reply rules translated to English equivalents: "Respond in ONE
    sentence. Maximum 12 words." etc.
  - INTENTS section rewritten in English. Tool names unchanged. Schema
    unchanged (JSON is language-neutral).
  - Positive examples provided in all three languages (at least 2 per
    language: en, uk, ru). Example format:
    ```
    User (Ukrainian): "запусти echo привіт"
    Response: Виконую. <intent>{"tool":"bash","args":"echo привіт"}</intent>

    User (English): "run echo hello"
    Response: On it. <intent>{"tool":"bash","args":"echo hello"}</intent>

    User (Russian): "запусти echo привет"
    Response: Выполняю. <intent>{"tool":"bash","args":"echo привет"}</intent>
    ```
  - Negative examples in all three languages (at least 1 per language).
  - Forbidden commands section in English.
  - All existing placeholders preserved: `{persona}`, `{time}`,
    `{timezone}`, `{recent_transcripts}`, `{mcp_servers}`,
    `{conversation_summary}`, `{active_topics}`, `{entities}`,
    `{recent_turns}`, `{recent_actions}`, `{transcript}`.
  - New placeholder: `{user_language}`.
- `prompts/persona.txt` line 3: change `"You speak Ukrainian."` to
  `"You speak the user's language."` (the generator prompt handles
  the specific language instruction; persona should not override it).
- `prompts/identity-bootstrap.txt` lines 1-3: remove
  `"You speak Ukrainian."` from the identity generation prompt (identity
  is language-neutral; the creature's name should still be short and
  pronounceable).
- `tests/test_generator_prompt.py` updates:
  - `test_template_has_exactly_expected_placeholders`: add
    `"user_language"` to expected set.
  - `test_template_requires_ukrainian_response` -> rename to
    `test_template_requires_user_language_response`: assert
    `"{user_language}"` present and instruction about responding in
    that language present.
  - `test_template_reply_text_forbids_json`: adapt assertion from
    Ukrainian "без JSON" to English equivalent ("no JSON" or "never
    output JSON").
  - `test_template_enforces_one_sentence_reply`: adapt from
    Ukrainian "ОДНИМ реченням" to English "ONE sentence".
  - `test_template_bans_filler`: adapt from Ukrainian filler names to
    English equivalents ("thanks", "apologies", "alternatives").
  - `test_template_caps_pre_intent_wording`: adapt from Ukrainian
    "до 5 слів" to English "up to 5 words" or "5 words".
  - `test_template_forbids_permission_asks_after_command`: adapt from
    Ukrainian "не питай дозволу" to English "do not ask permission".
  - `test_template_expands_ukrainian_trigger_verbs` -> rename to
    `test_template_lists_trigger_verbs_multilingual`: verify English
    trigger verbs ("run", "open", "execute", "close", "create", "find")
    AND Ukrainian trigger verbs are present in the examples.
  - `test_template_has_positive_and_negative_intent_examples`: update
    counts to reflect multilingual examples.
  - `test_substitution_leaves_no_placeholders`: add `"user_language":
    "Ukrainian"` to context dict.
  - All 17+ existing tests must have updated equivalents that pass.

### US-I18N-05 -- TTS voice swapping per turn

**As** the TTS subsystem
**I need** to switch the edge-tts voice based on the detected language
**So that** the spoken reply matches the user's language acoustically

**Acceptance criteria:**

- `src/tts_edge.py`:
  - `EdgeTTSService` gains a `set_voice(self, voice: str) -> None`
    method that updates `self._voice` and `self._settings.voice` (the
    Pipecat TTSSettings field). This is safe because `run_tts` reads
    `self._voice` per call (line 181), not at construction time.
  - No changes to the streaming/ffmpeg pipeline.
- `src/generator.py` -- `GeneratorProcessor.__init__` accepts an
  optional `tts_service` parameter (the `EdgeTTSService` instance from
  pipeline assembly). Stored as `self._tts_service`.
- `src/generator.py` -- new method `_set_tts_voice(self, lang: str)`:
  calls `voice_for_language(lang)` and if the result differs from
  `self._current_voice`, calls `self._tts_service.set_voice(new_voice)`,
  updates `self._current_voice`, and logs
  `[TTS VOICE SWAP] from=<old_voice> to=<new_voice> lang=<lang>` at
  INFO level. If `self._tts_service is None`, no-op (test harness path).
  If the voice is the same as `self._current_voice`, no log (no-op).
- `src/pipeline.py:97-106` -- pass `tts_service=tts` to
  `create_generator_processor(...)`.
- `src/generator.py::create_generator_processor` -- accept `tts_service`
  kwarg, thread into `GeneratorProcessor.__init__`.
- `tests/test_generator.py`:
  - `test_tts_voice_swap_on_language_change`: mock TTS service, process
    a Ukrainian transcript (with hysteresis: 2 turns), assert `set_voice`
    called with `"uk-UA-OstapNeural"`. Verify `[TTS VOICE SWAP]` log
    line emitted with `from=` and `to=` fields.
    Then process an English transcript (2 turns), assert `set_voice`
    called with `"en-US-AriaNeural"`.
  - `test_tts_voice_no_swap_same_language`: two Ukrainian transcripts
    in a row (after language is already "uk") -> `set_voice` not called
    again. No `[TTS VOICE SWAP]` log emitted.
  - `test_tts_voice_swap_without_service`: `tts_service=None` ->
    no crash, voice swap silently skipped.

### US-I18N-06 -- Context builder surfaces `user_language`

**As** the prompt renderer
**I need** `build_for_generator` to accept and project `user_language`
**So that** the generator prompt can instruct the LLM which language to
reply in

**Acceptance criteria:**

- `src/context.py::build_for_generator` signature gains
  `user_language: str = "en"`.
- Output dict gains key `"user_language"` with value mapped to full name:
  `{"en": "English", "uk": "Ukrainian", "ru": "Russian"}.get(user_language, "English")`.
  (Or import `LANG_NAMES` from `src.language` and use
  `LANG_NAMES.get(user_language, "English")`.)
- `_EXCLUDED_FROM_GENERATOR_CTX` unchanged (user_language is a bfg-only
  key, like `persona` and `transcript`).
- `tests/test_context.py`:
  - `test_build_for_generator_returns_minimal_keys`: add
    `"user_language"` to expected key set (11 keys total).
  - `test_build_for_generator_user_language_mapping`: verify "uk" ->
    "Ukrainian", "en" -> "English", "ru" -> "Russian", "fr" -> "English".
  - Existing drift-guard test
    `test_context_builder_keys_accounted_for` continues to pass
    (user_language is bfg-only, does not appear in `build()` output).

### US-I18N-07 -- Fallback phrases for all supported languages

**As** the TTS cache
**I need** fallback and system phrases in all three languages pre-warmed
**So that** error recovery and system responses play instantly regardless
of the active language

**Acceptance criteria:**

- `src/tts_phrases.py::FIXED_PHRASES` updated:
  - English: `"One moment, something went wrong."`, `"okay"`,
    `"cancelled"`, `"Say yes or no"`.
  - Ukrainian: keep existing `"Хвилинку, щось не так."`,
    `"Скажи: так чи ні?"`, `"дія не вдалася"`.
  - Russian: `"Минутку, что-то не так."`, `"Скажи: да или нет?"`,
    `"действие не удалось"`.
  - **Keep** `"Скажи: гава так, або гава ні"` and
    `"Скажи пароль, або гава ні"` in `FIXED_PHRASES`. These are actively
    used by the confirmation flow in `src/decider.py` (lines 932, 981).
    Add a comment: `# DO NOT REMOVE -- used by confirmation flow in decider.py. Deferred to PRD C for localization.`
- `src/main.py:428` -- greeting: use `LANG_NAMES.get(default_lang, "English")`
  to pick greeting language. `"en"` -> `"{name} online"`,
  `"uk"` -> `"{name} на зв'язку"`, `"ru"` -> `"{name} на связи"`.
- No changes to `src/tts_cache.py` (it already iterates `FIXED_PHRASES`).

### US-I18N-08 -- Pipeline threading

**As** the pipeline assembly
**I need** language detection results to flow from STT through the
generator to TTS without adding new frame types or middleware
**So that** the architecture stays simple

**Design decision:** We do NOT introduce a new Pipecat frame type for
language. The `TranscriptionFrame` already carries `result` (the raw
Whisper response) which contains the detected language. The generator
reads it, uses it for cancel/prompt/TTS, and the language never needs
to be a separate frame in the pipeline. This avoids Pipecat version
coupling and frame-ordering complexity.

**Acceptance criteria:**

- `src/pipeline.py` -- `create_generator_processor(...)` call gains
  `tts_service=tts` kwarg (from US-I18N-05).
- `src/pipeline.py:80-83` -- STT language parameter handling updated
  per US-I18N-02. `include_prob_metrics=True` passed to `GroqSTTService`.
- No new FrameProcessor stages added to the pipeline.
- No new frame types created.
- `tests/integration/test_s2s_pipeline.py` -- verify pipeline builds
  without error when `groq_language="en"` (default) and when
  `groq_language="uk"` (pinned hint).

### US-I18N-09 -- Regression + lint green

**As** the codebase
**I need** all tests green, lint clean, no new skipped tests
**So that** PRD A doesn't break existing functionality

**Acceptance criteria:**

- `uv run pytest -q` exits 0. Target: ~852 existing + ~30 new = ~882.
- `uv run ruff check` exits 0.
- No new skipped tests.
- Specifically verify these test files pass (they are most likely to
  break):
  - `tests/test_generator_prompt.py` -- golden expectations updated
  - `tests/test_generator.py` -- cancel keyword tests updated
  - `tests/test_config.py` -- default value assertions updated
  - `tests/test_context.py` -- key set assertions updated
  - `tests/test_pipeline.py` -- STT construction assertions updated
  - `tests/test_main.py` -- greeting assertion if any
  - `tests/test_main_cli.py` -- if it references config defaults
  - `tests/fixtures/decider_prompt_flag_off.golden.txt` -- if it
    contains Ukrainian-only assertions (unlikely but check)
- Integration tests pass:
  - `tests/integration/test_s2s_pipeline.py`
  - `tests/integration/test_intent_flow.py`

### US-I18N-10 -- Live verification recipe

**As** the plan author
**I need** a repeatable manual test to prove the multilingual loop works
**So that** "done" is evidence-based

**Acceptance criteria:**

- Three-turn smoke test documented and executed:
  1. **English turn:** Say "hello, what time is it?" ->
     Bot replies in English. TTS voice is `en-US-AriaNeural`.
     `daemon.log` shows `[TIMING] generator ... lang=en`.
  2. **Ukrainian turn:** Say "скасуй" with a pending intent ->
     Cancel fires. Bot replies in Ukrainian. TTS voice is
     `uk-UA-OstapNeural`.
     (Note: requires 2 Ukrainian turns due to hysteresis if previous
     active language was English. Test with two Ukrainian turns.)
  3. **Russian turn:** Say "запусти echo привет" ->
     Bot replies in Russian with intent tag. TTS voice is
     `ru-RU-DmitryNeural`.
     (Note: requires 2 Russian turns due to hysteresis.)
- Verify from `daemon.log`:
  - Each turn logs `lang=XX` matching the spoken language (in `[TIMING]`
    log line).
  - `[TTS VOICE SWAP] from=<old> to=<new> lang=<lang>` logged when
    language changes (after hysteresis threshold met).
  - No `[TTS VOICE SWAP]` when language stays the same.
  - No `[LANG_MISMATCH]` warnings (Gemini responds in the correct
    language). If warnings appear, note them as findings but do not
    block -- this is observability, not a gate.
- Measure `include_prob_metrics=True` impact: compare TTFT with and
  without (toggle in config). Note any delta in smoke test results.
- Results written to `.omc/benchmarks/prd-a-i18n-live-smoke.md`.
- TTFT stays <=2s (no regression from language detection overhead).

---

## Test Plan

### Existing tests that WILL break (require update)

| Test file | Tests affected | Reason |
|-----------|---------------|--------|
| `tests/test_generator_prompt.py` | ~17 tests | All golden assertions reference Ukrainian text, Ukrainian placeholder expectations. Every test needs updating for English baseline + `{user_language}`. |
| `tests/test_generator.py:255-314,440-449` | 4 tests | Cancel keyword tests use Ukrainian-only `_CANCEL_RE`. Need multilingual cancel via `check_cancel()`. |
| `tests/test_config.py:10,17` | 2 assertions | Default `tts_voice` and `groq_language` values change. |
| `tests/test_context.py` | ~2 tests | `build_for_generator` key set gains `user_language`. |

### New tests to add

| Test file | Count | Covers |
|-----------|-------|--------|
| `tests/test_language.py` | ~14 | Voice mapping, cancel patterns, language extraction, Whisper name normalization, script detection, fallback (US-I18N-01) |
| `tests/test_generator.py` (additions) | ~10 | English/Russian cancel, language propagation, TTS voice swap, hysteresis buffer, TIMING lang field, LANG_MISMATCH logging (US-I18N-03, US-I18N-05) |
| `tests/test_context.py` (additions) | ~2 | `user_language` mapping and key presence (US-I18N-06) |
| `tests/test_generator_prompt.py` (rewrites) | ~17 | English baseline golden tests (US-I18N-04) |

**Net new test count:** ~30 new + ~25 rewritten = ~55 test changes.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Whisper `result.language` attribute missing or renamed in future Pipecat versions | Low | Medium | `detect_language_from_frame` has try/getattr fallback; degrades to configured `groq_language` hint or `"en"` |
| Gemini responds in English despite `{user_language}` instruction | Medium | Medium | Explicit instruction + multilingual few-shot examples. Cyrillic/Latin script-ratio heuristic logs `[LANG_MISMATCH]` at WARNING level for observability (no retry, no latency impact). Live smoke test validates. PRD C can add stronger per-language persona if needed |
| Edge-tts voice ID invalid or deprecated | Low | Low | Voice IDs are stable; `edge-tts --list-voices` can verify. Pinned IDs: `en-US-AriaNeural`, `uk-UA-OstapNeural`, `ru-RU-DmitryNeural`. Fallback: if synth fails, TTS error handler already exists |
| `groq_language="en"` hint degrades STT accuracy for non-English audio | Low | Medium | Whisper's language hint is a prior, not a constraint. Auto-detection is >95% accurate for en/uk/ru even with an English hint. Users who want a stronger prior can set `groq_language = "uk"` in `config.toml` |
| Cancel false positives with English "stop" (common word) | Medium | Low | Boundary regex prevents substring matches. "stop" only triggers cancel when a pending intent exists. If no pending intent, the word goes through normal generator flow. Monitor during live smoke test |
| `include_prob_metrics=True` adds STT response latency | Low | Low | Switches response format from `"json"` to `"verbose_json"`. May add ~10-50ms response-size delta. Measure in smoke test (US-I18N-10). If TTFT > 2s budget, investigate |
| Breaking change for existing users with `config.toml` defaults | Low | Medium | Users who explicitly set `groq_language = "uk"` and `tts_voice = "uk-UA-PolinaNeural"` keep their settings. Only defaults change |
| Whisper returns full language names, not ISO codes | Certain | Medium | `WHISPER_NAME_TO_ISO` normalization map in `src/language.py` handles conversion. Unknown names fall back to English. Unit tested |
| Single-turn language flicker from STT misdetection | Medium | Medium | 2-turn hysteresis buffer in generator. Active language only switches after 2 consecutive turns detect the same non-current language. Prevents voice/prompt oscillation from noisy detections |

---

## ADR

- **Decision:** Convert heare's prompt baseline to English, detect
  language per turn from Whisper STT `verbose_json` metadata (via
  `include_prob_metrics=True`), apply 2-turn hysteresis before switching
  active language, instruct the generator to respond in the active
  language, swap TTS voice per turn, and replace the Ukrainian-only
  cancel gate with a multilingual pattern table.
- **Drivers:** (1) Household has multilingual speakers (uk/en/ru
  code-switching). (2) English prompts give best LLM instruction-following
  across models. (3) Per-turn detection is zero-latency (Whisper already
  computes it, but requires `verbose_json` format).
- **Alternatives considered:**
  - Sticky session language: rejected -- no code-switching support.
  - LLM-based detection: rejected -- adds 200-500ms hot-path latency.
  - Per-language prompt files: rejected -- 3x maintenance, divergence risk.
  - `language=None` to Whisper (pure auto-detect, no hint): rejected --
    `GroqSTTService._transcribe()` asserts `language is not None`
    (groq/stt.py:122). Would crash on every audio frame.
  - Raw per-turn detection without hysteresis: viable but causes voice
    flicker when Whisper momentarily misdetects language (e.g. short
    utterances, mixed-language phrases). Rejected in favor of 2-turn
    hysteresis which adds minimal latency to real language switches
    (~2 turns = 4-8 seconds) while eliminating single-turn jitter.
- **Why chosen:** Zero-latency language detection from existing STT metadata.
  Single English prompt with `{user_language}` instruction is maintainable
  and model-agnostic. Static 3-entry voice map is correct for the supported
  language set. 2-turn hysteresis prevents flicker without adding perceptible
  delay to genuine language switches.
- **Consequences:**
  - All prompt golden tests must be rewritten (one-time cost).
  - Default config changes may surprise users who relied on Ukrainian
    defaults (mitigated: explicit `config.toml` values are preserved).
  - `FALLBACK_PHRASE` becomes English (acceptable: fallback fires only
    on LLM failure; English is the safest default).
  - Persona prompt loses hardcoded "speak Ukrainian" (correct: language
    is now dynamic per turn).
  - `include_prob_metrics=True` may add ~10-50ms response-size delta
    (acceptable: well within 2s TTFT budget; measured in smoke test).
  - Confirmation phrases (`"Скажи: гава так, або гава ні"` and
    `"Скажи пароль, або гава ні"`) remain in Ukrainian only for now.
    They are actively used by `src/decider.py` (lines 932, 981).
    Localization deferred to PRD C.
- **Follow-ups:**
  - PRD B: voice-friendly action result contract (`{output, spoken}` per
    direct tool, English templates).
  - PRD C: per-language persona variants, locale polish, greeting
    customization, confirmation phrase localization (including the
    `"гава так/ні"` and `"пароль"` phrases currently hardcoded in
    `src/decider.py`).
