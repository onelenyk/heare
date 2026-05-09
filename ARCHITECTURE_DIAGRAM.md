# Heare System Architecture — Visual Overview

> Last verified against commit `3686961` (feat: browser-bridge + audio-event detection).
> All paths and line references are current as of that commit.

This document gives a precise, multi-layer picture of every component in the
heare system and how they interact. It complements `ARCHITECTURE_ANALYSIS.md`
(narrative deep-dive) — this file is the visual + structural index.

The diagrams are organized as:

1. Process topology — what runs where
2. The complete frame chain (the main pipeline)
3. Shared-state graph — who reads/writes which in-memory objects
4. Daemon lifecycle (startup → run → shutdown)
5. Tool dispatch mechanics
6. Hot-reload surface
7. Filesystem state
8. Common-case sequence diagrams
9. Special frame paths (upstream, system frames, frame relay)
10. Known defects flagged inline

---

## 1. Process Topology

```
══════════════════════════════════════════════════════════════════════════════════════
                              EXTERNAL SERVICES (network)
   ┌──────────────┬──────────────────┬────────────┬─────────────┬──────────────────┐
   │ Groq Whisper │ OpenRouter API   │ z.ai API   │ Edge TTS    │ skillsmp.com /   │
   │ (STT)        │ (LLM, OpenAI sh) │ (LLM, Anth)│ (TTS, ws)   │ MCP registry     │
   └──────┬───────┴────────┬─────────┴─────┬──────┴──────┬──────┴────────┬─────────┘
          │ HTTPS          │ HTTPS         │ HTTPS       │ wss          │ HTTPS
══════════╪════════════════╪═══════════════╪═════════════╪═══════════════╪═════════════
          ▼                ▼               ▼             ▼               ▼
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │ HEARE DAEMON  (single Python 3.11 process, single asyncio event loop)         │
   │ ────────────────────────────────────────────────────────────────────────────  │
   │   Entry:        src/main.py:_cmd_start          (asyncio.run)                 │
   │   Supervisor:   src/main.py:run_until_stopped   (asyncio.wait FIRST_COMPLETED)│
   │   Frame loop:   pipecat.PipelineRunner.run(pipeline)                          │
   │   PID file:     ~/.heare/heare.pid              (single-instance lock)        │
   │                                                                               │
   │   Background tasks (asyncio.create_task):                                     │
   │     ◾ pipeline_task         the pipecat frame loop                            │
   │     ◾ warmup_task           periodic Edge TTS keep-alive                      │
   │     ◾ namer_task            speaker-naming LLM caller (optional)              │
   │     ◾ greeting (one-shot)   "<bot> ready" via TTSSpeakFrame                   │
   │     ◾ inject poller         ~/.heare/inject/ → TranscriptionFrame             │
   │     ◾ heartbeat             writes ~/.heare/heartbeat (alive proof)           │
   │     ◾ browser_bridge        WS server on 127.0.0.1:9333 (Chrome ext RPC)      │
   │                              src/daemon/browser.py:start/stop                  │
   └──────────────────────────────┬────────────────────────────────────────────────┘
                                  │ reads/writes (filesystem IPC)
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │ FILESYSTEM (~/.heare/) — see §7 for full schema                                │
   │   heare.db (SQLite WAL) ▪ heare.pid ▪ provider ▪ mute.flag ▪ mute_input.flag  │
   │   capabilities.json ▪ identity.json ▪ speakers.json ▪ inject/ ▪ logs/         │
   └──────────────────────────────┬────────────────────────────────────────────────┘
                                  │ reads (separate process)
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │ WATCH DASHBOARD  (separate Python process, Textual TUI)                       │
   │   src/watch/{app,data,widgets,screens,_legacy}.py                             │
   │   Reads heare.db + daemon.log; toggles flag files via hotkeys.                │
   │   Runs `heare watch` (src/main.py:_cmd_watch) — does NOT load pipecat.        │
   └───────────────────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────────────────┐
   │ ADMIN CLI  (heare {stop,status,mode,provider,reset-*,enroll-owner,…})         │
   │   Same src/main.py module; argparse subcommands. Most commands DO NOT load    │
   │   pipecat — pipecat imports are deferred to _cmd_start only.                  │
   └───────────────────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────────────────┐
   │ CHROME EXTENSION  (extensions/heare-bridge/, MV3, sideloaded)                │
   │   Runs in user's Chrome browser process (NOT the daemon process).            │
   │   Connected to daemon via WebSocket on 127.0.0.1:9333.                       │
   │   Provides 8 RPC methods: list_tabs, read_page, click, fill, extract,        │
   │     navigate, open_tab, activate_tab.                                        │
   │   Architecture: service-worker + offscreen document (persistent WS owner).    │
   └───────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. The Frame Chain (Pipecat pipeline)

Composed by `src/pipeline/build.py:build_pipeline` and `_assemble_native_stages`.
DOWNSTREAM is top-to-bottom. UPSTREAM frames (interrupt, cancel) flow the other way.

```
══════════════════════════════════════════════════════════════════════════════════════
                          PIPECAT FRAME CHAIN (downstream ↓)
══════════════════════════════════════════════════════════════════════════════════════

