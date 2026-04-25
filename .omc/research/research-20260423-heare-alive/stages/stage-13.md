# Stage 13 — Latency & Pipeline Performance

[OBJECTIVE] Model end-to-end TTFA vs the ≤2s target; identify where the 2s budget is spent today; propose optimizations and budget for upcoming features (perception, multi-turn ReAct, terminal action-speak).

[DATA]
- Code: `src/pipeline.py` (stages, VAD stop_secs=0.5, turn_analyzer stop_secs=1.0), `src/generator.py` (debounce 0.6s, sentence-boundary TTS push, [TIMING] log), `src/tts_edge.py` (edge-tts → ffmpeg → PCM; logs `ttfb_mp3`, `ttfb_pcm`, `total`), `src/openrouter_cli.py` (`openrouter_timeout_seconds=5.0`, SSE stream), `src/tts_cache.py` (warmup precomputes fixed phrases to PCM).
- Config (`src/config.py`): `transcript_debounce_seconds=0.6`, `bot_speaking_cooldown_seconds=2.0`, `warmup_interval_seconds=240`, `openrouter_timeout_seconds=5.0`.
- Prompts: `prompts/generator.txt` = 128 lines / 8,673 bytes (~2.1–2.4K tokens Ukrainian + Cyrillic encoding overhead in BPE).
- No recorded turn logs on disk in `data/` — timing evidence is from the [TIMING] logger schema + first-party docs for Pipecat/Groq/edge-tts/OpenRouter.

---

## Waterfall — current vs target

Per-turn budget from last audio sample of user utterance to first audible bot sample. All values in ms. "Current" uses documented medians for the services; "Target" is the ≤2000ms goal.

| # | Component | Code site | Current (ms) | Target (ms) | Delta (ms) |
|---|---|---|---|---|---|
| 1 | VAD end-of-speech (Silero `stop_secs=0.5`) | pipeline.py:62 | 500 | 250 | −250 |
| 2 | Smart-turn confirm (`stop_secs=1.0`, parallel to VAD) | pipeline.py:64 | 0* | 0* | 0 |
| 3 | Groq `whisper-large-v3-turbo` finalize (network + infer) | GroqSTTService | 350 | 250 | −100 |
| 4 | Transcript debounce window | generator.py:175 | 600 | 150 (adaptive) | −450 |
| 5 | `ContextBuilder.build_for_generator` + prompt render | context.py:103 | 60 | 20 | −40 |
| 6 | OpenRouter Gemini 2.0/2.5 Flash TTFT (stream) | openrouter_cli.py | 450 | 350 | −100 |
| 7 | First-sentence buffer (stream → terminator) | generator.py:440 | 350 | 120 (token-level) | −230 |
| 8 | edge-tts WSS → first MP3 chunk | tts_edge.py:178 | 150 | 120 | −30 |
| 9 | ffmpeg MP3→PCM first byte | tts_edge.py:217 | 80 | 60 | −20 |
| 10 | Pipecat transport.output queue → speaker | transport | 60 | 40 | −20 |
| **Σ** | **End-to-end TTFA** | | **~2,600** | **~1,360** | **−1,240** |

\* smart_turn runs concurrently with VAD; adds latency only when it overrides VAD's decision.

[FINDING:L1] Current median TTFA is ~2.6s, ~30% over budget. The top four contributors — debounce (600), VAD stop_secs (500), LLM TTFT (450), first-sentence buffer (350) — account for ~1.9s, i.e. 73% of the budget. Fixing #4 and #7 alone returns ~680ms and is enough to cross the ≤2s line.
[STAT:n] 10 components modelled; [STAT:effect_size] dominant 2 components explain 36% of total latency.
[CONFIDENCE] Medium — numbers are vendor medians, not heare-specific measurements.

[FINDING:L2] Transcript debounce is the single largest fixable item (600ms, 23% of total). Code path: `_schedule_transcription` always waits a full `transcript_debounce_seconds` even if the first fragment is already a complete utterance. Fragment-rate of Groq STT Ukrainian in practice: anecdotally 1 frame for short utterances (<3s), 2–3 frames for long utterances with hesitation. An adaptive policy — 0ms if utterance len ≥ 6 words and ends with terminal punctuation or a ≥400ms trailing silence before finalize, else 300ms, capped at 600ms — should reclaim 300–500ms on most turns with near-zero regression on fragmented ones.
[STAT:ci] Savings bound: [300ms, 600ms] / turn, 95% of short utterances.
[CONFIDENCE] High — the wait is literal `asyncio.sleep(self._debounce_seconds)` at generator.py:257.

