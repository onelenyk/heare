# Heare Quality Standard

A measurable quality maturity model for the heare AI voice assistant.
Based on Microsoft Agentic AI Maturity Model, ContextOS Harness Audit,
Moai Agentic Product Standard, and Production AI Institute PSF.

---

## How to use

Each dimension has 5 levels (L0–L4). Score honestly against evidence — not intent.
Progress is tracked in `.sisyphus/quality.json`.

- **L0** — Absent. No controls, no measurement.
- **L1** — Initial. Works on happy path, breaks at edge cases.
- **L2** — Defined. Measured, documented, repeatable.
- **L3** — Managed. Proactive, risk-calibrated, auto-recovery.
- **L4** — Optimized. Self-correcting, continuously verified.

---

## Dimension 1: Reliability

*Does the daemon stay up? Does the pipeline survive failures?*

| Level | Criteria | Heare Status |
|-------|----------|-------------|
| **L0** | Crashes silently. No restart. No health check. | — |
| **L1** | PID-based start/stop. Crashes restart manually. | ✅ Current |
| **L2** | Health endpoint. Crash detection. Graceful degradation on STT/LLM failure. | ⚠️ Partial |
| **L3** | Auto-restart on crash. Circuit breakers for external APIs. Fallback LLM provider. | ❌ |
| **L4** | Self-healing. Predictive failure detection. Zero-downtime upgrades. | ❌ |

**Heare current: L1** — daemon lifecycle works, but no health monitoring, no auto-restart, no circuit breakers.

---

## Dimension 2: Safety

*Does it confirm before destructive actions? Are tools properly gated?*

| Level | Criteria | Heare Status |
|-------|----------|-------------|
| **L0** | No confirmation. Any tool call executes immediately. | — |
| **L1** | Basic yes/no confirmation for destructive tools. Verbal passphrase support. | ✅ Current |
| **L2** | Tool risk classification (read/write/execute). Mode-based tool denial. Confirmation timeout with auto-cancel. | ⚠️ Partial |
| **L3** | Permissions enforced in code, not prompt. Sandbox execution. Input validation before LLM. Output scrubbing before TTS. | ❌ |
| **L4** | Red-team tested. Prompt injection defense. Adversarial testing suite. Reversible actions with rollback. | ❌ |

**Heare current: L1** — verbal confirmation works, mode gating exists, but no code-level permission enforcement.

---

## Dimension 3: Voice Interaction Quality

*Is the voice experience fast, natural, and responsive?*

| Level | Criteria | Heare Status |
|-------|----------|-------------|
| **L0** | >5s latency. Robotic TTS. No barge-in. | — |
| **L1** | <2s TTFB. Acceptable TTS. Basic VAD turn-taking. | ✅ Current |
| **L2** | <800ms A2A latency. Natural TTS with language switching. Barge-in/interruption support. Sound cues for listening/speaking. | ⚠️ Partial |
| **L3** | <500ms A2A. Streaming TTS (speak while generating). Adaptive turn-taking. Echo cancellation verified. | ❌ |
| **L4** | <300ms A2A. Emotion-aware TTS. Predictive turn-taking. Multi-speaker handling. | ❌ |

**Heare current: L1-L2** — TTS works with language switching, barge-in exists, echo cancellation active. Latency depends on LLM provider.

---

## Dimension 4: Intelligence

*Tool selection accuracy, memory quality, decision quality.*

| Level | Criteria | Heare Status |
|-------|----------|-------------|
| **L0** | Random tool selection. No memory. Hallucinates freely. | — |
| **L1** | Function-calling works. Basic memory (transcripts only). | ✅ Previous |
| **L2** | Semantic memory with search. Auto-extraction. Capability hints. <4 tool calls per turn (enforced). | ✅ Current |
| **L3** | Memory consolidation (dedup, decay). Intent routing before LLM. Tool selection accuracy >90%. Eval-driven prompt engineering. | ❌ |
| **L4** | Predictive memory fetch. Learned preferences. Self-improving tool selection. A/B tested prompts. | ❌ |

**Heare current: L2** — memory system shipped, auto-extraction works, tool cap enforced. No eval-driven development yet.

---

## Dimension 5: Observability

*Can we see what's happening? Debug failures? Track quality over time?*