mic ─► ┌─────────────────────────────────────────────────────────────────────────┐
       │ transport.input  (LocalAudioTransport)                                  │
       │   src/pipeline/build.py:260                                              │
       │   ◾ audio_in_sample_rate = 16000                                          │
       │   ◾ VAD: SileroVADAnalyzer(stop=0.5, start=0.3, conf=0.7, min_vol=0.6)   │
       │   ◾ Smart-turn: LocalSmartTurnAnalyzerV3(stop=1.0)                       │
       │   ◾ Emits: InputAudioRawFrame, UserStarted/StoppedSpeakingFrame,        │
       │            BotStarted/StoppedSpeakingFrame, MetricsFrame                │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ input_mute_gate                                                          │
       │   src/pipeline/stages/mute_gate.py:114-134                               │
       │   ◾ Reads: ~/.heare/mute_input.flag (file existence per InputAudioRawFrame)│
       │   ◾ Drop: InputAudioRawFrame when flag exists                            │
       │   ◾ Pass: everything else unchanged                                      │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ [speaker_buffer]   (optional — speaker_id_enabled)                      │
       │   src/voice/speaker/processor.py:create_speaker_processors               │
       │   ◾ Buffers raw audio per VAD bracket for diarization                   │
       │   ◾ Loaded ECAPA model: src/voice/speaker/id.py:load_model              │
       │   ◾ Voiceprint store: src/voice/speaker/gallery.py SpeakerGallery        │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ stt = GroqSTTService                                                     │
       │   src/pipeline/build.py:274                                              │
       │   ◾ Model: Whisper-large-v3 (Groq cloud)                                 │
       │   ◾ Language: HINT (settings.groq_language); Groq detects + may override │
       │   ◾ include_prob_metrics=True (per-utterance language confidence)        │
       │   ◾ Emits: TranscriptionFrame(text, language, …) on speech-end          │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ stt_error_observer  (inline anonymous class)                            │
       │   src/pipeline/build.py:351-365                                          │
       │   ◾ Observes ErrorFrame from stt → indication.notify(STT_ERROR, body=…) │
       │   ◾ Forwards every frame unchanged (observer pattern)                   │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ [speaker_tagger]  (optional — speaker_id_enabled)                       │
       │   src/voice/speaker/processor.py                                         │
       │   ◾ Embeds buffered audio with ECAPA, looks up gallery                  │
       │   ◾ Annotates: TranscriptionFrame.speaker_id + speaker_confidence       │
       │   ◾ namer_enqueue: pushes unknown speakers to async LLM-naming task    │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ [audio_event_observer]  (optional — audio_event_detection_enabled)      │
       │   src/audio_event/observer.py:create_audio_event_observer               │
       │   ◾ Pass-through YAMNet classifier (ONNX, 16kHz, 0.96s windows)         │
       │   ◾ Detects non-speech: laughter, cough, bark, etc. (curated 17-label)  │
       │   ◾ Drop-on-busy + 2-window confirmation before event emit              │
       │   ◾ Writes: ~/.heare/audio_event.json {label, score, ts}               │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ voice_state_observer                                                     │
       │   src/pipeline/stages/voice_state_observer.py:create_voice_state_observer
       │   ◾ Writes: ~/.heare/voice_state.json {state, since_ts, last_*}        │
       │   ◾ state ∈ {idle, listening, stt, result} per STT transition          │
       │   ◾ result auto-decays → idle after 4s (dashboard-side timer)           │
       │   ◾ Read by watch dashboard: VoiceStateBar widget                       │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
   ┌────┼─────────────────────────────────────────────────────────────────────────┐
   │    │                  TRANSCRIPTION GATE  ★ critical chokepoint              │
   │    ▼                                                                         │
   │ ┌─────────────────────────────────────────────────────────────────────────┐  │
   │ │ transcription_gate   (TranscriptionGateProcessor)                       │  │
   │ │   src/pipeline/stages/transcription_gate.py:106-417                     │  │
   │ │                                                                         │  │
   │ │   On BotStartedSpeakingFrame  → bot_speaking=True                       │  │
   │ │   On BotStoppedSpeakingFrame  → bot_speaking=False; cooldown=now+2.0s   │  │
   │ │   On IndicationCueFrame       → indication_speaking=frame.start        │  │
   │ │                                                                         │  │
   │ │   On TranscriptionFrame:                                               │  │
   │ │     1. (optional) debounce-coalesce within transcript_debounce_seconds │  │
   │ │     2. Drop if bot_speaking | indication_speaking | enrollment | cooldown│  │
   │ │     3. detect_language_from_frame → 2-turn hysteresis → LanguageState  │  │
   │ │     4. _set_tts_voice(active_lang) — calls tts.set_voice(...)         │  │
   │ │     5. store.log_transcript(text, mode, speaker_id, …)  [DB write]    │  │
   │ │     6. is_standalone_cancel_imperative → push InterruptionFrame ↑↑↑   │  │
   │ │     7. forward TranscriptionFrame downstream                          │  │
   │ │                                                                         │  │
   │ │   ⚠ DEFECT: cancel-keyword check is AFTER bot_speaking drop. Cancel   │  │
   │ │     words spoken DURING bot speech are silently swallowed. The 2s     │  │
   │ │     cooldown means cancel only works ≥2s after bot stops. See         │  │
   │ │     ARCHITECTURE_ANALYSIS Risk #3.                                    │  │
   │ └─────────────────────────────────────────────────────────────────────────┘  │
   └────┼─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ system_prompt_injector   (SystemPromptInjector)                         │
       │   src/agent/llm/context_injector.py:295-394                              │
       │                                                                          │
       │   On TranscriptionFrame (every user turn):                              │
       │     1. language = LanguageState.language                                │
       │     2. conversation_id = await conversation_manager.get_or_create_active│
       │     3. ctx = await context_builder.build_for_generator(transcript,     │
       │              persona, conversation_id, user_language)        ←DB heavy │
       │     4. matches = capability_index.query(transcript, top_k=5)          │
       │     5. new_prompt = render_native_system_prompt(persona, ctx, lang,   │
       │                       capability_hints=matches)                       │
       │     6. _replace_system_message(llm_context, new_prompt)               │
       │     7. forward TranscriptionFrame                                     │
       │                                                                          │
       │   render_native_system_prompt assembles:                                │
       │     ▪ persona block          (name/creature/vibe/emoji/tagline)         │
       │     ▪ language directive     ("Respond ONLY in <lang>")                 │
       │     ▪ Host OS hint                                                       │
       │     ▪ Recent transcripts / conversation_summary / topics / entities     │
       │     ▪ Recent action log (deduplicates old searches)                     │
       │     ▪ MCP server prompt block                                           │
       │     ▪ "Capabilities" section with category rules                       │
       │     ▪ Installed skills list (from skills/agent_skills loader)           │
       │     ▪ Top-K relevant capability hints for this turn                    │
       │     ▪ Reply rules (≤1 sentence, no markdown, no tool-name leakage)     │
       │                                                                          │
       │   ⚠ This runs SYNCHRONOUSLY before the user_aggregator. Slow DB or     │
       │     capability_index.query stalls every turn. See Risk #1.             │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ user_aggregator  (LLMContextAggregatorPair.user())                      │
       │   pipecat.processors.aggregators.llm_response_universal                  │
       │   ◾ Appends user TranscriptionFrame to shared LLMContext.messages       │
       │   ◾ Emits: LLMContextFrame   (start of LLM turn)                        │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
   ┌────┼─────────────────────────────────────────────────────────────────────────┐
   │    │                   LLM CORE  ★ provider switch + tools                  │
   │    ▼                                                                         │
   │ ┌─────────────────────────────────────────────────────────────────────────┐  │
   │ │ llm_service = SwitchableLLMService                                      │  │
   │ │   src/agent/llm/switchable.py:37-352                                    │  │
   │ │                                                                          │  │
   │ │   Composition (NOT inheritance):                                        │  │
   │ │     ┌──────────────────────────────────────────────────────────────┐    │  │
   │ │     │ wrapper (linked into pipeline)                              │    │  │
   │ │     │                                                              │    │  │
   │ │     │   ┌────────────────────┐    ┌─────────────────────────┐     │    │  │
   │ │     │   │ _or_service         │    │ _zai_service             │     │    │  │
   │ │     │   │  OpenAILLMService  │    │  AnthropicLLMService     │     │    │  │
   │ │     │   │  → OpenRouter API  │    │  → z.ai API              │     │    │  │
   │ │     │   └─────────┬──────────┘    └────────────┬─────────────┘     │    │  │
   │ │     │             │ patched push_frame        │ patched push_frame│    │  │
   │ │     │             ▼                            ▼                   │    │  │
   │ │     │   "frame relay" — delegates' frames are captured by         │    │  │
   │ │     │   wrapper.push_frame because their _next/_prev are NULL.    │    │  │
   │ │     └──────────────────────────────────────────────────────────────┘    │  │
   │ │                                                                          │  │
   │ │   Provider routing:                                                     │  │
   │ │     ◾ ~/.heare/provider read mtime-gated, ONLY at turn-start frames    │  │
   │ │       (LLMContextFrame, OpenAILLMContextFrame, LLMMessagesFrame)       │  │
   │ │     ◾ Sticky-turn lock: _turn_in_flight=True until LLMFullResponseEnd  │  │
   │ │     ◾ Mid-turn provider changes are deferred to next turn              │  │
   │ │                                                                          │  │
   │ │   Tool registration (register_function fan-out):                       │  │
   │ │     ◾ register_all_tools(llm_service, settings, conversation_manager)  │  │
   │ │       called once at build (src/agent/tools/schemas.py)                │  │
   │ │     ◾ Each tool's handler is registered on BOTH delegates              │  │
   │ │     ◾ cancel_on_interruption=True per registration                     │  │
   │ │                                                                          │  │
   │ │   Failure handling:                                                     │  │
   │ │     ◾ z.ai AuthError/APIStatusError/Timeout → permanent process-wide   │  │
   │ │       z.ai disable, fallback to OpenRouter, ErrorFrame upstream        │  │
   │ │     ◾ Rate-limited log: 1 ERROR per 60s window                         │  │
   │ │     ◾ Other exceptions re-raise (crash the turn)                       │  │
   │ │                                                                          │  │
   │ │   ⚠ z.ai disable is one-way and process-lifetime. See Risk #17.        │  │
   │ │   ⚠ No tool timeout enforcement at this layer. See Risk #4.            │  │
   │ │                                                                          │  │
   │ │   Emits per turn:                                                       │  │
   │ │     LLMFullResponseStartFrame                                           │  │
   │ │     ▸ LLMTextFrame * N        (streamed text chunks)                    │  │
   │ │     OR ▸ FunctionCallInProgressFrame → handler runs → result feedback  │  │
   │ │     LLMFullResponseEndFrame                                             │  │
   │ │     MetricsFrame[LLMUsageMetricsData]                                   │  │
   │ └─────────────────────────────────────────────────────────────────────────┘  │
   └────┼─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ assistant_response_logger   (AssistantResponseProcessor)                │
       │   src/pipeline/stages/assistant_response_logger.py                      │
       │   ◾ Buffers LLMTextFrame.text between Start/End                         │
       │   ◾ On End: store.log_transcript(text, mode="assistant", speaker_id="bot")│
       │   ◾ Standalone TTSSpeakFrame is logged directly (startup greeting)      │
       │   ◾ Forwards every frame unchanged                                      │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ tts_scrub   (TTSScrubProcessor)                                         │
       │   src/pipeline/stages/tts_scrub_processor.py                            │
       │   ◾ Buffers LLMTextFrame between Start/End                              │
       │   ◾ On End: _scrub_buffered_response (joined-text rule:                 │
       │             if joined scrubs to "" → blank ALL frames)                  │
       │   ◾ TTSSpeakFrame: _scrub_speak_frame in-place                          │
       │   ◾ Strips tool-name narration ("list_tools", "bash:foo", …)            │
       │   ◾ Logger UPSTREAM sees raw text; user hears scrubbed text             │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ tts = EdgeTTSService (via create_edge_tts_service)                      │
       │   src/voice/tts/edge.py                                                  │
       │   ◾ Streams speech via Edge TTS websocket                               │
       │   ◾ Voice swap on transcription_gate language change                    │
       │   ◾ TTSCache (LRU, key=text+voice): src/voice/tts/cache.py             │
       │   ◾ Pre-warmed via TTSCache.warmup(FIXED_PHRASES) at startup           │
       │   ◾ Emits: TTSAudioRawFrame chunks + BotStarted/StoppedSpeakingFrame   │
       │   ◾ MetricsFrame[TTSUsageMetricsData] on completion                    │
       │                                                                          │
       │   ⚠ No fallback if Edge TTS fails. Reply is silent. See Risk #19.      │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ usage_recorder   (observer)                                              │
       │   src/pipeline/stages/usage_recorder.py                                  │
       │   ◾ Watches MetricsFrame[LLMUsageMetricsData] (from llm_service)        │
       │   ◾ Watches MetricsFrame[TTSUsageMetricsData] (from tts)                │
       │   ◾ Watches finalized TranscriptionFrame + VAD bracket frames           │
       │   ◾ Writes: store.log_usage_event(provider, kind, tokens|chars|seconds) │
       │   ◾ provider_getter: lambda: llm_service.active_provider (live)         │
       │   ◾ stt_provider="groq-whisper-large-v3", tts_provider="edge_tts"       │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ tts_fade_observer   (inline _TtsFadeOnInterruption class)               │
       │   src/pipeline/build.py:377-398                                          │
       │   ◾ On InterruptionFrame: await tts.cancel_pending() (~50ms fade)       │
       │   ◾ Polish layer on top of pipecat's native cancel_on_interruption      │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ [sound_cue_processor]  (optional — indication.sound_enabled)            │
       │   src/voice/indication/core.py:build_sound_cue_processor                 │
       │   ◾ Mixes audio cues (start/end/blocked) into TTSAudioRawFrame stream  │
       │   ◾ Emits IndicationCueFrame so transcription_gate can suppress STT    │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ mute_gate                                                                │
       │   src/pipeline/stages/mute_gate.py:91-111                                │
       │   ◾ Reads: ~/.heare/mute.flag (file existence per TTSAudioRawFrame)     │
       │   ◾ Drop: TTSAudioRawFrame when flag exists                             │
       │   ◾ Bot text is still logged (logger sits UPSTREAM of TTS)              │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ transport.output  →  speakers                                            │
       │   audio_out_sample_rate = settings.tts_sample_rate (24000)               │
       └────────────────────────────────────┬────────────────────────────────────┘
                                            ▼
       ┌─────────────────────────────────────────────────────────────────────────┐
       │ assistant_aggregator  (LLMContextAggregatorPair.assistant())            │
       │   ◾ Appends bot turn back to shared LLMContext.messages                 │
       │   ◾ Closes the round-trip — the same LLMContext is reused next turn    │
       └─────────────────────────────────────────────────────────────────────────┘