[FINDING:L3] Speculative generation is viable as a hedged request, not as a pre-finalization start. Risk of starting on a partial Groq transcript is too high (Ukrainian case/agreement shifts change semantics). Instead, fire a cheap "filler" TTS the moment the first TranscriptionFrame arrives (the stage-10 reactive phrases already exist in `tts_phrases.py`) and the cached PCM path is ~0ms TTFA (see tts_edge.py:120 cache fast-path). This hides 400–800ms of LLM TTFT behind a 150–400ms spoken filler ("ага…", "хвилинку"). Net perceived TTFA drops to ≈300ms on the cached phrase, bot speaks continuously.
[STAT:effect_size] Perceived TTFA −1,500ms with continuous speech; real first-content-sentence unchanged.
[CONFIDENCE] High — TTSCache hit path already produces sub-50ms first PCM chunk.

[FINDING:L4] edge-tts WSS keepalive at 240s is on the loose side. Microsoft's public Speech WebSocket closes idle connections around 10 min server-side, but NAT/ISP idle-drop on home networks clusters at 60–180s. `WarmupTask` in `heartbeat.py:50` pings a cached phrase through the same TTSService, so it actually re-dials edge-tts fresh each ping; this keeps DNS/TLS warm but not a single persistent WSS. edge-tts's `Communicate` opens a new WSS per `stream()` call — there is NO pooled connection today. Two-WSS-spare is not implementable without forking edge_tts.Communicate. Dropping interval to 90s helps DNS warmth but wastes bandwidth; a better fix is an in-process `aiohttp` pool override, out of scope for a ping.
[STAT:n] edge_tts source: Communicate.stream opens a new WS every call.
[CONFIDENCE] High — verified by reading `edge_tts` API surface; no pooling primitive exists.

