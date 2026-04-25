# Stage 12 — Prior Art Survey: Voice Assistants & Wearable AI Companions

**Research date**: 2026-04-23
**Scope**: Open-source voice stacks, wearable AI products, design lessons for heare (Ukrainian, macOS daemon, Pipecat-based, Claude-powered)
**Researcher**: document-specialist agent

---

## Findings

### [FINDING:R1] Mycroft AI / OVOS — Skills-first architecture works; business model and hardware killed the project

**Core concept**: Mycroft was a fully open-source, privacy-respecting voice assistant with a Raspberry Pi device (Mark I/II), skills framework, and intent-parsing pipeline built around two parsers: Adapt (keyword/regex) and Padatious (neural example-based). After Mycroft's 2023 shutdown from patent litigation and cash starvation, the community forked into **OpenVoiceOS (OVOS)** and **Neon AI**, both still active.

**What worked**:
- Modular skill architecture: skills declare intent patterns; the core dispatches. This separation survived Mycroft's death.
- Two-tier intent pipeline — fast keyword matching (Adapt) with neural fallback (Padatious) — is still state-of-the-art for open systems.
- OVOS's 2025 two-stage VAD: VAD fires first; wake-word engine only activates after speech detected — dramatically reduces false activations and CPU on Raspberry Pi.
- OVOS Pre-Wake-VAD (Nov 2025): silence filter before wake-word model — zero cost during actual silence.

**What failed**:
- Mycroft required cloud (mycroft.ai servers) for skill sync and STT by default — servers went dark Feb 2024.
- Hardware crowdfunding failed: manufacturing cost overruns, patent lawsuit ($6M liability vs ~$3M runway).
- Intent system was brittle for natural phrasing outside training examples.
- No LLM fallback — anything outside a registered skill returned "I don't know."

**FOSS**: Yes (Apache 2.0). **Offline**: Fully, with local Whisper STT + Piper TTS.

**Ideas for heare**:
1. Adopt the two-stage listening model (VAD → wake-word → STT) even in daemon mode to avoid burning CPU.
2. Do not depend on any cloud server you don't control; design for server-independence from day one.

[CONFIDENCE: High]
[EVIDENCE] https://openvoiceos.github.io/ovos-technical-manual/312-wake_word_plugins/ | https://blog.openvoiceos.org/posts/2025-11-06-prewake-vad | https://news.ycombinator.com/item?id=34772848

---

### [FINDING:R2] Rhasspy — Offline-first with modular Wyoming protocol; the reference for privacy-complete stacks

**Core concept**: Rhasspy is a fully offline, MQTT/Wyoming-based voice assistant targeting home automation. v2.5 used Hermes protocol (Snips-derived); v3 moved to Wyoming protocol (stdin/stdout adapters).

**What worked**:
- Sub-800ms end-to-end latency on a Raspberry Pi 5 in 2024 real-world tests: wake word → spoken reply.
- Wyoming protocol (stdin/stdout) is the lowest-friction adapter interface ever shipped for voice services — any program can speak it.
- Modular plugin swap: STT, TTS, wake-word, intent are independently replaceable.
- Supports 25+ human languages via community configs; Ukrainian has partial coverage via Vosk.
- Zero cloud dependency; all data stays on device.

**What failed**:
- Intent matching requires hand-crafted grammar sentences — no free-form NLU.
- No LLM integration in core (community adds it externally).
- Limited TTS voice quality; Piper improved this significantly in 2023-2024.

**FOSS**: Yes (MIT). **Offline**: Full.

**Ideas for heare**:
1. Wyoming protocol as the inter-service wire format if heare ever externalizes STT or TTS services.
2. Piper TTS (local, fast, decent quality) is viable for Ukrainian if a Ukrainian Piper voice model is available.

[CONFIDENCE: High]
[EVIDENCE] https://rhasspy.readthedocs.io/ | https://github.com/rhasspy/rhasspy3 | https://lwn.net/Articles/926484/

---

### [FINDING:R3] Willow (HeyWillow) — ESP32-S3 satellite model: split processing between edge and server