```

### Frame propagation rules

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Frame routing in pipecat                                                         │
│                                                                                  │
│  • DataFrame (TranscriptionFrame, LLMTextFrame, TTSAudioRawFrame, etc.)         │
│      → routed via per-stage asyncio queue, processed in order                   │
│                                                                                  │
│  • SystemFrame (StartFrame, EndFrame, CancelFrame, InterruptionFrame,           │
│                 ErrorFrame)                                                       │
│      → bypass per-stage queues, routed IMMEDIATELY through every stage          │
│      → this is why InterruptionFrame UPSTREAM works                             │
│                                                                                  │
│  • ControlFrame (BotStarted/Stopped, MetricsFrame, etc.)                        │
│      → routed via queue but at higher priority                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Shared-State Graph

```
══════════════════════════════════════════════════════════════════════════════════════
            IN-MEMORY OBJECTS  (single-process, asyncio-safe)
══════════════════════════════════════════════════════════════════════════════════════

┌─────────────────────────────────────────────────────────────────────────────────┐
│ LanguageState  (src/pipeline/language_state.py)                                  │
│                                                                                   │
│   single attribute: language ("en" | "uk" | "ru")                               │
│   listener registered via .set_change_listener(fn)                              │
│                                                                                   │
│   WRITERS:                                                                       │
│     ▸ transcription_gate:      .set_language(lang) after 2-turn hysteresis      │
│                                                                                   │
│   READERS:                                                                       │
│     ▸ system_prompt_injector:  .language → render_native_system_prompt          │
│     ▸ build._wire_language_state:  on change → rewrite messages[0] in LLMContext│
│     ▸ transcription_gate._set_tts_voice:  → tts.set_voice(...)                  │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ LLMContext  (pipecat.processors.aggregators.llm_context.LLMContext)             │
│                                                                                   │
│   ◾ messages = [{system}, {user|assistant}, ...]                                 │
│   ◾ tools = ToolsSchema (built once by agent/tools/schemas.build_tools_schema)   │
│                                                                                   │
│   WRITERS:                                                                       │
│     ▸ system_prompt_injector:    rewrite messages[0]                            │
│     ▸ _wire_language_state:      rewrite messages[0]                            │
│     ▸ user_aggregator:           append user message                            │
│     ▸ assistant_aggregator:      append bot message                             │
│                                                                                   │
│   READERS:                                                                       │
│     ▸ llm_service:               .get_messages() each turn                      │
│                                                                                   │
│   ⚠ Single shared object across the pipeline lifetime.                          │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ CapabilityIndex  (src/agent/tools/capability_index.py)                          │
│                                                                                   │
│   ◾ Combined index over: built-in tools + installed skills + MCP servers        │
│   ◾ Built once at startup: build_capability_index(settings, workspace_dir)      │
│   ◾ Stored as global singleton: set_capability_index(idx)                       │
│   ◾ Persisted snapshot: ~/.heare/capabilities.json                              │
│                                                                                   │
│   WRITERS:                                                                       │
│     ▸ build_pipeline at startup                                                  │
│     ▸ install_skill / install_mcp_server tools (rebuild on side effect)         │
│                                                                                   │
│   READERS:                                                                       │
│     ▸ system_prompt_injector:    .query(transcript, top_k=5)                    │
│     ▸ direct.py tools:           discover/install/revoke handlers               │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ Indication  (src/voice/indication/core.py)                                       │
│                                                                                   │
│   ◾ Multi-backend event router; global singleton via set_indication / get_indication│
│   ◾ Backends:                                                                     │
│       SoundBackend         src/voice/indication/backends/sound.py                │
│       VisualBackend        src/voice/indication/backends/visual.py (JSONL log)   │
│       NotificationBackend  src/voice/indication/backends/notification.py (macOS) │
│                                                                                   │
│   WRITERS (notify):                                                              │
│     ▸ stt_error_observer:        STT_ERROR                                       │
│     ▸ direct.py tools:           ACTION_*                                         │
│     ▸ build._cmd_start:          DAEMON_STARTED, DAEMON_SHUTDOWN                  │
│     ▸ heartbeat / namer / etc.   (occasional)                                     │
│                                                                                   │
│   READERS (state):                                                               │
│     ▸ transcription_gate:        is_enrollment_active()                          │
│     ▸ sound_cue_processor:       state-driven cue timing                          │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ TTSCache  (src/voice/tts/cache.py)                                               │
│                                                                                   │
│   ◾ LRU cache keyed on (text, voice)                                             │
│   ◾ Pre-warmed at startup: TTSCache.warmup(FIXED_PHRASES, synthesize_to_pcm)    │
│   ◾ Reads/writes by tts service alone                                            │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ BrowserBridge  (src/agent/browser_bridge.py)                                     │
│                                                                                   │
│   ◾ Singleton WebSocket server on 127.0.0.1:9333 (single auth'd client max)      │
│   ◾ Token-authenticated + WPS-style 6-digit pair-code (60s TTL, 5-attempt)       │
│   ◾ Lonely-watcher mints fresh pair code every 30s while disconnected            │
│   ◾ Wire protocol versioned: v=1, messages: auth, pair, request, response, ping  │
│                                                                                   │
│   WRITERS:                                                                       │
│     ▸ BrowserBridge.call(method, params) → async RPC through Chrome ext         │
│                                                                                   │
│   READERS (8 LLM-facing tools in direct.py):                                     │
│     ▸ list_browser_tabs, read_browser_page, click_in_browser, fill_in_browser    │
│     ▸ extract_in_browser, navigate_browser, open_browser_tab, activate_browser_tab
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Daemon Lifecycle

```
══════════════════════════════════════════════════════════════════════════════════════
                       STARTUP SEQUENCE  (src/main.py:_cmd_start)
══════════════════════════════════════════════════════════════════════════════════════

   ┌──────────────────────────────────────────────────────────────────┐
   │ Phase A. Pre-flight                                                │
   │   ▸ load_dotenv(.env)                                             │
   │   ▸ load_settings()  ← config.toml + env vars                     │
   │   ▸ ensure_dirs()    ← mkdir ~/.heare/{logs, inject, ...}         │
   │   ▸ _setup_logging() ← RotatingFileHandler(daemon.log)            │
   │   ▸ _ensure_workspace_mcp() ← seed workspace/.mcp.json            │
   │   ▸ PID file lock or refuse                                       │
   └──────────────────────────────────────────────────────────────────┘
                                  ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │ Phase B. Build                                                     │
   │   ▸ TranscriptStore(db_path) → store.init() → purge_older_than()  │
   │   ▸ build_openrouter_bootstrap → ensure_identity                  │
   │       ↳ if ~/.heare/identity.json missing: HTTPS call → JSON write│
   │   ▸ render_persona(template, identity)                            │
   │   ▸ ConversationManager(store)  (if memory enabled)               │
   │       ↳ hydrate_action_log(since_ts=now-conversation_idle_seconds)│
   │   ▸ ContextBuilder(store, settings, conversation_manager)         │
   │   ▸ Optional: SpeakerGallery.load + speaker_id.load_model         │
   │   ▸ Optional: maybe_build_namer (if speaker subsystem ready)      │
   │   ▸ build_pipeline(...) → 6-tuple                                 │
   │     ╔═══════════════════════════════════════════════════════╗     │
   │     ║ Inside build_pipeline (src/pipeline/build.py:193):   ║     │
   │     ║   ▸ create transport, stt, tts                        ║     │
   │     ║   ▸ create indication + register backends             ║     │
   │     ║   ▸ create stt_error_observer + tts_fade_observer     ║     │
   │     ║   ▸ create LanguageState                              ║     │
   │     ║   ▸ create transcription_gate                         ║     │
   │     ║   ▸ create SwitchableLLMService (both delegates)      ║     │
   │     ║   ▸ build_tools_schema → LLMContext(messages, tools)  ║     │
   │     ║   ▸ user/assistant_aggregator from LLMContextAggrPair ║     │
   │     ║   ▸ register_all_tools(llm_service, ...)              ║     │
   │     ║   ▸ load dynamic_tools from DB → register             ║     │
   │     ║   ▸ build_capability_index → set_capability_index     ║     │
   │     ║   ▸ create_audio_event_observer (opt, deferred)        ║     │
   │     ║   ▸ create_voice_state_observer                        ║     │
   │     ║   ▸ system_prompt_injector wired to ContextBuilder    ║     │
   │     ║   ▸ assistant_response_logger / tts_scrub             ║     │
   │     ║   ▸ usage_recorder                                    ║     │
   │     ║   ▸ mute_gate / input_mute_gate                       ║     │
   │     ║   ▸ _assemble_native_stages → Pipeline(stages)        ║     │
   │     ║   ▸ wrap in PipelineTask(allow_interruptions=False,   ║     │
   │     ║       enable_metrics=True, enable_usage_metrics=True) ║     │
   │     ╚═══════════════════════════════════════════════════════╝     │
   └──────────────────────────────────────────────────────────────────┘
                                  ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │ Phase C. Warmup + side rails                                       │
   │   ▸ tts_cache.warmup(FIXED_PHRASES, synthesize_fn)                │
   │   ▸ create_task(_push_greeting)                                   │
   │       ↳ 1s delay → indication.notify(DAEMON_STARTED)              │
   │       ↳ llm_service.push_frame(TTSSpeakFrame("<bot> online"))     │
   │   ▸ create_task(speaker_namer.run) if enabled                    │
   │   ▸ create_task(run_injector_loop(inject_dir, ...))              │
   │       ↳ polls ~/.heare/inject/ for .txt drops → TranscriptionFrame│
   │   ▸ WarmupTask(voice, interval) prepared                          │
   └──────────────────────────────────────────────────────────────────┘
                                  ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │ Phase D. Run                                                       │
   │   ▸ runner = PipelineRunner()                                     │
   │   ▸ await run_until_stopped(runner, pipeline, warmup, namer_task) │
   │     ╔═══════════════════════════════════════════════════════╗     │
   │     ║ Inside run_until_stopped (src/main.py:317):          ║     │
   │     ║   pipeline_task = create_task(runner.run(pipeline))   ║     │
   │     ║   warmup_task   = create_task(warmup.run())           ║     │
   │     ║   browser_bridge_task = create_task(bridge.start())   ║     │
   │     ║   stop_event    = Event()                             ║     │
   │     ║                                                        ║     │
   │     ║   add_signal_handler(SIGTERM/SIGINT  → stop_event.set)║     │
   │     ║   add_signal_handler(SIGHUP          → indication.reload)║  │
   │     ║                                                        ║     │
   │     ║   await asyncio.wait({pipeline_task, warmup_task,    ║     │
   │     ║                       browser_bridge_task,            ║     │
   │     ║                       stop_waiter, namer_task},      ║     │
   │     ║                      return_when=FIRST_COMPLETED)    ║     │
   │     ║                                                        ║     │
   │     ║   for t in all_tasks: t.cancel(); await t             ║     │
   │     ╚═══════════════════════════════════════════════════════╝     │
   └──────────────────────────────────────────────────────────────────┘
                                  ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │ Phase E. Cleanup (in finally)                                      │
   │   ▸ indication.notify(DAEMON_SHUTDOWN); indication.aclose()       │
   │   ▸ store.close()                                                 │
   │   ▸ pid_file.unlink()                                              │
   │   ▸ "heare stopped"                                                │
   └──────────────────────────────────────────────────────────────────┘

══════════════════════════════════════════════════════════════════════════════════════
                              SHUTDOWN PATHS
══════════════════════════════════════════════════════════════════════════════════════

   ① Graceful (user)        SIGTERM → stop_event.set → cancel all → Phase E
   ② External (heare stop)   SIGTERM via PID; if 30×100ms timeout → SIGKILL
   ③ Pipeline self-end       EndFrame propagates → pipeline_task done → Phase E
   ④ Pipeline crash          unhandled exc in runner → pipeline_task done → Phase E
   ⑤ User cancel mid-bot     InterruptionFrame UPSTREAM → tts.cancel_pending +
                                pipecat cancels register_function in flight (does NOT
                                end the pipeline; conversation continues)
```

---

## 5. Tool Dispatch Mechanics

```
══════════════════════════════════════════════════════════════════════════════════════
                         TOOL SUBSYSTEM (src/agent/tools/)
══════════════════════════════════════════════════════════════════════════════════════

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ schemas.py:build_tools_schema()                                          │
   │   ▸ Returns ToolsSchema(standard_tools=[FunctionSchema, ...])           │
   │   ▸ Used to seed LLMContext.tools at startup                            │
   └────────────────┬────────────────────────────────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ schemas.py:register_all_tools(llm_service, settings, ...)               │
   │   ▸ For each tool name, call llm_service.register_function(name, fn,    │
   │       cancel_on_interruption=True)                                      │
   │   ▸ SwitchableLLMService.register_function fans out to BOTH delegates    │
   │   ▸ The handler `fn` looks up the actual implementation in              │
   │       agent/tools/direct.py (e.g. _execute_bash, _execute_read, …)      │
   └────────────────┬────────────────────────────────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Dynamic tools (DB-backed, hot-loaded)                                   │
   │   ▸ store.load_all_dynamic_tools() at startup                            │
   │   ▸ For each: register_dynamic_tool, register_dynamic_tool_schema,      │
   │       register_dynamic_tool_handler                                     │
   │   ▸ Definition JSON contains: arguments schema, implementation_type,    │
   │       implementation (e.g. bash command template)                       │
   └─────────────────────────────────────────────────────────────────────────┘

══════════════════════════════════════════════════════════════════════════════════════
                            TOOL CALL FLOW (per turn)
══════════════════════════════════════════════════════════════════════════════════════

   user: "read config.toml"
        │
        ▼
   transcription_gate forwards TranscriptionFrame
        │
        ▼
   system_prompt_injector rebuilds prompt
        │
        ▼
   user_aggregator emits LLMContextFrame
        │
        ▼
   SwitchableLLMService → delegate (e.g. OpenAI shape)
        │  ◾ delegate decides to call function "read"
        │  ◾ emits FunctionCallInProgressFrame + tool args JSON
        ▼
   pipecat dispatches: registered handler "read" → _execute_read(args, settings)
        │
        ├──► subprocess / file IO / httpx / etc.
        │
        ▼
   handler returns dict {success, output, error, exit_code, spoken}
        │  (cancellable via CancelledError if InterruptionFrame fires)
        ▼
   pipecat re-injects tool result as another LLMContextFrame turn
        │
        ▼
   SwitchableLLMService runs ANOTHER LLM round on the same delegate
        │
        ▼
   LLM emits final text response (LLMTextFrame chunks + LLMFullResponseEndFrame)
        │
        ▼
   ...continues through assistant_response_logger, tts_scrub, tts, etc.

   ⚠ No timeout on tool dispatch in the wiring layer. Hangs hang the turn.
   ⚠ No iteration cap on recursive tool calls. Misbehaving LLM can chain.
   ⚠ Tool arguments and the LLM context grow on each iteration.
```

### Tool families (in `src/agent/tools/direct.py`)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ ~50 _execute_* functions in direct.py (4,235 LOC, future split target):       │
│                                                                                │
│   filesystem    bash, read, write, edit, list_directory, find_files,          │
│                 get_tree_view, get_file_info, copy_file, move_file,           │
│                 delete_file, create_directory, create_archive, extract_archive│
│   web           web_search, web_fetch, _search_serper, _search_duckduckgo     │
│   memory        recall, remember, list_memories, log_action                   │
│   capability    discover_capability, install_skill, create_skill,             │
│                 install_mcp_server, register_mcp_server, revoke_capability    │
│   skills        list_skills, list_capabilities, run_skill                     │
│   speaker       re_enroll, list_profiles                                      │
│   browser       list_browser_tabs, read_browser_page, click_in_browser,        │
│                 fill_in_browser, extract_in_browser, navigate_browser,         │
│                 open_browser_tab, activate_browser_tab  (via BrowserBridge RPC)│
│   daemon        stop_daemon, restart_daemon (self-control via daemon/control) │
│   batch         batch_operation                                               │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Hot-Reload Surface

```
══════════════════════════════════════════════════════════════════════════════════════
       What can change without restarting the daemon, and how
══════════════════════════════════════════════════════════════════════════════════════

   Surface                  Mechanism                                Granularity
   ──────────────────────── ──────────────────────────────────────── ──────────────
   LLM provider             ~/.heare/provider                        per-turn
                              SwitchableLLMService._sync_provider()
                              mtime-gated, fires only at turn-start
   Bot mute (output)        ~/.heare/mute.flag                       per-frame
                              mute_gate checks .exists() per frame
   Mic mute (input)         ~/.heare/mute_input.flag                 per-frame
                              input_mute_gate checks .exists() per frame
   Active mode              ~/.heare/mode (silent/focus/ambient)     lazy read
   Indication config        SIGHUP → indication.reload(...)          on signal
   Active language          STT detection → 2-turn hysteresis        per-turn
   System prompt            rebuilt from LanguageState + ctx_builder per-turn
   TTS voice                tts.set_voice(...) on language change    on lang change
   Skill / MCP installs     install_skill_tool → rebuild capability  on tool call
                              index → next prompt sees new skill
   Capability index         direct.py tools rebuild on install/      explicit
                              revoke; otherwise startup-only

   Surface that REQUIRES restart:
     ▸ Dynamic tools       loaded from DB once at startup
     ▸ Speaker gallery     loaded once at startup
     ▸ Persona / identity  loaded once at startup (or via reset-identity)
     ▸ API keys            from .env — no live reload
     ▸ Pipeline shape      stages list is fixed at build_pipeline
```

---

## 7. Filesystem State (`~/.heare/`)

```
══════════════════════════════════════════════════════════════════════════════════════
       ~/.heare/   (settings.HEARE_HOME)
══════════════════════════════════════════════════════════════════════════════════════

   PERSISTENT DATA (DB + JSON)
   ────────────────────────────────────────────────────────────────────────────────
   heare.db                SQLite (WAL mode), all events + transcripts
                             tables: transcripts, actions, action_log,
                                     usage_events, conversations, turns,
                                     dynamic_tools, allowed_directories,
                                     user_profile, decisions, events, heartbeats
   identity.json           {name, creature, vibe, emoji, tagline, generated_at}
                             — bootstrapped via OpenRouter on first run
                             — backed up on `heare reset-identity`
   speakers.json           SpeakerGallery — voiceprints + labels + turn counts
                             — populated by `heare enroll-owner` and namer task

   RUNTIME FLAGS (file existence is the signal)
   ────────────────────────────────────────────────────────────────────────────────
   heare.pid               Daemon PID (single-instance lock)
   heartbeat               Periodic alive timestamp
   provider                "openrouter" | "zai" — read by SwitchableLLMService
   mute.flag               Bot output muted when present
   mute_input.flag         Mic input muted when present
   mode                    Behavior mode: silent | focus | ambient
   .onboarded              Marker that onboarding wizard ran

   CACHED / DERIVED STATE
   ────────────────────────────────────────────────────────────────────────────────
   audio_event.json        {label, score, ts} — YAMNet detection (read by watch)
   browser_bridge.status   {connected, ts, port, pair_code, pair_remaining_s}
   browser_bridge.token    Convenience copy of token (mode 0600, in config.toml)
   capabilities.json       Snapshot of CapabilityIndex (skills + MCP + tools)
   heare_memory.db         SQLite FTS5 persistent memory (optional fastmcp server)
   session.json            Persistent Claude Code session ID (legacy)
   mcp.json                MCP server config (read by skills/mcp_utils)
   voice_state.json        {state, since_ts, last_partial, last_final} (auto-decay)
   skills/_marketplace/    Installed marketplace skills (currently 0)
   skills/<custom>/        User-authored skills (markdown procedures)
   inject/                 Drop directory for text-injection (.txt files)
   logs/                   daemon.log (rotating), indication.jsonl

   CONFIGURATION
   ────────────────────────────────────────────────────────────────────────────────
   config.toml             Optional override of Settings dataclass fields
                             (parsed by config.load_settings)
   workspace/              CLI/agent CWD; .mcp.json seeded from ~/.claude.json

   MODELS & EXTENSIONS
   ────────────────────────────────────────────────────────────────────────────────
   models/                 ML model artifacts (user-supplied)
      yamnet.onnx          YAMNet audio classifier (ONNX, mel-input variant, ~14MB)
   extensions/heare-bridge/  Chrome MV3 extension (sideloaded)
      manifest.json, background.js, offscreen.js, content_script.js, icons/, …
```

### SQLite schema highlights

```
   transcripts(ts, text, mode, speaker_id, speaker_confidence, language, ...)
   actions(ts, tool, args_json, result_json, status, exit_code, ...)
   action_log(ts, intent, decision, ...)        ← intents (legacy)
   usage_events(ts, provider, kind, value, ...)  ← tokens / chars / audio_seconds
   conversations(id, started_at, ended_at, summary, ...)
   turns(id, conversation_id, role, content, ...)
   dynamic_tools(name, sdk_name, description, definition_json, enabled)
   user_profile, allowed_directories, decisions, events, heartbeats
```

---

## 8. Common-Case Sequence Diagrams

### 8.1 Chitchat ("how are you?")

```
USER          mic     vad/turn      stt              gate     injector  user_aggr  llm        tts           speaker
 │              │        │           │                │           │         │        │          │              │
 ┝━━╾ "how…"━━━▶│        │           │                │           │         │        │          │              │
 │              ┝━━ raw audio ━━━━━━▶│                │           │         │        │          │              │
 │              │        ┝▶ UserStartedSpeakingFrame  │           │         │        │          │              │
 │              │        │           │ buffering...    │           │         │        │          │              │
 │              │        ┝▶ UserStoppedSpeakingFrame  │           │         │        │          │              │
 │              │        │  + smart-turn confirms 1.0s│           │         │        │          │              │
 │              │        │           ┝━━ Groq STT API ━━━━━╾ json │         │        │          │              │
 │              │        │           ┝▶ TranscriptionFrame────────▶ guards  │         │        │          │              │
 │              │        │           │                │  pass     │         │        │          │              │
 │              │        │           │                │  log txt  │         │        │          │              │
 │              │        │           │                │  forward──▶ build_  │        │          │              │
 │              │        │           │                │           │  for_…  │        │          │              │
 │              │        │           │                │           │  cap.q  │        │          │              │
 │              │        │           │                │           │  rewrite│        │          │              │
 │              │        │           │                │           │  prompt │        │          │              │
 │              │        │           │                │           │  forward▶ append │          │              │
 │              │        │           │                │           │         │ user   │          │              │
 │              │        │           │                │           │         │ emit ──▶ LLMContext│             │
 │              │        │           │                │           │         │        │  Frame   │              │
 │              │        │           │                │           │         │        ┝━ HTTPS ━━╾ stream chunks │
 │              │        │           │                │           │         │        ┝▶ LLMText * N─▶ logger     │
 │              │        │           │                │           │         │        │              │ scrub      │
 │              │        │           │                │           │         │        │              ▶ TTS service│
 │              │        │           │                │           │         │        │              │ websocket  │
 │              │        │           │                │           │         │        │              ┝▶ TTSAudio ─▶ speakers
 │◀━━━ audio ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┙
 │              │        │           │                │           │         │        │              │              │
                                                                  ★ usage_recorder logs metrics here
                                                                  ★ assistant_aggregator appends bot to LLMContext
```

### 8.2 Tool call ("read config.toml")

```
   ... same up to LLMContext message append ...
     ▶ SwitchableLLMService → delegate → emits FunctionCallInProgressFrame
     ▶ pipecat invokes registered handler → _execute_read("config.toml", settings)
        │
        ├── reads file → returns {success: true, output: "<contents>", ...}
        │  (or returns {success: false, error: ...} on failure)
        │
     ▶ result wrapped as another LLM context message → SECOND LLM round
     ▶ delegate emits final natural-language summary as LLMTextFrame stream
     ▶ logger / scrub / tts as usual
```

### 8.3 Cancel mid-speech ("stop") — ⚠ DEFECT VISIBLE

```
   bot is mid-utterance:    BotStartedSpeakingFrame already fired
   transcription_gate.bot_speaking = True

   user: "stop"
     │
     ▼
   stt emits TranscriptionFrame("stop")
     │
     ▼
   transcription_gate._handle_transcription:
     1. enrollment_active = False
     2. bot_speaking = TRUE  ←  GUARD HITS
     ┝━━━━━━━━ DROP (return) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ ⚠
     6. cancel-keyword check  ← NEVER REACHED
     7. forward                ← NEVER REACHED

   Net result: bot keeps speaking. User must wait until BotStoppedSpeakingFrame
   AND the 2.0s cooldown elapses before "stop" actually fires.

   FIX:  reorder so the cancel-keyword check runs BEFORE the bot_speaking guard.
         Add a small guard that ignores cancel-detection if the last bot text
         contained the same word (so the bot can't cancel itself).
```

---

## 9. Special Frame Paths

```
══════════════════════════════════════════════════════════════════════════════════════
                          NON-OBVIOUS FRAME ROUTING
══════════════════════════════════════════════════════════════════════════════════════

   ① InterruptionFrame UPSTREAM
        ┌───────────────────────────────────────────────────────────────┐
        │  transcription_gate detects cancel keyword                    │
        │      → push_frame(InterruptionFrame(), UPSTREAM)              │
        │  Pipecat routes SystemFrames immediately through every stage │
        │  (no per-stage queue). Two consumers:                         │
        │    a. tts_fade_observer → tts.cancel_pending() (50ms fade)    │
        │    b. SwitchableLLMService → cancel in-flight register_function│
        │       handler (raises CancelledError in the handler task)     │
        └───────────────────────────────────────────────────────────────┘

   ② Frame relay (SwitchableLLMService delegates)
        ┌───────────────────────────────────────────────────────────────┐
        │  Both delegates (_or_service, _zai_service) are NOT linked   │
        │  into the pipeline graph. Their _next/_prev are None.         │
        │  Frames they emit would be lost if not relayed.               │
        │                                                               │
        │  Wrapper monkey-patches each delegate's:                      │
        │    .push_frame  → wrapper.push_frame                          │
        │    .broadcast_frame → wrapper.broadcast_frame                 │
        │                                                               │
        │  The patch also observes LLMFullResponseEndFrame to clear    │
        │  the sticky-turn lock (_turn_in_flight=False).                │
        └───────────────────────────────────────────────────────────────┘

   ③ Greeting injection (bypasses user_aggregator)
        ┌───────────────────────────────────────────────────────────────┐
        │  _push_greeting() does:                                       │
        │    await llm_service.push_frame(TTSSpeakFrame(text))          │
        │  Pushed DOWNSTREAM from llm_service stage so it bypasses      │
        │  user_aggregator (which would treat unknown frames oddly).    │
        │  TTSSpeakFrame goes directly to scrub → tts.                  │
        └───────────────────────────────────────────────────────────────┘

   ④ Text injection (dashboard → daemon IPC)
        ┌───────────────────────────────────────────────────────────────┐
        │  Watch dashboard writes a .txt file into ~/.heare/inject/.   │
        │  Daemon background task run_injector_loop polls that dir.     │
        │  On new file: make_transcription_pusher emits a synthetic    │
        │  TranscriptionFrame(text=..., user_id="injected", language)  │
        │  pushed into transcription_gate → same path as STT output.   │
        └───────────────────────────────────────────────────────────────┘

   ⑤ allow_interruptions=False
        ┌───────────────────────────────────────────────────────────────┐
        │  PipelineParams in build.py:599 sets this False.              │
        │  Reason: bot's own audio leaking into mic would constantly    │
        │  trigger pipecat's NATIVE barge-in, preempting itself.        │
        │  Cancellation now flows ONLY via the explicit cancel-keyword  │
        │  fast path (which has the defect noted in §8.3).              │
        └───────────────────────────────────────────────────────────────┘

   ⑥ enable_turn_tracking=False
        ┌───────────────────────────────────────────────────────────────┐
        │  Smart-turn analyzer (V3) handles turn segmentation. The      │
        │  higher-level pipecat turn tracker would double-count.        │
        └───────────────────────────────────────────────────────────────┘
```

---

## 10. Background Tasks (asyncio)

```
══════════════════════════════════════════════════════════════════════════════════════
   Task                      Source                                  Lifetime
   ─────────────────────────────────────────────────────────────────────────────────
   pipeline_task             runner.run(pipeline)                    until EndFrame
                                                                      or cancel
   warmup_task               WarmupTask.run                           until .stop()
                                                                      keeps Edge TTS
                                                                      websocket warm
   namer_task                speaker_namer.run                        self-ending
                                                                      LLM-driven name
                                                                      inference
   greeting (one-shot)       create_task(_push_greeting)              ~1s sleep + push
   inject poller             create_task(run_injector_loop)           until cancel
                                                                      filesystem poll
   heartbeat                 (in indication / daemon helpers)          background ping
   identity bootstrap        ensure_identity (one-shot, blocking)     startup only
                                                                      OpenRouter call
   conversation hydration    conversation_manager.hydrate_action_log  startup only
                                                                      DB read + LLM
```

---

## 11. Watch Dashboard

```
══════════════════════════════════════════════════════════════════════════════════════
                  src/watch/   (separate Python process)
══════════════════════════════════════════════════════════════════════════════════════

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Files:                                                                  │
   │   __init__.py     run_watch(settings, interval, once)                  │
   │   app.py          HeareDashboard(App), bindings, refresh loop           │
   │   data.py         fetch_dashboard_state(settings) → DashboardSnapshot   │
   │                     reads heare.db (transcripts/actions/usage)          │
   │                     reads logs/daemon.log tail                          │
   │                     reads mute flags, provider file, identity, pid      │
   │   widgets.py      HeaderBar, ActivityTable, LogTail, ControlsBar        │
   │   screens.py      ModelSelectScreen (provider picker)                   │
   │   models.py       NamedTuple/dataclass schema                           │
   │   _legacy.py      Pre-Textual rich-live impl (rollback path)            │
   │   dashboard.tcss  CSS                                                    │
   └─────────────────────────────────────────────────────────────────────────┘

   Hotkeys → daemon control:
     s/x/r          → src/daemon/control:start/stop/restart_daemon
                      (subprocess: heare start/stop, kill/SIGTERM)
     m/M            → toggle ~/.heare/mute.flag / mute_input.flag
     p              → rewrite ~/.heare/provider (openrouter ↔ zai)
     t              → show input → src/pipeline/stages/text_injector.inject_text
                      (writes ~/.heare/inject/<ts>.txt)
     q              → quit
     left/right     → resize ActivityTable column
```

---

## 12. Configuration

```
══════════════════════════════════════════════════════════════════════════════════════
                            src/config.py:Settings
══════════════════════════════════════════════════════════════════════════════════════

   Loaded by:  load_settings()  →  config.toml + env vars + defaults

   Critical fields:
     groq_api_key, groq_language ("uk" by default — language HINT not lock)
     openrouter_api_key, openrouter_model
     zai_api_key, zai_model, zai_base_url
     provider_file (~/.heare/provider)
     mode_file, pid_file, mute_file, mute_input_file
     workspace_dir, log_dir, db_path, identity_file, speakers_file
     skills_paths, capabilities_file
     tts_voice, tts_sample_rate (24000)
     transcript_debounce_seconds, bot_speaking_cooldown_seconds (2.0)
     conversation_idle_seconds (1800), conversation_memory_enabled
     speaker_id_enabled, speaker_namer_enabled, …
     cancel_stop_words = ["stop", "cancel", "halt", "відміни", "отмени", "стоп"]
     indication.{enabled,sound_enabled,visual_enabled,notification_center_enabled}

   Dotfiles:
     ~/.heare/config.toml    user overrides
     .env                    secrets (API keys) — never in config.toml
     ~/.heare/heare.env      systemd service env
```

---

## 13. Key Defects (cross-reference to ARCHITECTURE_ANALYSIS.md)

```
   #3   Cancel keyword unreachable during bot speech            CRITICAL  §2 / §8.3
   #4   No tool timeouts                                        HIGH      §5
   #7   Unbounded recursive tool calls                          HIGH      §5
   #17  z.ai disabled permanently on first failure              HIGH      §2 (LLM)
   #19  No TTS fallback on Edge TTS failure                     HIGH      §2 (TTS)
   #1   Per-turn DB cost in system_prompt_injector              MEDIUM    §2
   #5   User feels ignored during long tool calls               MEDIUM    §5
   #8   One-turn lag on language switch                         MEDIUM    §2 (gate)
   #12  Static 2s feedback cooldown                             MEDIUM    §2 (gate)
   #15  Long pauses bloat audio buffer + STT cost               MEDIUM    §2 (transport)
   #24  Static prompt rebuilt every turn                        MEDIUM    §2 (injector)
```

---

## 14. Summary Cheatsheet

```
   INPUT   mic → VAD/smart-turn → input_mute_gate → [speaker_buffer] → stt
                → stt_error_observer → [speaker_tagger]
                → transcription_gate (★ feedback guard, lang hyst, cancel)
                → system_prompt_injector (rebuilds every turn)
                → user_aggregator → LLMContextFrame

   LLM     SwitchableLLMService (OpenRouter | z.ai, sticky-turn lock)
                ↳ tools: register_function fan-out to both delegates
                ↳ recursive same-turn rounds for tool calls
                → LLMTextFrame stream + LLMFullResponseEndFrame

   OUTPUT  assistant_response_logger (DB) → tts_scrub (mutates text)
                → tts (EdgeTTS + cache) → usage_recorder (DB metrics)
                → tts_fade_observer (50ms fade on Interrupt)
                → [sound_cue_processor] → mute_gate
                → transport.output → speakers
                → assistant_aggregator (back into LLMContext)

   STATE   LanguageState  ←→  transcription_gate / injector / tts / context
           LLMContext     ←→  injector / aggregators / llm
           CapabilityIndex ↔  injector / install tools
           Indication     ←→  observers / direct.py / lifecycle hooks
           heare.db       ←→  store / context_builder / dashboard / usage_recorder

   CONTROL CLI / dashboard → ~/.heare/{provider, mute*, mode} → live behavior
                         → SIGHUP → indication reload
                         → SIGTERM → graceful shutdown via run_until_stopped
```

---

## 15. Where to find things (post-refactor map)

```
   src/main.py                       daemon entry, CLI, supervisor
   src/config.py                     Settings dataclass, load_settings
   src/version.py                    __version__, app_version
   src/test_recognizer.py            dev script (NOT a test)

   src/agent/identity.py             persona bootstrap
   src/agent/llm/switchable.py       LLM service with provider hot-swap
   src/agent/llm/context_injector.py per-turn system prompt builder
   src/agent/llm/pricing.py          token / char / audio cost table
   src/agent/tools/registry.py       tool registry runtime cache
   src/agent/tools/schemas.py        ToolsSchema + register_all_tools
   src/agent/tools/direct.py         ~50 _execute_* implementations
   src/agent/tools/dynamic.py        DB-backed dynamic tool helpers
   src/agent/tools/capability_index.py FTS over skills+MCP+tools

   src/pipeline/build.py             build_pipeline composition root
   src/pipeline/__init__.py          re-exports build_pipeline
   src/pipeline/language_state.py    shared language singleton
   src/pipeline/stages/transcription_gate.py   pre-LLM gate
   src/pipeline/stages/mute_gate.py            in/out mute gates
   src/pipeline/stages/text_injector.py        ~/.heare/inject/ → frame
   src/pipeline/stages/text_scrub.py           tool-name regex
   src/pipeline/stages/tts_scrub_processor.py  pre-TTS scrub
   src/pipeline/stages/turn_aggregator.py      (legacy, gated off)
   src/pipeline/stages/usage_recorder.py       metrics → DB
   src/pipeline/stages/assistant_response_logger.py LLM text → DB

   src/voice/language/core.py        detect_language_from_frame, voice_for_language
   src/voice/language/detector.py    Claude-based detector + heuristic fallback
   src/voice/tts/edge.py             EdgeTTSService factory
   src/voice/tts/cache.py            TTSCache (LRU)
   src/voice/tts/phrases.py          FIXED_PHRASES warmup list
   src/voice/indication/core.py      Indication facade + cue processor
   src/voice/indication/assets.py    audio cue file table
   src/voice/indication/backends/    Sound, Visual, Notification
   src/voice/speaker/processor.py    speaker_buffer + speaker_tagger
   src/voice/speaker/id.py           ECAPA model wrapper
   src/voice/speaker/gallery.py      voiceprint store
   src/voice/speaker/namer.py        async LLM naming task

   src/store/storage.py              TranscriptStore (sqlite + WAL, ~1k LOC)
   src/store/conversation.py         ConversationManager
   src/store/context.py              ContextBuilder.build_for_generator
   src/store/user_profile.py         per-user prefs

   src/daemon/control.py             start/stop/restart helpers
   src/daemon/heartbeat.py           heartbeat + WarmupTask
   src/daemon/watch_controls.py      dashboard → daemon bridge
   src/daemon/browser.py             Chrome bridge lifecycle (start/stop)
   src/agent/browser_bridge.py       BrowserBridge WS server + pair-code logic

   src/audio_event/class_map.py      AUDIOSET_CLASSES (521 labels) + ALLOWLIST (17)
   src/audio_event/classifier.py     YamnetClassifier (ONNX wrapper, mel-input)
   src/audio_event/observer.py       AudioEventObserver pipeline stage
   src/audio_event/writer.py         Atomic JSON write to audio_event.json

   src/pipeline/stages/voice_state_observer.py  VoiceStateObserver pipeline stage

   src/skills/agent_skills.py        SkillsLoader for ~/.heare/skills/
   src/skills/marketplace.py         remote catalog client
   src/skills/installer.py           install_skill / create_skill / install_mcp
   src/skills/discovery.py           capability discovery
   src/skills/mcp_utils.py           ~/.heare/mcp.json read/write

   src/watch/                        Textual dashboard (separate process)
   extensions/heare-bridge/          Chrome MV3 extension (offscreen-doc RPC owner)
```

---

## 16. Chrome Extension Subsystem

```
══════════════════════════════════════════════════════════════════════════════════════
          extensions/heare-bridge/  (MV3, Chrome 109+, sideloaded)
══════════════════════════════════════════════════════════════════════════════════════

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Manifest & Permissions                                                  │
   │   manifest.json (v3):                                                   │
   │     - permissions: tabs, scripting, activeTab, storage, alarms,         │
   │       webNavigation, offscreen                                           │
   │     - minimum_chrome_version: "109"  (offscreen API requirement)        │
   │     - host_permissions: <all_urls> (no CSP block of chrome-extension://)│
   └─────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Service Worker (background.js)                                          │
   │   - Thin RPC dispatcher: chrome.runtime.onConnect listener for          │
   │     port "heare-rpc"                                                    │
   │   - Maintains HANDLERS map (8 methods):                                 │
   │       list_tabs, read_page, click, fill, extract, navigate,             │
   │       open_tab, activate_tab                                            │
   │   - chrome.alarms keepalive (redundant with offscreen ownership)         │
   │   - Badge management (green = connected, red = auth failed, grey = idle)│
   │   - Forwards storage_remove, open_options_page, reconnect msgs to       │
   │     offscreen via port.postMessage (chrome.storage proxy quirk)         │
   └─────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Offscreen Document (offscreen.html + offscreen.js)                      │
   │   - CRITICAL: owns the persistent WebSocket to 127.0.0.1:9333           │
   │   - Reason: MV3 service workers idle out within ~30s; WS gets dropped.  │
   │     Offscreen documents are NOT subject to SW lifetime rules.            │
   │   - Connects to SW via: chrome.runtime.connect({name:'heare-rpc'})      │
   │   - Keepalive: 24s ping + 35s pong watchdog (terminates WS on hang)     │
   │   - Wire protocol: {v:1, type:'auth'|'request'|'response'|'ping'|'pong'}│
   │   - Auth: token-authenticated; pair-code fallback (60s TTL, 5-attempt)  │
   │   - Token stored in: chrome.storage.local (persisted by browser)        │
   │   - Forwards handler results back to SW via rpc_response port message   │
   └─────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Popup & Options Pages                                                   │
   │   popup.html/js:        Status display + "Open Options" button          │
   │   options.html/js:      Token entry + pair-code entry UI                │
   │                         Sends reconnect message to SW on submit         │
   └─────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Connection Lifecycle                                                    │
   │   Daemon BrowserBridge.start() → WS server on 127.0.0.1:9333            │
   │   Chrome ext offscreen.js → WSS handshake + auth (token or pair-code)   │
   │   Token validation:                                                      │
   │     - WIRE_VERSION = 1                                                   │
   │     - CLOSE_AUTH_FAILED = 4001                                           │
   │     - CLOSE_ALREADY_CONNECTED = 4002 (refuse-new policy)                │
   │     - DEFAULT_RPC_TIMEOUT = 5.0s, LONG_RPC_TIMEOUT = 15.0s for          │
   │       screenshot, wait_for                                              │
   │   Pair-code flow:                                                        │
   │     - PAIR_CODE_TTL_S = 60.0                                             │
   │     - PAIR_CODE_REGEN_AFTER_LONELY_S = 30.0 (refreshed while lonely)    │
   │     - PAIR_MAX_ATTEMPTS = 5; PAIR_CODE_DIGITS = 6                        │
   │   Refuse-new policy: second client → close 4002 (avoid kick-war)        │
   └─────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ chrome.storage Proxy Quirk                                              │
   │   Issue: chrome.storage.local is NOT exposed to offscreen documents     │
   │   on every Chrome build (security boundary varies by version).           │
   │   Solution: offscreen proxies storage access through the SW via port    │
   │   messages: load_config, storage_remove, open_options_page             │
   │   Impact: all credential/token storage reads go through SW message      │
   │   handler, not direct document.storage API.                             │
   └─────────────────────────────────────────────────────────────────────────┘
```

---

## 17. Audio Event Detection (YAMNet)

```
══════════════════════════════════════════════════════════════════════════════════════
     src/audio_event/  + pipeline integration (opt-in, feature flag)
══════════════════════════════════════════════════════════════════════════════════════

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ YAMNet Classifier (src/audio_event/classifier.py)                       │
   │   - ONNX Runtime wrapper around mel-input YAMNet variant (~14 MB)        │
   │   - Input: 16kHz PCM, 0.96s window (15,360 samples) → log-mel patch    │
   │   - SAMPLE_RATE = 16000                                                 │
   │   - WINDOW_SAMPLES = 15360  (0.96s @ 16kHz)                             │
   │   - STFT params: 25ms frames, 10ms hop, 512 FFT, 64 mel bands           │
   │   - Output: 521-class softmax scores (AUDIOSET_CLASSES)                 │
   │   - Inference: single-threaded, ~5ms per window (benchmarked)           │
   │   - Failure: FileNotFoundError (model missing) → logged, returns None   │
   │   - Optional deps: onnxruntime, numpy → pyproject.toml audio-event extra│
   └─────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Class Map & Curation (src/audio_event/class_map.py)                     │
   │   - AUDIOSET_CLASSES: full 521 AudioSet ontology                        │
   │   - ALLOWLIST: 17-label curated subset for heare:                       │
   │       laughter, giggle, cry, cough, sneeze, sniff, snore, bark, meow,  │
   │       yowl, howl, yell, grunt, sigh, throat_clearing, scream, hiss     │
   │   - Inference outputs scores; filter against allowlist before emitting  │
   └─────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Pipeline Integration (src/audio_event/observer.py)                      │
   │   - Pass-through FrameProcessor: every frame forwards unchanged          │
   │   - Offload inference to asyncio.to_thread (non-blocking to audio loop) │
   │   - Drop-on-busy policy: while task._running=True, new windows dropped  │
   │   - 2-window confirmation rule: emit only after seeing label 2x in a row│
   │   - Config settings (src/config.py):                                    │
   │       audio_event_detection_enabled: bool = False  (feature flag)       │
   │       audio_event_threshold: float = 0.4  (confidence cutoff)           │
   │       yamnet_model_path: Path = ~/.heare/models/yamnet.onnx (user-orig) │
   │       audio_event_file: Path = ~/.heare/audio_event.json                │
   │   - Failure modes (factory returns None + WARNING log):                 │
   │       1. feature flag off → silent no-op                                │
   │       2. onnxruntime not installed → install hint                       │
   │       3. model file missing → tf2onnx hint                              │
   │       4. exception in _infer → logged, _running resets, continue        │
   └─────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Output State (src/audio_event/writer.py)                                │
   │   - Writes: ~/.heare/audio_event.json {label, score, ts}  (atomic)      │
   │   - Read by: watch dashboard (src/watch/data.py:read_audio_event)       │
   │   - Rendered by: VoiceStateBar widget with 5s TTL auto-decay            │
   │   - File format: single JSON object (not JSONL), overwritten per event  │
   └─────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │ Voice-State Observer (src/pipeline/stages/voice_state_observer.py)      │
   │   - NEW: writes ~/.heare/voice_state.json on every STT state change    │
   │   - States: idle, listening, stt, result                                │
   │   - Schema: {state, since_ts, last_partial, last_final}                │
   │   - Auto-decay: dashboard-side 4s timer (state="result" → "idle")      │
   │   - Read by: VoiceStateBar widget for visual feedback                  │
   └─────────────────────────────────────────────────────────────────────────┘
```

---

## END

This diagram should remain accurate as long as the package shape from `8cb49ef`
holds. If files move or major stages are added/removed, regenerate from
`build_pipeline` (the composition root in `src/pipeline/build.py`) and the
shared-state graph in §3.
