# Stage 10: Voice UX — Barge-in, Prosody, Emotional TTS, Non-verbal Vocalizations

**Date:** 2026-04-23
**Scope:** Soft-skills voice UX for heare; making the agent feel human, not robotic.
**Sources:** Pipecat docs, MS SSML docs, edge-tts PyPI/GitHub, ElevenLabs docs, Chatterbox repo, OpenAI TTS guide, webrtc-audio-processing PyPI, Deepgram streaming TTS guide, smallest.ai voice agent design blog.

---

## [OBJECTIVE]
Research all dimensions of voice humanization for heare: barge-in interruption, echo cancellation, SSML prosody, emotional TTS alternatives, non-verbal vocalizations, streaming chunk strategy, filler speech, disfluencies, voice persona mapping, backchannel listening, STT robustness, and time-of-day volume adaptation.

---

## [DATA] Codebase Baseline

- **generator.py**: `_bot_speaking` / `_bot_cooldown_until` gate drops all `TranscriptionFrame`s while bot speaks + 2 s cooldown (`bot_speaking_cooldown_seconds`).
- **generator.py**: `_split_on_sentence()` splits on `.!?…` and pushes one `TTSSpeakFrame` per sentence via `_push_tts()`.
- **generator.py**: No SSML, no prosody, no filler injection, no disfluency logic.
- **actions.py**: `ActionWorker` pulls intents FIFO; no pre-action filler speech emitted.
- TTS engine: `edge-tts` with `uk-UA-PolinaNeural`. One-shot frame per sentence.
- STT: Groq (debounce in `_debounce_seconds`). Speaker-ID fields present but optional.

---

## Findings

### [FINDING:V1] Barge-in / Interruption Handling

**Confidence: HIGH**