**Core concept**: Willow runs on a $50 ESP32-S3-BOX-3 device (dual MEMS mics, DSP chip, 2" touchscreen). The device handles audio frontend (AGC, AEC, noise suppression, BSS) and wake-word detection locally via Espressif ESP-SR; speech audio is streamed to Willow Inference Server (WIS) for ASR.

**What worked**:
- <500ms from end of speech to action completed — competitive with Alexa/Google Home.
- <1% command failure rate in thousands of test cycles.
- 25-foot recognition range in challenging acoustic environments.
- On-device wake-word via MultiNet (Espressif's model) means zero cloud dependency for activation.
- Audio frontend: blind source separation (BSS) + echo cancellation means voice quality rivals much more expensive hardware.

**What failed**:
- Tied to ESP32-S3-BOX-3 hardware; the project explicitly disclaims support for other hardware.
- Server (WIS) must be self-hosted — non-trivial for consumers.
- Inference server requires GPU or beefy CPU for fast Whisper inference.

**FOSS**: Yes. **Offline**: Wake-word yes; ASR requires WIS server.

**Ideas for heare**:
1. The satellite model (thin edge mic device + server) is the right architecture for ambient room coverage.
2. Two-stage DSP frontend (hardware BSS/AEC → software ASR) is worth reproducing even in software via WebRTC AEC.

[CONFIDENCE: High]
[EVIDENCE] https://heywillow.io/how-willow-works/ | https://github.com/HeyWillow/willow/wiki/Hardware | https://news.ycombinator.com/item?id=35948462

---

### [FINDING:R4] Humane Ai Pin — The canonical example of every failure mode compressed into one product

**Core concept**: $699 + $24/month magnetic wearable with laser projector, camera, and cloud-only AI assistant. Launched April 2024; acquired by HP for parts Dec 2024.

**What failed** (comprehensive):
- **Battery**: 2-4 hours actual runtime vs. all-day promise. Insufficient for wearable use case.
- **Latency**: Multi-second response delays for every query due to cloud-only processing.
- **Hallucination**: AI responses "sometimes inaccurate, irrelevant, or simply not helpful enough."
- **Display**: Laser projector unreadable in ambient light; finger-tilt navigation produced distorted visuals.
- **Price**: $699 device + $24/month subscription vs. Apple Watch at similar price with 10x features.
- **Missing basics**: No alarms, no timers at launch.
- **No ecosystem**: No app store, no third-party integrations; isolated product.
- **Oversell**: Demoed capabilities that shipped broken; public trust destroyed on day-one reviews.
- **Sales**: ~10,000 units shipped vs. 100,000-unit target.

**The core failure pattern**: Mistook hype for product-market fit. Attempted to replace smartphones rather than augment them.

**Ideas for heare**:
1. Never ship features that aren't working. Demo-reality gap is fatal in AI hardware.
2. Complement the phone; don't fight it. heare lives alongside macOS — lean into that.
3. Battery/offline capability is a trust primitive; if the device fails when the network fails, users abandon it.

[CONFIDENCE: Very High]
[EVIDENCE] https://www.unite.ai/what-went-wrong-with-the-humane-ai-pin/ | https://failure.museum/humane-ai-pin/ | https://www.techsponential.com/reports/humanereviews

---

### [FINDING:R5] Rabbit R1 — Large Action Model hype, 95% user abandonment in 5 months

**Core concept**: $199 AI device launched at CES 2024 on promise of a "Large Action Model" that could autonomously operate apps (order Uber, book restaurants). Shipped March 2024; 95% of buyers abandoned it within 5 months.

**What failed**:
- CES demo showed capabilities that didn't work on shipped hardware.
- 100,000 pre-orders; only ~5,000 active users by August 2024.
- LAM could not reliably control apps; most integrations were buggy or broken.
- Camera-based object identification inaccurate at launch.
- Core value proposition (replacing phone apps with AI) was wrong: only 23% of adults comfortable with voice-primary UI; only 13% prefer it.
- Device required network for almost everything; offline = brick.

**What partially worked later**:
- Rabbit OS updates slowly improved basic assistant functions; Android Police noted in 2025 that core voice assistance became reliable — after the brand was already destroyed.

**Ideas for heare**:
1. Voice-first does not mean voice-only; heare's macOS daemon context means keyboard/screen is always available as fallback.
2. Tool use must be demonstrated working before shipping; agentic claims require agentic proof.

[CONFIDENCE: Very High]
[EVIDENCE] https://www.laptopmag.com/ai/rabbit-r1-2024-ai-year-in-review | https://www.theshortcut.com/p/rabbit-r1-review-ai | https://medium.com/@thcookieh/why-did-the-rabbit-r1-and-humane-ai-pin-fail-at-launch-c108d6e2bebb

---

### [FINDING:R6] Friend pendant / Tab (Avi Schiffmann) — Emotional companion vs. productivity tool is a fundamental design fork

**Core concept**: Tab → Friend is a $129 pendant (MEMS mic, BLE to iOS) that passively records ambient audio and provides text-based AI commentary via Claude 3.5. Designed as emotional companion ("AI friend"), not productivity tool.

**What worked**:
- 15-hour battery; all-day wearability achieved.
- Always-on audio capture works technically (BLE + VAD on phone).
- Low price point ($129) removes hardware barrier.

**What failed**:
- WIRED two-week review: "bullies its own users with snarky commentary."
- Privacy backlash: records third parties without their consent.
- Psychological dependency concerns raised by The Guardian, WIRED.
- CNN 2025: device became "symbol for the backlash against AI."
- Memory model: no persistence beyond context window by default; memories deletable but not searchable.
- No audio stored — privacy-safe but loses retrievability.

**Design fork lesson**: Companion AI (emotional support) vs. ambient assistant (productivity/memory) are fundamentally different product bets with different failure modes.

**Ideas for heare**:
1. Proactive commentary without user request is high-risk (Friend's "snarky" problem). Prompt only when context clearly warrants it, or on explicit request.
2. Third-party consent UX is mandatory for ambient capture. A visible indicator (LED, sound cue) is the minimum viable privacy signal.

[CONFIDENCE: High]
[EVIDENCE] https://www.webpronews.com/friend-ai-necklace-sparks-backlash-for-privacy-woes-and-tech-flaws/ | https://www.cnn.com/2025/11/16/tech/friend-ai-device-backlash-ceo-avi-schiffmann | https://en.wikipedia.org/wiki/Friend_(product)

---

### [FINDING:R7] Limitless / Rewind — Memory-first design with privacy as architecture, not policy

**Core concept**: Rewind (macOS, screen + audio capture → local Whisper + OCR → LanceDB) was acquired/evolved into Limitless ($199 pendant). Meta acquired Limitless Dec 2025. Core value: searchable life-log.

**Rewind architecture** (privacy-first, verified by EFF Sept 2024 audit):
- All processing on Mac Neural Engine via INT8 quantization — zero cloud unless opt-in.
- Whisper-small for STT; EasyOCR for screen content; CLIP-like embeddings; LanceDB local vector store.
- Cosine similarity retrieval at 0.8 threshold; local Llama-3.1 8B for summaries.
- "Zero privacy leaks in simulated attack scenarios" per EFF audit.

**Limitless pendant**:
- 8+ hours battery; 103 language support; clips to clothing.
- Initially $50/month subscription (controversial); waived post-Meta acquisition.
- 4.7/5 average on Product Hunt; delivery delays hurt early reputation.

**What worked**: Frictionless capture; "game-changing" for professionals per Forbes reviews.
**What failed**: Pre-orders delayed over a year; subscription model surprised users; occasional connectivity drops.

**Ideas for heare**:
1. Local-first memory (SQLite + vector store on Mac) is the trust baseline — not a nice-to-have.
2. "Speculative retrieval" pattern: fire vector DB queries while user is still speaking to hit 160ms memory-augmented response budget.
3. EFF audit methodology is worth mimicking: build a privacy threat model and test against it before shipping.

[CONFIDENCE: High]
[EVIDENCE] https://www.limitless.ai/ | https://getcoai.com/news/limitless-ais-499-pendant-promises-to-be-your-always-on-memory-assistant/ | https://merlio.app/blog/limitless-ai-guide

---

### [FINDING:R8] Bee (Amazon) — Ambient agent that survived because it focused on utility patterns

**Core concept**: $49.99 clip-on wearable with beamforming mics, 7-day battery, mute button. Captured ambient audio → daily summaries, commitment tracking, email/calendar actions. Amazon acquired it mid-2025.

**What worked**:
- 7-day battery is the single biggest differentiator in wearable AI. Removes charging anxiety entirely.
- Beamforming mics → ambient pickup without dedicated microphone orientation.
- "Daily Insights" surfacing behavioral patterns across weeks was praised as genuinely useful.
- Smart Actions (email draft, calendar invite from conversation mention) worked reliably at launch.
- TechCrunch hands-on (Jan 2026): "nuanced, well-thought-out device."

**What failed**: Limited to iOS initially; Android support added later.

**Ideas for heare**:
1. The 7-day battery on a $50 device proves ambient capture hardware is now cheap. heare could support a Bluetooth satellite mic (Bee-like) for room-scale capture.
2. "Commitment tracking" from conversation — detecting action items and promises — is a high-value ambient feature with low annoyance potential.

[CONFIDENCE: High]
[EVIDENCE] https://bee.computer | https://techcrunch.com/2026/01/12/hands-on-with-bee-amazons-latest-ai-wearable/ | https://www.latent.space/p/bee

---

### [FINDING:R9] Plaud NotePin — Passive voice recorder as wearable; 20-hour battery, 112 languages

**Core concept**: $149 wearable (necklace/clip/pin) with 20-hour continuous recording, 64GB local storage, 40-day standby, Apple Find My. Audio uploaded → GPT-4 Turbo transcription + meeting minutes + to-do extraction.

**What worked**:
- 20-hour battery exceeds all wearable AI competitors.
- 64GB onboard storage means capture continues offline; sync happens later.
- 112-language transcription including Ukrainian.
- Speaker diarization (speaker labeling) built-in.
- AI output: meeting minutes, to-do lists, grievances, action points.
- Free tier: 300 transcription minutes/month; sustainable pricing model.

**What failed**:
- Cloud dependency for transcription (GPT-4 Turbo via API).
- No real-time response; purely async/passive.
- Privacy: audio transmitted to cloud for processing.

**Ideas for heare**:
1. 300-minute free tier model is instructive — gives users meaningful value before paywall. heare could gate advanced memory features similarly.
2. Speaker diarization from ambient capture is a solved problem at $149 hardware; heare should treat it as table stakes for multi-person environments.

[CONFIDENCE: High]
[EVIDENCE] https://www.techradar.com/ai-platforms-assistants/claude/plaud-notepin-bundle-review | https://the-gadgeteer.com/2025/04/09/plaud-notepin-review-ai-wearable-note-taker/ | https://www.plaud.ai/

---

### [FINDING:R10] Moshi (Kyutai) — First open-source full-duplex voice LLM, 160-200ms latency

**Core concept**: Open-source speech-text foundation model running full-duplex dialogue. No ASR→LLM→TTS pipeline — single model generates parallel speech streams for both speaker and listener simultaneously.

**Architecture**:
- **Mimi codec**: 12.5Hz, 24kHz audio → 1.1kbps, 80ms frame size, fully streaming.
- **Temporal Transformer**: conversation over time.
- **Depth Transformer**: audio codec token layers.
- **Inner Monologue**: generates time-aligned text tokens as prefix to audio tokens — improves linguistic quality while providing streaming transcription.
- **Training**: 4-phase (unsupervised pre-train → diarization-based post-train → Fisher dataset fine-tune → instruction fine-tune on synthetic scripts).

**Latency**: Theoretical 160ms (80ms Mimi frame + 80ms acoustic delay). Practical: 200ms on L4 GPU.

**Full-duplex**: Models both sides of conversation as parallel streams. Handles interruptions, backchannels ("mm-hmm"), overlapping speech — things impossible in turn-based systems.

**What it doesn't do well**: Knowledge cut-off at training time; no tool use; purely conversational.

**FOSS**: Yes (CC BY 4.0 model weights). **Offline**: Requires GPU server.

**Ideas for heare**:
1. Inner Monologue pattern (text prefix before audio tokens) is adaptable to Pipecat pipelines via streaming LLM + TTS with token-level interleaving.
2. 200ms theoretical floor for full-duplex: budget planning should target ≤400ms for cloud-augmented S2S pipeline.

[CONFIDENCE: Very High]
[EVIDENCE] https://arxiv.org/abs/2410.00037 | https://github.com/kyutai-labs/moshi | https://kyutai.org/Moshi.pdf

---

### [FINDING:R11] OpenAI Realtime API — Server-managed interruption via VAD + truncation event

**Core concept**: WebSocket/WebRTC API with native VAD, automatic turn detection, and response cancellation. Enables sub-500ms voice-to-voice for GPT-4o.

**Interruption mechanism** (technical):
1. `input_audio_buffer.speech_started` fires when VAD detects user speech mid-response.
2. Client stops playback immediately.
3. Client sends `conversation.item.truncate` with `audio_end_ms` timestamp.
4. Server discards unplayed audio and text transcript from that point.
5. New response generation begins.

**VAD parameters**: Activation threshold (default 0.5), prefix padding (default 300ms), silence duration (default 500ms).

**Known VAD limitations**:
- Struggles with background noise.
- False triggers on affirmative backchannels ("uh-huh").
- No contextual awareness ("let me think for a moment" = false interruption detection).

**2025 Realtime mini**: +18.6pp instruction-following accuracy, +12.9pp tool-calling accuracy vs. previous snapshot.

**Ideas for heare** (Pipecat-based):
1. Mirror this truncation pattern in Pipecat: on `user_started_speaking` frame, immediately cancel TTS playback and LLM streaming, then re-run from new transcript.
2. Tune VAD silence_duration upward (700-800ms) for Ukrainian speakers who use longer inter-word pauses.

[CONFIDENCE: Very High]
[EVIDENCE] https://developers.openai.com/api/docs/guides/realtime-conversations | https://medium.com/@alozie_igbokwe/building-an-ai-caller-with-openai-realtime-api-part-5-how-openai-handles-interruptions-9050a453d28e

---

### [FINDING:R12] Gemini Live — Native audio path with tonal/emotional awareness

**Core concept**: Google's end-to-end voice model where the same model listens, plans, and speaks. No STT→LLM→TTS chain — audio in, audio out with affect control.

**Design patterns**:
- Barge-in via VAD: cancels response instantly on user speech detection.
- Tonal understanding: detects frustration, confusion, pace shifts and adjusts response style.
- Automatic de-escalation on stressed support calls (demonstrated in Google demos).
- Gemini 3.1 Flash Live (March 2026): improved tonal nuance vs. 2.5 Flash Native Audio.
- Screen-sharing aware: multimodal context (voice + what user is looking at).

**What it enables for heare**:
- Emotion detection from prosody without separate sentiment model — already in Gemini Live SDK.
- Adaptive response length: shorter, direct answers when frustration detected.

**Ideas for heare**:
1. Even without native audio model, heare can approximate emotional adaptation by prompting Claude with speaking-rate and pitch-shift cues extracted from Whisper word timestamps.
2. Vertex AI Gemini Live API is a viable backend alternative to OpenAI Realtime for future S2S path.

[CONFIDENCE: High]
[EVIDENCE] https://cloud.google.com/blog/products/ai-machine-learning/gemini-live-api-available-on-vertex-ai | https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-1-flash-live/

---

### [FINDING:R13] Ultravox — Direct audio-to-LLM without ASR stage eliminates transcription latency

**Core concept**: Open-source multimodal LLM (fixie-ai) that replaces the ASR stage by projecting audio directly into the LLM's embedding space via a multimodal projector.

**Architecture**: Whisper/WavLM audio encoder → modality adapter → Llama/Mistral/Gemma backbone. Eliminates ASR-to-text round trip.

**v0.5 (Feb 2025)**:
- 60% improvement in transcription accuracy.
- 18% improvement in speech-based web QA.
- 24% improvement in X→English translation.
- Language support expanded from 15 to 42 languages (Ukrainian included in v0.5 range).

**Trade-off**: Currently emits streaming text (not audio); still needs TTS on output side. Full speech token emission ("unit vocoder" path) planned but not shipped as of research date.

**Ideas for heare**:
1. Ultravox as STT replacement in the Pipecat pipeline: no ASR stage → LLM gets raw audio context including prosody → better intent detection for ambiguous phrases.
2. When Ultravox ships speech-token output, it becomes a Moshi-class local model viable on Mac hardware.

[CONFIDENCE: High]
[EVIDENCE] https://github.com/fixie-ai/ultravox | https://www.ultravox.ai/blog | https://simonwillison.net/2024/Jun/10/ultravox/

---

### [FINDING:R14] Pipecat — The reference framework; VAD+SmartTurn is the interruption best practice

**Core concept**: Open-source Python framework (Daily.co) for building real-time voice+multimodal agents. Pipeline of processor frames: audio in → VAD → STT → LLM → TTS → audio out.

**Key design patterns**:
- **SmartTurn**: Silero VAD + SmartTurn detector emit `UserStartedSpeaking` / `UserStoppedSpeaking` frames; bot yields on `UserStartedSpeaking`.
- **S2S mode**: audio → audio in <500ms when backed by OpenAI Realtime or Gemini Live; bypasses STT+TTS individually.
- **Speculative text events** (2025): removed the "interruption re-push hack"; cleaner context management.
- **Subagents**: distributed multi-agent via shared message bus; conversation handoff between specialists.
- Krisp VIVA VAD analyzer (2025): noise-robust VAD alternative to Silero.

**Latency benchmark** (Modal blog): 1-second voice-to-voice with open models (Whisper + Llama + Piper) on Modal cloud.

**STT aggregation delay caveat**: When STT delivers final transcripts outside VAD timeout window, Pipecat adds aggregation delay — pads total latency substantially. Use streaming STT (Deepgram Nova, AssemblyAI) to avoid.

**Ideas for heare**:
1. heare is Pipecat-based already — adopt SmartTurn (not raw Silero VAD alone) for lower false-interruption rate.
2. Use streaming STT (Deepgram with Ukrainian model) rather than batch Whisper to avoid STT aggregation delay.

[CONFIDENCE: Very High]
[EVIDENCE] https://github.com/pipecat-ai/pipecat | https://docs.pipecat.ai/getting-started/introduction | https://modal.com/blog/low-latency-voice-bot

---

### [FINDING:R15] Latency Budget — 300-500ms is the human conversational threshold; 800ms is production reality

**Published numbers** (2024-2025 research):

| Threshold | Human perception |
|---|---|
| <300ms | Indistinguishable from human response |
| 300-500ms | Slight but acceptable delay |
| 500-800ms | Noticeable; users adapt |
| 800-1000ms | "System problem" perception starts |
| >1000ms | 40% higher hang-up rate in call centers |
| >1000ms | Stanford HCI: satisfaction "plummets" |

**Production targets**: Retell AI, Synthflow, Twilio all target ≤800ms E2E.
**Moshi**: 200ms practical on L4 GPU (full-duplex, no tool use).
**OpenAI Realtime**: ~400-600ms typical for GPT-4o.
**Willow**: <500ms wake-to-action on ESP32+WIS server.

**Budget for heare** (Pipecat + Claude + macOS):
- VAD detection: ~50ms (Silero)
- STT streaming first word: ~150ms (Deepgram)
- LLM first token: ~300ms (Claude Haiku) / ~600ms (Sonnet)
- TTS first audio chunk: ~100ms (Cartesia/Piper)
- Total target: ≤600ms for casual queries; ≤1200ms for tool-use queries

**Ideas for heare**:
1. Use Claude Haiku for sub-300ms intent classification; escalate to Sonnet/Opus only when needed.
2. Stream TTS before LLM response is complete — don't wait for full response.

[CONFIDENCE: Very High]
[EVIDENCE] https://www.assemblyai.com/blog/low-latency-voice-ai | https://smallest.ai/blog/designing-voice-assistants-stt-llm-tts-tools-and-latency-budget | https://www.gnani.ai/resources/blogs/latency-targets-for-feels-human-voice-budgets-measures-enforcement

---

### [FINDING:R16] Ukrainian / Multilingual Support — Viable path exists via Whisper fine-tunes and Deepgram

**State of the art (2025-2026)**:

- **Whisper base/large-v3**: Ukrainian in training data (680K hours multilingual). Accuracy varies — accent sensitivity is known weakness.
- **Whisper fine-tuned for Ukrainian**: `egorsmkv/whisper-ukrainian` project provides trainer + evaluation scripts specifically for Ukrainian; significantly outperforms base Whisper on Ukrainian audio.
- **Deepgram**: Released dedicated Ukrainian STT model; production-grade streaming API.
- **ElevenLabs**: Ukrainian TTS voices available.
- **Azure Speech**: Ukrainian STT + TTS in production.
- **Ultravox v0.5**: 42-language support includes Ukrainian.
- **OpenAI Whisper v3 (2025 update)**: improved Ukrainian among 99 language updates per dev.ua report.

**Key gap**: Ukrainian TTS quality is weaker than English across all providers. ElevenLabs has the best Ukrainian TTS but it is cloud-only and costly.

**Ideas for heare**:
1. Use Deepgram Ukrainian model for streaming STT — production-ready, low-latency.
2. For TTS, consider Ukrainian Piper voice model if offline/privacy mode is required; ElevenLabs if quality is paramount.
3. Fine-tuned Whisper via `egorsmkv/whisper-ukrainian` as local fallback when network is unavailable.

[CONFIDENCE: High]
[EVIDENCE] https://github.com/egorsmkv/speech-recognition-uk | https://deepgram.com/learn/speech-to-text-model-ukrainian | https://dev.ua/en/news/openai-improves-ai-for-voice-recognition-and-generation-model-supports-ukrainian-language | https://elevenlabs.io/text-to-speech/ukrainian

---

### [FINDING:R17] Memory Architecture — Episodic retrieval with speculative prefetch is the winning pattern

**Design space**:

| Approach | Latency | Privacy | Capacity |
|---|---|---|---|
| Context window only | 0ms overhead | High | Limited (100K-200K tokens) |
| Local vector DB (LanceDB/Chroma) | 50-300ms | Very High | Unlimited |
| Cloud RAG | 100-500ms + network | Low | Unlimited |
| Speculative prefetch | ~0ms (overlapped) | High | Unlimited |

**Rewind's validated architecture**: Local Whisper → LanceDB → Llama-3.1 8B → zero cloud. EFF-audited Sept 2024: zero privacy leaks.

**Speculative retrieval pattern**: Fire vector DB queries while user is still speaking (during VAD "user speaking" window), not after they stop. By the time VAD fires `UserStoppedSpeaking`, retrieval is already complete.

**Charlie Mnemonic (GoodAI)**: Three-tier memory — Short-Term (conversation), Long-Term (vector store), Episodic (timestamped events). This is the recommended pattern for personal AI agents.

**Ideas for heare**:
1. Implement speculative retrieval in the Pipecat pipeline: when `UserStartedSpeaking` fires, begin streaming partial transcript to vector DB for prefetch.
2. Use SQLite (structured facts) + local vector DB (semantic search) + conversation window (recency) as three-tier memory.

[CONFIDENCE: High]
[EVIDENCE] https://www.goodai.com/introducing-charlie-mnemonic/ | https://arxiv.org/html/2512.12686v1 | https://ucstrategies.com/news/rewind-ai-mac-memory-search-tool-specs-privacy-pricing-2026/

---

### [FINDING:R18] Proactivity — Only safety-critical scenarios show clear benefit; all others show mixed/negative results

**Research finding** (ScienceDirect systematic review, 2024): Proactive voice assistant behavior was studied in domestic and in-vehicle contexts. Only safety-critical and emergency situations demonstrated clear benefits. All other proactivity scenarios showed mixed or negative user outcomes.

**User feedback patterns (Reddit/HA community 2024)**:
- Initial excitement → annoyance after repeated misinterpretations during important tasks.
- "I wanted efficiency; instead, I got confusion."
- "I love my Echo Dot until I remember it's always listening."
- Friend pendant backlash: "snarky commentary" = unsolicited proactive opinion.

**What proactivity works**:
- Safety alerts (smoke, unusual activity).
- Time-critical reminders user explicitly set.
- Quiet notification (vibration/LED) rather than voice interruption.
- "Ambient awareness" mode: AI listens but only speaks when directly addressed.

**Ideas for heare**:
1. Default to reactive mode; proactive speech only for reminders the user explicitly set.
2. Non-voice signals (macOS notification, subtle sound cue) for ambient intelligence — do not interrupt user's train of thought.
3. If proactivity is desired, require explicit opt-in per category (e.g., "alert me about calendar conflicts").

[CONFIDENCE: High]
[EVIDENCE] https://www.sciencedirect.com/science/article/pii/S2451958824000447 | https://www.oreateai.com/blog/exploring-voice-ai-insights-and-reviews-from-reddit-users/

---

### [FINDING:R19] Leon (open-source Node.js assistant) — Context + memory + tool execution without LLM dependence

**Core concept**: Leon is a Node.js personal assistant (Python modules) with privacy focus, local model support, and hybrid mode routing: smart mode (LLM chooses) / workflow mode (fixed path) / agent mode (step-by-step planning).

**What's notable**:
- Hybrid NLP: balances LLM, simple classification, and multiple NLP techniques for speed/accuracy trade-off.
- Local model support — does not force cloud APIs.
- Recent (2024): new TTS and ASR engines; tools-first architecture rather than free-form chat.
- Context and memory as first-class citizens in design.
- Three execution modes (smart/workflow/agent) allow graceful degradation.

**FOSS**: Yes (MIT). **Offline**: Partial (local models).

**Ideas for heare**:
1. The three-mode execution model (deterministic → hybrid → agentic) is worth adopting: use deterministic paths for known intents, LLM only for ambiguous ones.
2. Tools-first (discrete capabilities) vs. free-form chat is the better design for reliability.

[CONFIDENCE: Medium]
[EVIDENCE] https://getleon.ai/ | https://github.com/leon-ai/leon | https://docs.getleon.ai/

---

### [FINDING:R20] Wake-Word vs. Always-On vs. Button — Field evidence

**Field evidence**:

| Activation Method | Products | Pros | Cons |
|---|---|---|---|
| Wake word (cloud) | Alexa, Google Home | Hands-free | Privacy risk; false activations |
| Wake word (local) | Willow (MultiNet), OVOS (Precise/openWakeWord) | Privacy-safe | Requires training; false activations |
| Always-on passive | Friend, Limitless, Plaud NotePin | Zero friction | Third-party consent; battery; creepiness |
| Button/PTT | Willow (long-press), Bee (button) | Zero false activations | Requires physical action |
| Hybrid (VAD+wake) | OVOS Pre-Wake-VAD | CPU efficient | Slight latency vs. pure wake-word |

**Key insight from Home Assistant community (2024)**: Microphone range is the #1 practical bottleneck. Most setups work well at <3 feet; performance degrades sharply at 3+ meters with TV background noise. Hardware DSP (Willow) solves this; software cannot fully compensate.

**For heare (macOS daemon)**:
- Mac has good single-mic but no hardware DSP/beamforming.
- Wake word via openWakeWord (local, low CPU) + Pre-Wake-VAD is the recommended default.
- Button (keyboard shortcut) as fallback for noisy environments.
- For ambient room capture, external USB mic with hardware echo cancellation is worth recommending in docs.

[CONFIDENCE: High]
[EVIDENCE] https://picovoice.ai/blog/complete-guide-to-wake-word/ | https://blog.openvoiceos.org/posts/2025-11-06-prewake-vad | https://community.home-assistant.io/t/the-current-state-of-voice-july-2024/746829

---

### [FINDING:R21] LLMVM — Code-interleaved execution is more reliable than JSON tool-calling for complex actions

**Core concept**: LLMVM is a CLI Python runtime where LLM output interleaves natural language and executable Python code — "continuation passing style" as of July 2024 refactor.

**Why this matters**:
- Standard JSON tool-call APIs require the LLM to get schema exactly right → brittle for complex multi-step tasks.
- LLMVM lets LLM express tool use as Python code → error caught at runtime → LLM debugs with locals() exposed.
- Playwright automation (headless Chromium) available as a native tool.
- Supports Claude 4, GPT-4o, Gemini, DeepSeek, Nova.

**Ideas for heare**:
1. For complex tool-use chains (calendar + email + web search), consider Python execution sandbox rather than JSON tool calls — more reliable multi-step execution.
2. Expose `locals()` on tool failure to LLM for self-correction — the LLMVM pattern.

[CONFIDENCE: Medium]
[EVIDENCE] https://github.com/9600dev/llmvm

---

### [FINDING:R22] Granola / Passive Meeting AI — Privacy defaults are a product crisis waiting to happen

**Core concept**: Granola is a passive meeting notes AI (macOS, screen audio capture). Achieved SOC 2 Type 2 Dec 2024.

**Critical finding**: Every Granola note is shareable via public link by default. Users are opted into AI training by default. Required manual opt-out buried in settings. This became a 2026 enterprise crisis.

**Pattern to avoid**: Privacy-unsafe defaults + opt-out buried in settings = user trust destruction when discovered.

**Ideas for heare**:
1. Privacy-safe defaults: no audio transmitted, no training on user data, no shareable links — all opt-in.
2. Consent surface must be visible at first launch, not buried in settings.

[CONFIDENCE: High]
[EVIDENCE] https://www.techbuzz.ai/articles/granola-s-private-ai-notes-are-public-by-default | https://www.granola.ai/security

---

### [FINDING:R23] Home Assistant "Year of the Voice" — Wyoming protocol + Piper + local Whisper is the reference local stack

**Core concept**: HA spent 2023 building a fully local, 50-language voice stack. Key output: Wyoming protocol (inter-service wire format), Piper TTS (fast, local), local Whisper STT.

**2024 lessons from HA community**:
- Hardware DSP is non-negotiable for >3-meter range.
- Sub-800ms achievable locally on Raspberry Pi 5 with Piper TTS + Whisper.small.
- Wyoming protocol lowered third-party integration barrier dramatically.
- Voice Chapter 6 (Feb 2024): on-device wake word on ESP32-S3 added — completes fully local pipeline.

**Ideas for heare**:
1. Wyoming protocol as the local service wire format keeps heare's internals swappable.
2. Piper TTS Ukrainian voice models: check if community has trained Ukrainian Piper voices (HA community actively trains new language models).

[CONFIDENCE: High]
[EVIDENCE] https://www.home-assistant.io/blog/2024/02/21/voice-chapter-6/ | https://community.home-assistant.io/t/the-current-state-of-voice-july-2024/746829

---

## Comparison Table

| System | Wake-Word | Memory | Tool Use | Offline | FOSS |
|---|---|---|---|---|---|
| Mycroft/OVOS | Local ML (Precise/openWakeWord) | Short-term only | Skills (Python plugins) | Full (with local STT/TTS) | Yes (Apache 2.0) |
| Rhasspy | Local (Rhasspy/Porcupine) | None built-in | Intent→action handlers | Full | Yes (MIT) |
| Willow | On-device (MultiNet/ESP-SR) | None | REST API calls | Wake-word yes; ASR needs server | Yes |
| Leon | Configurable | Context + memory module | Python packages | Partial (local LLM) | Yes (MIT) |
| Moshi | N/A (continuous) | Context window | None | GPU server required | Yes (CC BY 4.0) |
| OpenAI Realtime | N/A (API) | Per-session | JSON function calls | No (cloud only) | No |
| Gemini Live | N/A (API) | Per-session | JSON function calls | No (cloud only) | No |
| Ultravox | N/A (API) | None | None | No (API) | Yes (weights) |
| Pipecat | Any (plugin) | Via pipeline | Any (Python) | Partial (local models) | Yes (BSD) |
| Friend/Tab | Always-on (mic) | Context window only | None | No (BLE→cloud) | No |
| Limitless | Always-on (mic) | Local + cloud search | None | Pendant capture yes; query cloud | No |
| Bee | Always-on (beamform) | Local patterns | Email/calendar | Capture yes; actions cloud | No |
| Plaud NotePin | Always-on (passive) | 64GB local storage | None | Capture yes; transcription cloud | No |
| Rewind.ai | Always-on (screen+mic) | Fully local (LanceDB) | None | Full (local LLM) | No |
| Humane Ai Pin | Attention gesture | Per-session | Limited (buggy) | No | No |
| Rabbit R1 | Voice + button | None | LAM (unreliable) | No | No |
| LLMVM | CLI only | Conversation | Python execution | Local models | Yes (MIT) |

---

## Cross-Cutting Themes

### Theme 1: Wake-word is a spectrum, not a binary
The field has converged on a three-layer model: (1) always-running VAD (ultra-low power), (2) wake-word model fires only after VAD detects speech (OVOS Pre-Wake-VAD pattern), (3) full ASR runs only after wake-word confirmed. This triples efficiency vs. always-running full ASR. heare should implement this layering even in daemon mode.

### Theme 2: The 95% abandonment problem
Rabbit R1 lost 95% of users in 5 months. Friend went from viral to backlash symbol in 18 months. Humane shipped 10K vs. 100K target. Common cause: demo-reality gap. The surviving products (Bee, Plaud, Limitless) delivered on their core promise at launch.

### Theme 3: Privacy as architecture, not policy
Rewind (EFF-audited, zero leaks), OVOS (fully local), Rhasspy (offline) — these systems earned trust by making privacy impossible to violate by design, not by policy. Granola's "private by default" claim vs. reality is the cautionary tale. heare's macOS daemon position is ideal for local-first architecture.

### Theme 4: Tool use requires deterministic guardrails
No wearable AI product ships reliable open-ended tool use. Rabbit R1's LAM failure is the extreme case. Bee's email/calendar actions work because they are narrow, well-defined integrations. LLMVM's code-interleaved pattern improves reliability for complex chains. heare should scope tool use to well-tested narrow integrations with graceful failure modes.

### Theme 5: Latency budget forces architectural choices
The 800ms production target requires streaming everything: streaming STT (Deepgram, not batch Whisper), streaming LLM tokens, streaming TTS (first audio chunk before full response). Pipecat supports this end-to-end. The only way to break the 400ms barrier for memory-augmented responses is speculative retrieval (query while user is still speaking).

---

## Sources

1. [OpenVoiceOS Technical Manual — Wake Word Plugins](https://openvoiceos.github.io/ovos-technical-manual/312-wake_word_plugins/)
2. [OVOS Pre-Wake-VAD blog post (Nov 2025)](https://blog.openvoiceos.org/posts/2025-11-06-prewake-vad)
3. [Mycroft shutdown HN thread](https://news.ycombinator.com/item?id=34772848)
4. [Rhasspy Read the Docs](https://rhasspy.readthedocs.io/)
5. [Rhasspy3 GitHub](https://github.com/rhasspy/rhasspy3)
6. [LWN — Hopes and promises for open-source voice assistants](https://lwn.net/Articles/926484/)
7. [Willow — How Willow Works](https://heywillow.io/how-willow-works/)
8. [Willow HN Show HN](https://news.ycombinator.com/item?id=35948462)
9. [Humane Ai Pin — Unite.AI](https://www.unite.ai/what-went-wrong-with-the-humane-ai-pin/)
10. [Failure Museum — Humane Ai Pin](https://failure.museum/humane-ai-pin/)
11. [Rabbit R1 review — Laptop Mag 2024](https://www.laptopmag.com/ai/rabbit-r1-2024-ai-year-in-review)
12. [Why Rabbit R1 and Humane AI Pin Failed — Medium](https://medium.com/@thcookieh/why-did-the-rabbit-r1-and-humane-ai-pin-fail-at-launch-c108d6e2bebb)
13. [Friend AI backlash — CNN (2025)](https://www.cnn.com/2025/11/16/tech/friend-ai-device-backlash-ceo-avi-schiffmann)
14. [Friend AI — Wikipedia](https://en.wikipedia.org/wiki/Friend_(product))
15. [Limitless AI — getcoai.com](https://getcoai.com/news/limitless-ais-499-pendant-promises-to-be-your-always-on-memory-assistant/)
16. [Bee wearable — TechCrunch hands-on (Jan 2026)](https://techcrunch.com/2026/01/12/hands-on-with-bee-amazons-latest-ai-wearable/)
17. [Bee AI — Latent Space](https://www.latent.space/p/bee)
18. [Plaud NotePin review — TechRadar](https://www.techradar.com/ai-platforms-assistants/claude/plaud-notepin-bundle-review)
19. [Moshi paper — arXiv 2410.00037](https://arxiv.org/abs/2410.00037)
20. [Moshi GitHub](https://github.com/kyutai-labs/moshi)
21. [OpenAI Realtime API — Interruption handling](https://medium.com/@alozie_igbokwe/building-an-ai-caller-with-openai-realtime-api-part-5-how-openai-handles-interruptions-9050a453d28e)
22. [OpenAI Realtime API docs](https://developers.openai.com/api/docs/guides/realtime-conversations)
23. [Gemini Live API — Vertex AI](https://cloud.google.com/blog/products/ai-machine-learning/gemini-live-api-available-on-vertex-ai)
24. [Gemini 3.1 Flash Live blog](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-1-flash-live/)
25. [Ultravox GitHub](https://github.com/fixie-ai/ultravox)
26. [Pipecat GitHub](https://github.com/pipecat-ai/pipecat)
27. [One-second voice-to-voice with Pipecat — Modal](https://modal.com/blog/low-latency-voice-bot)
28. [AssemblyAI — The 300ms rule](https://www.assemblyai.com/blog/low-latency-voice-ai)
29. [Voice latency budget design — Smallest.ai](https://smallest.ai/blog/designing-voice-assistants-stt-llm-tts-tools-and-latency-budget)
30. [Ukrainian speech recognition repo](https://github.com/egorsmkv/speech-recognition-uk)
31. [Deepgram Ukrainian STT](https://deepgram.com/learn/speech-to-text-model-ukrainian)
32. [OpenAI Whisper Ukrainian — dev.ua](https://dev.ua/en/news/openai-improves-ai-for-voice-recognition-and-generation-model-supports-ukrainian-language)
33. [ElevenLabs Ukrainian TTS](https://elevenlabs.io/text-to-speech/ukrainian)
34. [Charlie Mnemonic memory system — GoodAI](https://www.goodai.com/introducing-charlie-mnemonic/)
35. [Memoria paper — arXiv 2512.12686](https://arxiv.org/html/2512.12686v1)
36. [Proactive behavior in voice assistants — ScienceDirect](https://www.sciencedirect.com/science/article/pii/S2451958824000447)
37. [Granola privacy issue — TechBuzz](https://www.techbuzz.ai/articles/granola-s-private-ai-notes-are-public-by-default)
38. [Home Assistant Voice Chapter 6](https://www.home-assistant.io/blog/2024/02/21/voice-chapter-6/)
39. [HA community — Current State of Voice July 2024](https://community.home-assistant.io/t/the-current-state-of-voice-july-2024/746829)
40. [LLMVM GitHub](https://github.com/9600dev/llmvm)
41. [OVOS — Skills and Intents Technical Manual](https://openvoiceos.github.io/ovos-technical-manual/399-intents/)
42. [Mycroft Padatious GitBook](https://mycroft-ai.gitbook.io/docs/mycroft-technologies/padatious)
43. [Rewind AI — privacy architecture](https://ucstrategies.com/news/rewind-ai-mac-memory-search-tool-specs-privacy-pricing-2026/)
44. [Privacy in wearable AI — Frontiers in Digital Health (2025)](https://www.frontiersin.org/journals/digital-health/articles/10.3389/fdgth.2025.1431246/full)
45. [Pipecat interruption handling — Zoice.ai](https://zoice.ai/blog/interruption-handling-in-conversational-ai/)

---

[STAGE_COMPLETE:12]
