# Phase 1 — Live Smoke Verification

Branch: `s2s-realtime`
Date: 2026-04-18

## Infrastructure verification (automated, complete)

- ✅ Daemon boots with `generator_mode = true` in `~/.heare/config.toml`
- ✅ Pipeline stages wired correctly (from `daemon.log`):
  ```
  transport.input() → GroqSTTService → GeneratorProcessor → EdgeTTSService → transport.output()
  ```
- ✅ `Generator mode enabled: model=google/gemini-3.1-flash-lite-preview-20260303, timeout=5.0s`
- ✅ TTS cache warm (7 phrases including `Хвилинку, щось не так.` fallback)
- ✅ Heartbeat + warmup tasks started, targeting generator with no-op methods
- ✅ Startup greeting push no longer races StartFrame (fixed via
  `asyncio.create_task` + 1s delay — no more
  `Trying to process TTSSpeakFrame but StartFrame not received yet` ERROR)
- ✅ `make test` 507/507 green
- ✅ `make lint` clean
- ✅ Daemon up-time in background confirms no crashes in first 60s

## Human-in-the-loop (pending user validation)

These steps require the operator to speak into the microphone:

1. Speak 5 varied test utterances in Ukrainian (e.g., "привіт", "як справи",
   "розкажи анекдот", "скільки зараз часу", "дякую")
2. Between each utterance, wait for bot reply to finish
3. After the session, inspect `daemon.log` for lines matching:
   ```
   [TIMING] generator transcript="..." ttft=XXXms total_chunks=N
   ```
4. Compute median `ttft` across the 5 turns — target ≤ 2000ms
5. Subjectively assess reply quality (Ukrainian coherence, natural voice)

## Baseline expectations

Based on `.omc/benchmarks/phase1-openrouter.md`:
- OpenRouter warm TTFT median: ~1131ms
- Plus STT (Groq Whisper): ~800-1500ms
- Plus TTS TTFB (Edge): ~700-900ms
- **Total time-to-first-audio estimate: ~1800-2500ms**

Compared to legacy decider path (7-13s), Phase 1 should cut TTFA by 3-5×
even on a realistic live run.

## Architect ITERATE items — status

- ✅ Issue 1: PRD notes amended with ADR-002 voiding of US-P1-02 v2 ACs
- ✅ Issue 2: `decider` → `processor` renamed across `src/main.py`
- ✅ Issue 3: Greeting race fixed — `asyncio.create_task` + 1s delay
- ✅ Issue 4: `GeneratorCLI` → `OpenRouterCLI` rename annotated in plan
- ✅ Issue 5: `CancelledError` propagation documented in `src/generator.py`

## Outcome

**Phase 1 infrastructure complete. Ready for user voice testing.**

Next steps after user validates:
1. User speaks 5 utterances, confirms median TTFT ≤ 2000ms
2. If green: commit + merge `s2s-realtime` to `main`
3. Open Phase 2.1 PRD (`phase2-intent-queue.md`)
4. Remove `generator_mode` flag in Phase 2.1 per US-P1-08 milestone