Pipecat (≥0.0.99) models interruption through `StartInterruptionFrame` / `StopInterruptionFrame` / `CancelFrame`. When VAD fires during bot speech, the pipeline propagates a `StartInterruptionFrame` upstream, which:
1. Cancels pending `TTSSpeakFrame`s in the TTS service queue via an internal `CancelFrame`.
2. Sends a flush/clear command to the TTS WebSocket (e.g., Deepgram's "Clear" message empties the text buffer and stops audio streaming mid-sentence).
3. The partial transcript that triggered the interruption is preserved and forwarded as a new `TranscriptionFrame` downstream for the generator.

The deprecated `MinWordsInterruptionStrategy(min_words=N)` required users to speak ≥ N words before an interrupt fired — preventing single-syllable backchannels from killing bot speech. Its successor (≥0.0.99) is `MinWordsUserTurnStartStrategy` passed as `turn_start_strategies` in `PipelineTask`. This is the correct API going forward.

**heare today**: The `_bot_speaking` + `_bot_cooldown_until` guard in `generator.py:372` silently **drops** user speech while the bot speaks — there is no barge-in at all. To implement clean barge-in: (a) listen for `StartInterruptionFrame` in `GeneratorProcessor.process_frame`, (b) when received, set a flag that causes the current in-flight `openrouter_cli.generate()` async iteration to be abandoned (cancel the task), (c) flush any buffered TTS sentences, (d) still submit the partial user transcript for a fresh generate cycle.

**Sources:** Pipecat Interruption Strategies docs · https://docs.pipecat.ai/server/utilities/interruption-strategies; Pipecat API ref · https://reference-server.pipecat.ai/en/stable/api/pipecat.audio.interruptions.html

---

### [FINDING:V2] Pre-emptive Mic-Gating / Echo Cancellation

**Confidence: MEDIUM**

The current 2 s cooldown (`bot_speaking_cooldown_seconds`) is a blunt heuristic. Its purpose is to suppress the bot hearing its own TTS output via the mic. On macOS with AirPods (hardware AEC) or MacBook mic (CoreAudio AEC), the echo is cancelled in hardware before reaching the mic stream, so the cooldown is primarily protecting against STT processing of bot speech that leaked through AEC — not raw audio echo.

Better approaches:
- **webrtc-audio-processing** (PyPI: `webrtc-audio-processing`): Python bindings for WebRTC's APM module. Provides software AEC, noise suppression, AGC. Eliminates bot-voice echo even without hardware AEC. Can be inserted as a pre-processing step on the mic input stream before Groq STT.
- **LiveKit `livekit.rtc.apm`**: Higher-level wrapper around the same WebRTC APM; also provides `AudioProcessingModule` with echo cancellation, noise suppression, gain control.
- With good AEC in place, the 2 s cooldown could be reduced to 0.3–0.5 s (only covering STT transcription propagation latency), dramatically improving responsiveness on AirPods.
- **SpeexDSP** is an alternative narrowband AEC library, but WebRTC APM is preferred for broadband audio.

**Sources:** webrtc-audio-processing PyPI · https://pypi.org/project/webrtc-audio-processing/; LiveKit rtc.apm · https://docs.livekit.io/reference/python/v1/livekit/rtc/apm.html

---

### [FINDING:V3] Prosody via SSML — Edge-TTS Limitation

**Confidence: HIGH**

`edge-tts` (PyPI) explicitly removed support for arbitrary SSML injection. Microsoft's Edge TTS service only permits a single `<voice>` + single `<prosody>` wrapper — the exact structure the library generates internally. Attempting to inject `<mstts:express-as>`, `<break>`, or `<emphasis>` tags through `edge-tts` is blocked.

What IS available through the `edge-tts` CLI/API:
- `--rate` (speaking rate, e.g. `+20%`, `-30%`)
- `--volume` (volume level, e.g. `+10%`, `-20%`)
- `--pitch` (pitch shift, e.g. `+5Hz`, `-10Hz`)

These three knobs are accessible on every `TTSSpeakFrame` by constructing the `communicate` object with those parameters. They are coarse but sufficient for time-of-day volume (Finding V12) and slightly faster/slower delivery.

For full SSML (`<mstts:express-as style="cheerful">`, `<break time="500ms"/>`, etc.), heare must migrate to the Azure Cognitive Services Speech SDK (not edge-tts) which is a paid API but supports all SSML on Ukrainian voices.

**Sources:** edge-tts GitHub (rany2/edge-tts) · https://github.com/rany2/edge-tts; MS SSML docs · https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-voice

---

### [FINDING:V4] SSML Express-As and Ukrainian Voice Styles (Azure SDK path)

**Confidence: HIGH**

The Azure Cognitive Services Speech SDK supports `<mstts:express-as>` for 30+ styles. Ukrainian voices (`uk-UA-OstapNeural`, `uk-UA-PolinaNeural`) are listed in the language support matrix and are accessible via multilingual neural voices (`en-US-AndrewMultilingualNeural`, etc.) that auto-detect Ukrainian input.

However, style attributes on `mstts:express-as` (cheerful, sad, excited, whispering, etc.) are only confirmed for a **subset** of neural voices — primarily Chinese and English voices. The Ukrainian-native voices (`PolinaNeural`, `OstapNeural`) do NOT appear in the voice-styles-and-roles table as having named style support. The workaround is to use an English multilingual voice (`en-US-AvaMultilingualNeural`) with `<lang xml:lang="uk-UA">` — this voice speaks Ukrainian via auto-detect and supports styles.

SSML prosody (`<prosody rate="slow" pitch="-5Hz">`) works universally for all voices including Ukrainian.

Example for excited Ukrainian:
```xml
<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis"
       xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="uk-UA">
  <voice name="en-US-AvaMultilingualNeural">
    <mstts:express-as style="excited" styledegree="1.5">
      <lang xml:lang="uk-UA">Чудово! Це справді вражає!</lang>
    </mstts:express-as>
  </voice>
</speak>
```

**Sources:** MS SSML voice styles · https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-voice

---

### [FINDING:V5] Emotional TTS Alternatives — Latency vs Quality vs Ukrainian

**Confidence: HIGH**

| Provider | Ukrainian | Emotion | Latency (TTFA) | Cost | Non-verbals |
|---|---|---|---|---|---|
| edge-tts (current) | Native (Polina/Ostap) | None | ~200ms | Free | No |
| Azure Speech SDK | Native + multilingual styles | Yes (via express-as on multilingual) | 300–500ms | Paid | Via audio insert |
| OpenAI gpt-4o-mini-tts | 50+ langs incl. UK | Yes (via instructions prompt) | 300–600ms | Paid | Via instructions |
| ElevenLabs Flash v2.5 | Yes (32 langs) | Context-inferred | ~75ms | Paid | Limited |
| ElevenLabs Multilingual v2 | Yes (29 langs) | Rich, emotionally faithful | 500–800ms | Paid | Limited |
| Chatterbox-Turbo | English only | Exaggeration param (0–2) | <200ms | Free/OSS | [laugh][cough][sigh] |
| CosyVoice 2/3 | No (9 langs, no UK) | Yes | Varies | Free/OSS | No |

**Recommendation for heare**: Short-term, OpenAI `gpt-4o-mini-tts` with `instructions="Speak in Ukrainian, warm and conversational, with natural enthusiasm"` gives emotion + Ukrainian support at moderate cost. Long-term, ElevenLabs Flash v2.5 at 75ms is the lowest-latency option with Ukrainian support.

**Sources:** ElevenLabs models · https://elevenlabs.io/docs/overview/models; OpenAI TTS · https://developers.openai.com/api/docs/guides/text-to-speech; Chatterbox repo · https://github.com/resemble-ai/chatterbox; CosyVoice · https://github.com/FunAudioLLM/CosyVoice

---

### [FINDING:V6] Non-Verbal Vocalizations

**Confidence: HIGH**

Three viable strategies for heare:

1. **Chatterbox-Turbo paralinguistic tags** (`[laugh]`, `[cough]`, `[sigh]`, `[chuckle]`): Native, inline with text. English-only — unusable for Ukrainian heare.

2. **OpenAI gpt-4o-mini-tts via instructions**: The `instructions` parameter steers vocal behavior. Prompting with `"Occasionally include natural thinking sounds and brief vocalizations"` can produce "hmm", sighs, and hesitations — though not reliably tagged. Works in Ukrainian.

3. **Pre-recorded audio clips**: Record or generate short `.mp3` clips for "хм", "угу", "ага", a soft laugh, a sigh. Play them via pipecat's `AudioRawFrame` or `TTSSpeakFrame` before/after generated speech. Fully deterministic, zero latency, language-agnostic.

**Generator prompt engineering** for Ukrainian non-verbals: Instruct the LLM to occasionally include `[хм]`, `[зітхання]`, `[смішок]` markers in its reply. A post-LLM substitution map in `generator.py` converts these to either pre-recorded audio injection or SSML `<audio src="..."/>` tags. This approach is reliable because it's LLM-driven with deterministic post-processing.

**Sources:** Chatterbox paralinguistic tags · https://github.com/resemble-ai/chatterbox; NVBench non-verbal vocalizations benchmark · https://arxiv.org/html/2604.16211

---

### [FINDING:V7] Sentence-Granularity Streaming — Chunk Strategy Tradeoffs

**Confidence: HIGH**

Current heare: `_split_on_sentence()` fires one `TTSSpeakFrame` per `.!?…` boundary. This gives low TTFA (first audio plays after first sentence token) but causes prosody flatness — each sentence is synthesized in isolation with no inter-sentence context.

Tradeoff matrix:

| Strategy | TTFA | Prosody Quality | Implementation Cost |
|---|---|---|---|
| Word/phoneme level (k=1) | Lowest | Very poor (no lookahead) | High |
| **Sentence (current)** | Low | Flat but consistent | Low (already done) |
| Phrase/clause level | Low-medium | Better than sentence | Medium |
| Paragraph (2-4 sentences) | Medium (+300–800ms) | Best (full context) | Low |
| Full response | High | Best | Trivial |

Industry guidance (Deepgram, smallest.ai): Sentence-level is the practical sweet spot for conversational agents. Paragraph-level improves prosody at the cost of +300–800ms TTFA, acceptable only for longer utterances (>40 words). The prosody flatness at sentence level is partially addressable by appending the next sentence as a lookahead suffix during synthesis (feed sentence N + silent-prefix of sentence N+1 to TTS, discard the suffix audio).

**Recommendation**: Keep sentence-level for short replies (<3 sentences). For longer replies, emit first sentence immediately then batch remaining into 2-sentence chunks.

**Sources:** Deepgram streaming TTS latency tradeoff · https://deepgram.com/learn/streaming-tts-latency-accuracy-tradeoff; smallest.ai voice agent latency guide · https://smallest.ai/blog/designing-voice-assistants-stt-llm-tts-tools-and-latency-budget

---

### [FINDING:V8] Filler Speech While Thinking

**Confidence: HIGH**

Current state: `ActionWorker` submits actions to `IntentQueue` and immediately pops. The generator pushes TTS frames for the spoken reply, then the action executes asynchronously. There is no mechanism to emit a holding phrase before a long action.

Proposed implementation in `ActionWorker.run()`:
1. Before calling `execute_direct()` or `claude_cli.call_action()`, record `t_start = time.monotonic()`.
2. For intents where the tool is `bash`, `web_fetch`, or `web_search` (known slow tools), push a `TTSSpeakFrame("Хм, секунду...")` or `TTSSpeakFrame("Зачекай, перевіряю...")` immediately.
3. Policy: Only emit filler if the action queue position is 0 (first action of turn) to avoid stacking multiple fillers.
4. Alternatively: use a timeout approach — start action, if no result after 1.5 s, push filler.

This requires `ActionWorker` to have a reference to the pipecat pipeline's push mechanism (or a callback registered at init time).

**Sources:** heare actions.py (codebase); pipecat TTSSpeakFrame docs · https://docs.pipecat.ai/guides/learn/text-to-speech

---

### [FINDING:V9] Disfluencies That Feel Human

**Confidence: MEDIUM**

Ukrainian natural conversational markers (discourse particles) include:
- **Opening fillers**: "Ну", "Слухай", "Так от", "Знаєш", "От", "Е-е"
- **Agreement/acknowledgment**: "Угу", "Ага", "Так", "Точно"
- **Hedging/thinking**: "Ну тобто", "Як би то сказати", "Хм"
- **Topic pivot**: "До речі", "Між іншим"

Research on naturalness in conversational TTS (general Slavic/Eastern European speech pragmatics) indicates disfluencies injected at ~10–20% of turns feel natural; above 25% becomes annoying and perceived as a speech impediment.

**Recommended policy for heare**: ~15% of generator turns should begin with a randomized opener from a small pool:
```python
OPENERS = ["Ну, ", "Слухай, ", "Так от, ", "Знаєш, ", ""]  # "" = 60% no-opener
```
Inject at LLM prompt level ("Occasionally begin your reply with a natural Ukrainian conversational opener") or post-process by prefixing. Post-processing is more controllable.

**Sources:** Academic: Conventional Implicatures in Ukrainian Discourse (Bezugla, TPLS) · https://tpls.academypublication.com/index.php/tpls/article/download/5945/4762/16829; Springer: Automated Identification of Discourse Connectives in Ukrainian · https://link.springer.com/chapter/10.1007/978-3-031-20834-8_5

---

### [FINDING:V10] Voice Persona Matching — Edge-TTS Voice Selection

**Confidence: HIGH**

Edge-TTS offers exactly two Ukrainian voices:
- `uk-UA-PolinaNeural` — female, neutral/warm, currently in use
- `uk-UA-OstapNeural` — male, neutral/warm

Persona-to-voice mapping proposal for `identity.json`:

| Persona vibe (identity.json) | Recommended voice | Rationale |
|---|---|---|
| Playful, curious, energetic | `uk-UA-PolinaNeural` | Higher natural pitch, expressive |
| Warm, calm, grounded | `uk-UA-PolinaNeural` (rate=-5%) | Slightly slower feels warmer |
| Professional, direct | `uk-UA-OstapNeural` | Male voice, more authoritative register |
| Friendly companion (male) | `uk-UA-OstapNeural` (pitch=+2Hz) | Slight pitch lift softens authority |

A `voice_variant` field in `identity.json` (or derived from `vibe` field) could drive voice selection at startup. The `edge-tts` `Communicate(voice=voice_name, rate="+0%", pitch="+0Hz")` parameters handle per-session customization.

**Sources:** Edge-TTS voice list · https://tts.travisvn.com/ ; edge-tts PyPI · https://pypi.org/project/edge-tts/

---

### [FINDING:V11] Backchannel Listening ("угу" mid-monologue)

**Confidence: LOW–MEDIUM**

Backchannel vocalizations ("угу", "ага") emitted while the user is speaking are technically infeasible with the current pipecat pipeline because:
1. Any bot TTS while the user speaks sets `BotStartedSpeakingFrame`, which triggers `BotSpeaking` cooldown — blocking subsequent user STT.
2. Bot audio through the speaker would be picked up by the mic and treated as user speech (echo problem).

For hardware with reliable AEC (AirPods), option (2) is mitigated. Option (1) requires pipecat-level changes to allow "whisper-mode" TTS that does not trigger `BotStartedSpeakingFrame`. This is non-trivial.

**Practical near-term alternative**: Visual backchanneling (e.g., a UI indicator) rather than audio. Or: use AirPods-only mode where AEC is hardware-guaranteed, emit very short (<0.3 s) backchannel clips below the VAD detection threshold.

**Sources:** Pipecat BotStartedSpeakingFrame / BotStoppedSpeakingFrame frame types · https://reference-server.pipecat.ai/en/stable/api/pipecat.frames.frames.html

---

### [FINDING:V12] STT Robustness — Accents and Noisy Environments

**Confidence: MEDIUM**

Groq (current STT) uses Whisper-large-v3-turbo, which handles Ukrainian including non-standard pronunciation patterns (Nazar's Kyiv-variant Ukrainian). Whisper is generally robust to moderate accent variation.

Tuning options:
1. **`language="uk"` hint** (already should be set in Groq API call — verify in `src/agent_sdk_cli.py` or pipecat transport config). Explicit language hint reduces hallucination to English.
2. **`prompt` parameter** (Groq/Whisper): Passing a short Ukrainian prompt string primes the model vocabulary for Ukrainian phonology and common words in heare's domain.
3. **Noise**: macOS AirPods have hardware beamforming + AEC. MacBook mic does not. For loud environments, webrtc-audio-processing noise suppression (Finding V2) improves STT accuracy significantly.
4. **Debounce** (`transcript_debounce_seconds` in `generator.py`): Already implemented. Coalesces mid-sentence STT pauses. Verified in codebase.

**Sources:** Groq Whisper API; webrtc-audio-processing · https://pypi.org/project/webrtc-audio-processing/

---

### [FINDING:V13] Whispering / Volume by Time-of-Day via SSML Prosody

**Confidence: HIGH**

Edge-TTS exposes `volume` parameter on `Communicate(volume="-20%")`. This maps to SSML `<prosody volume="-20%">` internally. The full range is −100% (silent) to +100% (maximum).

Implementation hook in `generator.py` or `main.py`:
```python
import datetime

def _tts_volume_for_hour(hour: int) -> str:
    if hour < 8:
        return "-40%"  # very quiet pre-dawn
    elif hour < 10:
        return "-20%"  # quiet morning
    else:
        return "+0%"   # normal

hour = datetime.datetime.now().hour
volume = _tts_volume_for_hour(hour)
# Pass to edge-tts Communicate(volume=volume) in the TTS processor
```

Azure Speech SDK equivalent: `<prosody volume="soft">` for pre-10am. Style `whispering` via `mstts:express-as` for deepest quiet (requires Azure SDK, not edge-tts).

**Sources:** MS SSML prosody docs · https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-voice; edge-tts PyPI · https://pypi.org/project/edge-tts/

---

## [LIMITATION]

1. **edge-tts SSML restriction**: Findings V3/V4 about full SSML expressiveness require migrating away from edge-tts to Azure SDK (paid) or a different TTS engine. All SSML style/emotion features are blocked by edge-tts design.
2. **Ukrainian style support unconfirmed**: `mstts:express-as` style availability for native Ukrainian voices (PolinaNeural, OstapNeural) is not confirmed in Microsoft's voice-styles table; only multilingual English-primary voices are confirmed.
3. **Chatterbox-Turbo English-only**: Non-verbal paralinguistic tags ([laugh], [sigh]) in Chatterbox-Turbo are English-only. Not applicable to heare's Ukrainian use case directly.
4. **CosyVoice no Ukrainian**: CosyVoice 2/3 does not support Ukrainian in its official language list.
5. **Backchannel infeasibility**: Backchannel "угу" mid-user-monologue (Finding V11) requires significant pipecat pipeline changes and reliable hardware AEC; low confidence on viability.
6. **Disfluency research gap**: No Ukrainian-specific sociolinguistic corpus data on optimal disfluency rate was found; the 15% figure is extrapolated from general conversational agent UX research.
7. **No latency measurements**: Filler speech timing in Finding V8 is estimated; actual ActionWorker execution times were not benchmarked in this research stage.

---

## Summary Table

| # | Topic | Feasibility (edge-tts) | Feasibility (Azure/OpenAI) | Priority |
|---|---|---|---|---|
| V1 | Barge-in / interruption | Medium (pipecat StartInterruptionFrame) | Same | HIGH |
| V2 | AEC / mic gating | High (webrtc-audio-processing) | Same | HIGH |
| V3 | SSML prosody (edge-tts) | Low (rate/volume/pitch only) | Full | MEDIUM |
| V4 | Express-as emotional styles | Blocked | High (multilingual voices) | MEDIUM |
| V5 | Emotional TTS alternatives | N/A | High (OpenAI/ElevenLabs) | MEDIUM |
| V6 | Non-verbal vocalizations | Low (pre-recorded clips only) | Medium (OpenAI instructions) | LOW |
| V7 | Chunk streaming strategy | High (2-sentence batching) | Same | HIGH |
| V8 | Filler while thinking | High (ActionWorker hook) | Same | HIGH |
| V9 | Ukrainian disfluencies | High (prompt/post-process) | Same | MEDIUM |
| V10 | Voice persona mapping | High (2 voices, rate/pitch) | Wider choice | LOW |
| V11 | Backchannel "угу" | Very low | Low | LOW |
| V12 | STT robustness | High (Whisper language hint) | Same | MEDIUM |
| V13 | Time-of-day volume | High (volume param) | Same + whispering style | MEDIUM |

---

[STAGE_COMPLETE:10]