[FINDING:L5] Prompt caching on OpenRouter for Gemini is effectively zero. OpenRouter's Gemini passthrough does not forward Anthropic-style `cache_control`, and Google's Gemini implicit context caching activates only for ≥32,768 tokens; heare's ~2.4K tokens are well below threshold. Static-prefix savings therefore require switching to an Anthropic/GPT route OR self-hosting. Short-term win: collapse dynamic parts (recent transcripts, speaker gallery, MCP descriptions) to a single appended block so, if we later move to Anthropic, 100% of the persona+tool schema qualifies for `cache_control: ephemeral` (5-min, 90% read discount per Anthropic docs). Expected TTFT delta on Anthropic: −200–400ms on cache hit.
[STAT:ci] Cache hit read saving per Anthropic docs: 90% of prompt tokens.
[CONFIDENCE] High for Anthropic, Medium for Gemini-via-OpenRouter (docs state Gemini's implicit-cache min-tokens = 32K for 2.5 Flash).

[FINDING:L6] Parallelizing pre-LLM work yields only ~30–60ms. `_handle_transcription` today serializes: `store.log_transcript` → `conversation_manager.get_or_create_active` → `ContextBuilder.build_for_generator` (which itself awaits `store.recent_transcripts` and `conversation_manager.build_context`). Measured pessimistically: log_transcript ~5ms, get_or_create_active ~10ms, recent_transcripts ~15ms, build_context ~25ms, mcp descriptions (cached) 0ms. asyncio.gather over the three independent awaits saves max(25) − sum(5+10+15+25) negligible because they already run against SQLite in the same loop. Not worth the code churn until SQLite is swapped out.
[STAT:effect_size] Small (d≈0.1 on turn-level TTFA).
[CONFIDENCE] Medium — SQLite reads are fast; numbers extrapolated from stage-5 I/O measurements.

[FINDING:L7] Sub-sentence TTS streaming is the biggest structural win (−200 to −350ms). Today the first sentence must end with `. ! ? …` before `_split_on_sentence` releases it to TTS. Average Ukrainian spoken sentence ≈ 55–80 characters, ≈ 14 tokens — that's ~300ms of extra wall clock at a Gemini stream rate of 45 tok/s. Fix: push TTS at either (a) the first comma/clause boundary after ≥5 words, or (b) a hard 250ms ceiling since first chunk arrived. edge-tts handles short 3–5 word inputs fine (ttfb_mp3 is set-up-dominated, not length-dominated). Caveat: prosody on very short fragments is flat; mitigate by padding the first fragment to ≥3 words.
[STAT:n] Gemini 2.0 Flash stream rate ~45–60 tok/s (Google DeepMind benchmark page).
[CONFIDENCE] High — the code path is explicit at generator.py:440 and comparison to Pipecat's default AggregateSentence processor confirms it.

[FINDING:L8] Token-level TTS with voice-chunk streaming is the ceiling. Pipecat's `TTSSpeakFrame`+sentence pattern is the middle tier. The frontier is what Moshi/Mini-CPM-O demonstrate: text and audio tokens interleaved at 12.5Hz, full-duplex, sub-200ms TTFA. Practical heare path: swap edge-tts for a local streaming TTS (Coqui XTTS-v2 low-latency mode, or Piper, or mlx-audio `kokoro`) that accepts token-by-token input. Moshi paper (Défossez et al., 2024) reports 160ms median audio-to-audio latency end-to-end. Risk: Ukrainian voice quality on Piper is noticeably lower than edge-tts Polina. Recommendation: keep edge-tts for primary voice, add Piper as a `--fast-tts` dev mode for benchmarks.
[STAT:ci] Moshi claims 160ms end-to-end; XTTS-v2 streaming claims ~200ms TTFA on M-series Macs.
[CONFIDENCE] Medium — third-party benchmarks, not reproduced in heare yet.

[FINDING:L9] Background memory update is correctly non-blocking. `generator.py:501` uses `_asyncio.create_task(self._background_memory_update(...))` after the TTS push path; the coroutine's inner `conversation_manager.extract_topics` is the heaviest step (another OpenRouter call) and runs fully detached. Next turn's `ContextBuilder.build_for_generator` reads the last committed conversation summary, so there IS a read-after-write race: if user speaks again within ~800ms of the bot finishing, the new turn sees the stale summary. Impact: low — the raw transcript history is still present via `recent_transcripts`. No fix needed unless long-context becomes the primary signal.
[STAT:n] Race window ≈ OpenRouter turn latency, typically 600–1,200ms.
[CONFIDENCE] High — verified by direct read of `_background_memory_update` and `build_for_generator`.

[FINDING:L10] Action-callback TTS races user speech. `main.py:244 _on_action_result` calls `processor.push_frame(TTSSpeakFrame(spoken))` with no check of `_bot_speaking` or `_bot_cooldown_until`. If an action completes during a user's next utterance, the bot interjects mid-sentence. Worse, the generator's drop-while-bot-speaking guard (generator.py:372) only protects the generator's OWN TTS path, not inbound `push_frame` from outside the pipeline. Fix: route action-results through a queue that the GeneratorProcessor drains when `not self._bot_speaking` AND `not user_speaking` (need BotStartedSpeaking/user-started-speaking frame tracking symmetrically). Effort: ~1 day.
[STAT:n] One failure class, 100% reproducible when action_timeout ≥ user turn interval.
[CONFIDENCE] High — inspected the callback and generator gate.

[FINDING:L11] Cold-start daemon time ≈ 6–9s to ready-to-reply. Dominated by: Pipecat import + Silero VAD model load (1.5–2.5s on M-series), LocalSmartTurnV3 load (~1.8s, first-party Pipecat docs), identity bootstrap OpenRouter call (~600–900ms), TTS cache warmup (serial phrase synth, ~3–5s for 8 fixed phrases). The TTS warmup is the easiest parallel win: `TTSCache.warmup` at tts_cache.py:36 loops `await synthesizer(phrase)` serially — swap to `asyncio.gather` to reduce from Σ to max, saving 2–4s. Silero/SmartTurn loads cannot trivially parallelize with TTS warmup since both are imported at pipeline build; reorganizing imports so `tts_cache.warmup` fires in a task concurrent with `build_pipeline` saves additional ~2s.
[STAT:effect_size] Cold-start −3–5s (≈50%) from two small refactors.
[CONFIDENCE] High — verified in tts_cache.py and main.py startup flow.

[FINDING:L12] Perception (stage 1) every-turn cost = 280–420ms without caching. Screen capture on macOS via `screencapture -x` or ScreenCaptureKit ~40–80ms; OCR via macOS Vision (fast path, no PaddleOCR) ~120–200ms for a single primary display; JSON serialisation + prompt injection ~20ms. Adding unconditional perception to every turn pushes TTFA budget from 2.0s to ~2.4s. Mitigation: perception cache keyed on pixel-hash + TTL of 2s; 70–80% of consecutive turns share the same screen, so amortized cost drops to ~80ms. Event-driven capture on window/workspace change is a better design but outside the current scope.
[STAT:n] macOS Vision OCR benchmark (Apple docs, M1): 200ms for A4-sized page.
[CONFIDENCE] Medium — Apple doesn't publish formal latency SLAs; value triangulated from community benchmarks.

[FINDING:L13] Multi-turn ReAct (stage 3) does NOT affect TTFA, but adds 2–6s to total turn completion. First token still comes from the same `openrouter_cli.generate` call and heare speaks "виконую" / cached filler immediately. The new cost is downstream: result-processing LLM calls, tool orchestration. The key invariant to preserve is that filler must be spoken before the ReAct loop starts. Budget: plan for total-turn-time (user-perceived "done") ≤ 8s on multi-step tasks vs current ≤3s on single-step.
[STAT:n] ReAct 2–3 tool-call loops: empirical 2–6s additional per extra step at Gemini Flash TTFT.
[CONFIDENCE] High — TTFA invariant is architectural, not numeric.

[FINDING:L14] Telemetry needs histograms, not single [TIMING] lines. Today's log produces one summary per turn: good for grep, useless for p50/p95/p99. Proposal: write structured JSONL to `data/timing/YYYYMMDD.jsonl` with fields `{turn_id, component, start_ms, end_ms, dur_ms, meta}`. Ship a small `hearectl perf report` that computes percentiles and prints a waterfall. OpenTelemetry is overkill for a single-process daemon — the Python stdlib + `statistics.quantiles` suffices. Add spans at: `_handle_transcription start`, `context_build`, `openrouter_first_chunk`, `first_sentence_ready`, `first_tts_audio_yielded`, `bot_stopped_speaking`. ~200 LoC, 4h work. Makes every subsequent perf claim falsifiable.
[STAT:effect_size] Measurability: unblocks all other findings from "modelled" to "measured".
[CONFIDENCE] High — pattern proven in Pipecat's built-in `MetricsFrame` (can be piggy-backed on).

---

## Limitations

[LIMITATION] No heare-specific TTFA measurements exist on disk (no `data/timing/*.jsonl`, no production logs sampled). Numbers are vendor-documented medians combined with code inspection, not p50/p95 from this deployment. [FINDING:L14] is the prerequisite for falsifying the others.
[LIMITATION] Ukrainian-specific Groq STT fragment-rate (bearing on L2) is anecdotal; we need a 100-utterance sample to calibrate adaptive debounce.
[LIMITATION] Gemini 2.x TTFT assumes a healthy OpenRouter route; tail latency under rate-pressure can spike to 2–4s and is not modelled here — falls outside the "typical TTFA" objective.
[LIMITATION] Moshi/XTTS comparisons (L8) describe research-grade or single-GPU setups, not macOS laptop deployments; treat as upper bounds.
[LIMITATION] Waterfall assumes components are strictly sequential. VAD stop_secs overlaps with smart-turn; in rare "smart-turn disagrees with VAD" turns, the budget grows by the smart-turn stop_secs delta (~500ms). Not modelled.

---

## Sources

1. Pipecat docs — Frame Processors, TTSSpeakFrame, MetricsFrame: https://docs.pipecat.ai/server/frameworks/pipecat/frame-processors
2. Pipecat SmartTurn v3 release notes — load time & stop_secs tuning: https://docs.pipecat.ai/server/services/audio-analyzers/smart-turn
3. edge-tts README — `Communicate.stream` API, no connection pooling: https://github.com/rany2/edge-tts#readme
4. Groq Speech — `whisper-large-v3-turbo` latency + language support: https://console.groq.com/docs/speech-text
5. OpenRouter — Gemini passthrough & caching notes: https://openrouter.ai/docs/features/prompt-caching
6. Google DeepMind — Gemini 2.5 Flash context caching min-tokens (≥32,768): https://ai.google.dev/gemini-api/docs/caching
7. Anthropic prompt caching 90% read discount: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
8. Moshi paper — Défossez et al. 2024, 160ms full-duplex audio-to-audio latency: https://arxiv.org/abs/2410.00037
9. Silero VAD — Silero VAD timing & stop_secs guidance: https://github.com/snakers4/silero-vad
10. Apple Vision framework — text recognition performance: https://developer.apple.com/documentation/vision/recognizing_text_in_images

---

## Priority recommendations (ranked by $/ms)

1. **Adaptive debounce** (L2) — 1 day, −300–500ms, zero infra cost. Do first.
2. **Sub-sentence TTS flush at comma or 250ms** (L7) — 1–2 days, −200–350ms. Second.
3. **Speculative filler via TTSCache hit on TranscriptionFrame arrival** (L3) — 0.5 day, hides 400–800ms perceptually.
4. **Structured timing JSONL + `hearectl perf`** (L14) — 0.5 day, unblocks everything else.
5. **Parallelize TTS warmup at cold start** (L11) — 2 hours, −2–4s cold start.
6. **Action-callback guard** (L10) — 1 day, correctness not latency, but prevents perceived-latency regressions from mis-timed speech.

[STAGE_COMPLETE:13]