| Level | Criteria | Heare Status |
|-------|----------|-------------|
| **L0** | No logs. No metrics. Black box. | — |
| **L1** | Basic logging (daemon.log). Status command shows running/stopped. | ✅ Current |
| **L2** | Structured events (emit). Cost tracking per turn. Tool call success/failure tracking. Dashboard shows transcript + usage. | ⚠️ Partial |
| **L3** | Traces with correlation IDs. Latency histograms (P50/P95/P99). Error rate dashboards. Alerting on regression. | ❌ |
| **L4** | Real-time monitoring. Anomaly detection. A/B experiment tracking. Trace replay for debugging. | ❌ |

**Heare current: L1-L2** — logging and events exist, cost tracked, dashboard works. No structured telemetry or alerting.

---

## Dimension 6: Testing

*Are we protected against regressions? How thorough is coverage?*

| Level | Criteria | Heare Status |
|-------|----------|-------------|
| **L0** | No tests. Manual verification only. | — |
| **L1** | Unit tests for core modules. Coverage <50%. | ✅ Current |
| **L2** | >80% coverage on critical path. Integration tests for pipeline. Tests block CI. Golden eval set for prompts. | ⚠️ Partial |
| **L3** | >90% coverage. Load/stress tests. Fuzzing for tool inputs. Regression suite run on every change. | ❌ |
| **L4** | Chaos engineering. Canary deployments with automated rollback. Continuous eval against production data. | ❌ |

**Heare current: L1-L2** — 1,201 tests pass, pipeline has integration tests, no golden eval set for LLM behavior.

---

## Heare Current Score

| Dimension | Level |
|-----------|-------|
| Reliability | L1 |
| Safety | L1 |
| Voice Interaction | L1-L2 |
| Intelligence | L2 |
| Observability | L1-L2 |
| Testing | L1-L2 |

**Overall: L1-L2 (Initial → Defined)**

---

## Next Level Targets (L2 → L3)

Priority order for reaching L3:

1. **Observability L2→L3**: Structured traces with correlation IDs. Latency histograms. Error rate tracking.
2. **Reliability L2→L3**: Health endpoint. Circuit breakers for STT/LLM. Auto-restart.
3. **Safety L2→L3**: Code-level permission enforcement. Input validation. Output scrubbing verification.
4. **Intelligence L2→L3**: Golden eval set. Eval-driven prompt engineering. Intent routing.
5. **Testing L2→L3**: >90% coverage on critical path. LLM eval set with ≥50 examples per failure mode.

---

## Tracking

```bash
# View current quality score
cat .sisyphus/quality.json

# Update after improvements
# Edit .sisyphus/quality.json manually with new evidence
```

---

## References

- [Microsoft Agentic AI Maturity Model](https://learn.microsoft.com/en-us/agents/adoption-maturity-model/)
- [Moai Agentic Product Standard](https://github.com/Moai-Team-LLC/agentic-product-standard)
- [ContextOS Agent Harness Audit](https://contextosai.com/blog/eight-property-harness-audit)
- [Production AI Institute PSF Checklist](https://www.productionai.institute/insights/ai-agent-production-ready-checklist)
- [Google Agent Evaluation Framework](https://cloud.google.com/blog/topics/developers-practitioners/a-methodical-approach-to-agent-evaluation)
- [Anthropic Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [CallSphere Voice Agent Quality Metrics 2026](https://callsphere.ai/blog/voice-agent-quality-metrics-wer-latency-grounding)

---

## Appendix: Voice Production Targets

Concrete numbers to measure against. From industry benchmarks and production voice deployments.

| Metric | Target (p50) | Target (p95) | Source |
|--------|-------------|-------------|--------|
| Mouth-to-ear latency | 800ms | 1500ms | Twig/Twilio/SignalWire consensus |
| STT Word Error Rate | — | < 3% | CallSphere |
| STT Time-to-final-segment | 250ms | 350ms | Pipecat STT benchmark |
| LLM Time-to-first-token | 400ms | 700ms | Twig latency budget |
| TTS Time-to-first-audio | 200ms | 350ms | Industry standard |
| Barge-in success rate | > 97% | — | CallSphere |
| Interruption recovery | > 93% | — | CallSphere |
| Connection drops | < 4 per 1000 sessions | — | Production standard |
| Tool call success rate | > 95% | — | Production standard |
| Containment rate | 68% baseline / 84% mature | — | Industry benchmark |

**Latency rule of thumb**: <700ms = natural, 700-1200ms = acceptable, >1500ms = broken, >2000ms = unacceptable.
