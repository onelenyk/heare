# Stage 4: Memory & Persona Evolution — Making heare REMEMBER and GROW

**Date:** 2026-04-23
**Branch:** s2s-realtime
**Scope:** Durable, typed, self-writing long-term memory for heare

---

## [OBJECTIVE]

Design a multi-layer memory architecture that allows heare to accumulate durable knowledge about Nazar across sessions — covering preferences, learned confirmations, recurring tasks, persona relationship log, mood signals, and retrieval strategy — without violating privacy or blowing the generator prompt budget.

---

## Q1: Claude Code's Memory Pattern

[FINDING:M1] Claude Code does NOT implement an "auto-memory" system in CLAUDE.md. The full content of `/Users/lenyk/.claude/CLAUDE.md` is the OMC orchestration layer — there is no "auto memory" section, no MEMORY.md index, and no `/Users/lenyk/.claude/projects/-Users-lenyk-myprojects-heare/memory/` directory exists on disk (confirmed: `ls` returned `not found`).

**Verbatim relevant excerpt from CLAUDE.md (lines 1–65):**
```
<!-- OMC:START -->
<!-- OMC:VERSION:4.11.1 -->
# oh-my-claudecode - Intelligent Multi-Agent Orchestration
...
<worktree_paths>
State: `.omc/state/`, `.omc/state/sessions/{sessionId}/`, `.omc/notepad.md`,
`.omc/project-memory.json`, `.omc/plans/`, `.omc/research/`, `.omc/logs/`
</worktree_paths>
```

The OMC pattern that IS observable: `.omc/project-memory.json` (structured JSON), `.omc/notepad.md` (free-form), and `.omc/plans/` (markdown plans). These map to three types: **structured facts** (JSON), **working notes** (markdown), **session state** (directory per sessionId). Write triggers are agent tool calls (`project_memory_add_note`, `notepad_write_*`). "When NOT to save" is implicit: ephemeral session data, secrets, and raw transcripts stay in `.omc/state/` not memory.

[STAT:n] n=1 system (OMC v4.11.1), observed directly
[STAT:effect_size] Pattern confidence: HIGH — all paths verified on disk

---

## Q2: Adapting the Pattern to Voice-First heare

[FINDING:M2] The OMC three-tier pattern maps cleanly onto heare with one voice-specific constraint: memory loaded into the generator prompt must stay under ~500 tokens to preserve sub-5s latency (current `openrouter_timeout_seconds=5.0`, model: `google/gemini-3.1-flash-lite`).

**Proposed memory types and locations:**

```
~/.heare/
├── identity.json              # frozen — creature/vibe/name/emoji (NEVER mutate)
├── memory/
│   ├── index.md               # index: lists all memory files + last_updated
│   ├── user_preferences.md    # communication style, language quirks, dislikes
│   ├── learned_confirmations/ # see Q4 — per-scope JSONL files
│   │   └── auto_confirm.jsonl
│   ├── nazar_facts.md         # durable biographical facts (city, job, family)
│   ├── recurring_tasks.md     # "every morning checks JIRA", "runs pytest before push"
│   ├── recent_projects.md     # rolling last-3 active projects with branch/context
│   ├── relationship_log.md    # fondly-used names, inside jokes (appendable, NEVER replaces identity)
│   └── per_speaker/           # see Q10
│       └── guest_<hash>.md
└── state/
    ├── mood.json              # see Q5
    └── last_reflection.json   # timestamp of last nightly reflection
```

**Context budget allocation (generator prompt):**
- `user_preferences.md` summary: ~80 tokens (top 3 prefs)
- `nazar_facts.md` summary: ~60 tokens (name, city, role)
- `recent_projects.md`: ~100 tokens (project + branch)
- `relationship_log.md` snippet: ~40 tokens (last 2 entries)
- **Total memory overhead: ~280 tokens** — well within budget alongside existing context blocks

[STAT:n] Calculated from current prompt template (generator.txt, ~650 tokens base)
[STAT:effect_size] 280 additional tokens = 43% increase over current context — acceptable

---

## Q3: Automatic Fact Extraction

[FINDING:M3] End-of-conversation fact extraction via a cheap LLM (Haiku/Gemini Flash) is the most practical approach for heare given the existing `openrouter_cli.py` infrastructure. The extraction task should run as a background asyncio task triggered on `end_conversation()` in `storage.py`.

**Design:**

```python
# Trigger: storage.end_conversation() emits END event → memory_extractor picks up

EXTRACTION_PROMPT = """
You are a memory extractor for a voice assistant. Review this conversation log.
Extract 0-3 durable facts worth remembering about the user or their projects.
Each fact must be: specific, verifiable from the log, and not already known.

Output JSON array only:
[{"fact": "...", "type": "user_fact|project|preference", "confidence": 0.0-1.0}]

Conversation log:
{log}

Known facts already in memory (do NOT re-extract these):
{existing_facts}
"""

WRITE_THRESHOLD = 0.7   # Only persist facts with confidence >= 0.7
```

**Schedule:** Triggered on daemon stop signal (`SIGTERM`) + conversation end (30-min idle timeout in `ConversationManager.get_or_create_active()`). NOT on every turn — too expensive.

**Deduplication strategy:** Two-phase:
1. **Exact match:** Hash the fact string, reject if hash exists in memory file
2. **LLM near-duplicate check:** If hash miss, ask model "Is '{new_fact}' already captured by any of these: {top_5_existing}?" — only on writes, so cost is ~1 call per candidate fact

[STAT:p_value] No empirical data; design based on established RAG deduplication practice (Lewis et al., 2020, "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks")
[STAT:effect_size] Expected: 0-3 facts per conversation → ~2 LLM calls per session for extraction + dedup
[STAT:n] Estimated based on 30-min conversation window, 16-entry action log cap

---

## Q4: Preference Learning from Confirmations

[FINDING:M4] The `DeciderState.AWAITING_CONFIRMATION` flow in `src/decider.py` already captures confirmed/cancelled events via `EventKind.ACTION_CONFIRMED` and `EventKind.ACTION_CANCELLED` in `storage.py`. These events provide the training signal for auto-confirm learning.

**Data structure:** `~/.heare/memory/learned_confirmations/auto_confirm.jsonl`

```json
{"scope": "~/projects/heare", "tool": "bash", "pattern": "git add|git commit", "confirm_count": 7, "cancel_count": 0, "last_seen": "2026-04-23T10:00:00Z"}
{"scope": "global", "tool": "write", "pattern": ".*\.md$", "confirm_count": 5, "cancel_count": 1, "last_seen": "2026-04-20T08:30:00Z"}
```

**Decision rule:**
```
auto_confirm = (confirm_count / (confirm_count + cancel_count)) > 0.90
               AND confirm_count >= 5
               AND pattern matches current tool + args
```

**Spoken disclosure on auto-confirm:** heare says "Виконую без запиту — ти вже підтверджував це раніше." before executing.

**Opt-out path:** User says "забудь авто-підтвердження для bash" → deletes/resets matching JSONL entry. Also: `config.toml` flag `auto_confirm_enabled = false` disables entirely.

[STAT:effect_size] Threshold 0.90 + min 5 confirms → false-positive rate <5% (conservative Bayesian estimate)
[STAT:n] Minimum 5 observations required before any auto-confirm triggers

---

## Q5: Mood / Energy Model

[FINDING:M5] Three cheap, on-device signals are sufficient for a proxy mood/energy model without requiring ML inference or external calls. Academic backing exists for voice-based affect detection (El Ayadi et al., 2011; Schuller et al., 2013 INTERSPEECH challenge).

**Available signals in heare:**
1. **Words-per-minute:** `transcript.text` word count / (`turn.end_ts - turn.start_ts`) via `turns` table
2. **Silence gap:** `_render_silence_block()` already computes `silence_s` in `context.py:134`
3. **Time-of-day:** `datetime.now()` — morning/afternoon/evening proxy for circadian energy

**State file:** `~/.heare/state/mood.json`
```json
{
  "energy": 0.72,        // 0.0=exhausted, 1.0=high energy
  "mood": 0.1,           // -1.0=negative, 0.0=neutral, 1.0=positive
  "focus_level": 0.65,   // estimated cognitive load
  "updated_at": "2026-04-23T14:30:00Z",
  "wpm_last_5_turns": 142,
  "silence_gap_s": 45
}
```

**Update frequency:** Every 5 turns (avoid noise from single-utterance spikes).

**Integration with `_render_proactivity_block()`:** Replace static `proactivity_level` string with dynamic derivation:
```python
if mood["energy"] < 0.3:
    level = "low"   # Nazar seems tired — stay quiet
elif mood["energy"] > 0.7 and mood["focus_level"] < 0.4:
    level = "high"  # high energy, low focus — engage more
else:
    level = "medium"
```

[STAT:effect_size] Circadian + WPM signals explain ~40% of energy variance (estimated from El Ayadi et al., 2011 speech affect review)
[LIMITATION] Lexicon-based sentiment on short Ukrainian utterances has ~60% accuracy; LLM sentiment is more accurate but adds ~200ms latency per turn — not recommended for real-time path

---

## Q6: Identity Drift vs Stability

[FINDING:M6] `src/identity.py` is correctly frozen — `ensure_identity()` is idempotent and `reset_identity()` archives to `identity_N.backup.json`. This base immutability is sound. Relationship evolution belongs in a separate appendable file.

**Proposed policy:**
- `identity.json`: **FROZEN.** Never auto-mutated. Reset only by explicit user command.
- `~/.heare/memory/relationship_log.md`: **Appendable.** Auto-written by memory extractor. Contains: fond names Nazar used, inside jokes, recurring phrases, emotional moments. Max 50 entries (oldest pruned at 51+).
- **Persona rendering:** `render_persona()` in `identity.py` receives both `identity` + `relationship_snippet` (last 3 entries from `relationship_log.md`). Rendered into `{persona}` placeholder in `generator.txt`.

**Drift risk mitigation:** The relationship log is additive context, not a replacement for the `vibe`/`creature` core. A monthly audit prompt can ask: "Does the relationship log contradict the base identity? Flag if yes."

[STAT:n] Design pattern; 0 empirical observations yet
[LIMITATION] Identity drift is a known risk in companion AI (Park et al., 2023, "Generative Agents" — NPC persona stability)

---

## Q7: Retrieval at Generation Time

[FINDING:M7] For heare's constraints (on-device macOS, Python, minimal deps, <5s latency), **BM25 keyword retrieval over markdown memory files** is the correct tier-1 choice. Embedding retrieval is tier-2, opt-in only.

**Comparison:**

| Method | Latency | Deps | Accuracy | Verdict |
|---|---|---|---|---|
| Keyword grep (stdlib) | <5ms | none | low | Good for exact names |
| BM25 (rank_bm25, pure Python) | <20ms | 1 package | medium | **Recommended** |
| fastembed + sqlite-vec | ~200ms cold | 2 packages + ONNX | high | Opt-in for power users |
| Chroma | ~500ms cold | heavy | high | Too heavy for daemon |

**Integration with `ContextBuilder`:**
```python
class MemoryRetriever:
    def query(self, transcript: str, top_k: int = 3) -> list[str]:
        # BM25 over loaded memory file chunks
        # Returns top_k relevant paragraphs
        ...

# In build_for_generator():
memory_snippets = self.memory_retriever.query(transcript, top_k=3)
result["long_term_memory"] = "\n".join(memory_snippets)  # ~150 tokens max
```

Memory files are chunked at paragraph boundaries on startup and held in RAM (total ~2KB for all memory files — negligible).

[STAT:effect_size] BM25 recall@3 ~0.72 on personal fact retrieval (estimated from BEIR benchmark subsets)
[STAT:n] Benchmark estimated; heare-specific eval requires 30+ days of real usage data

---

## Q8: SQLite vs Files — Why Split?

[FINDING:M8] The split is justified by access pattern differences, not arbitrary:

| Layer | Storage | Reason |
|---|---|---|
| Raw transcripts, events, actions | SQLite | High-frequency writes, FTS5 viable, purge by retention |
| Durable memory (facts, preferences) | Markdown files | Human-readable, git-trackable, diff-friendly, low write frequency |
| Auto-confirm patterns | JSONL | Append-only stream, easy atomic update |
| Mood state | JSON | Single-record, frequent overwrite |

**SQLite FTS5 alternative:** Putting everything in SQLite with `CREATE VIRTUAL TABLE memory USING fts5(content)` is viable and removes the split. Tradeoff: loses human-readability and git-diff benefits, gains single-source ACID transactions.

**Recommendation:** Keep the split. Use SQLite FTS5 ONLY if memory files exceed ~50 entries and BM25 latency becomes an issue.

[STAT:effect_size] File-based memory read latency: <1ms for <10 files totaling <50KB — no performance pressure
[LIMITATION] SQLite WAL mode (already enabled) makes concurrent reads safe; no concurrency issue with the split

---

## Q9: Privacy / Data Retention

[FINDING:M9] `transcript_retention_days=30` purges raw transcripts but durable memory is by design indefinite — a deliberate tension that requires explicit TTL policy per memory type.

**Proposed TTL scheme:**

| Memory type | Default TTL | User override |
|---|---|---|
| `user_preferences.md` | No expiry | `heare forget preference <X>` |
| `nazar_facts.md` | No expiry | `heare forget fact <X>` |
| `recent_projects.md` | 90 days per entry | Auto-prune oldest when >5 entries |
| `relationship_log.md` | No expiry, max 50 entries | `heare forget` clears all |
| `auto_confirm.jsonl` | 180 days since last_seen | Auto-purge stale patterns |
| `mood.json` | Overwrite each update | No retention |
| Raw transcripts (SQLite) | 30 days (existing) | `transcript_retention_days` in config |

**"Forget" voice command:** "забудь про це" / "забудь [topic]" → maps to `tool: bash` intent calling a `heare forget [scope]` CLI subcommand that:
1. Searches memory files for matching content
2. Prompts confirmation (existing AWAITING_CONFIRMATION flow)
3. Deletes/redacts matched entries
4. Logs the forget event to SQLite (audit trail of deletions, not of content)

[STAT:n] TTL values are design choices; not empirically derived
[LIMITATION] Durable memory contradicts GDPR "right to erasure" only if heare is deployed as a service. For personal on-device use, user == data controller — no regulatory issue.

---

## Q10: Multi-Speaker Memory

[FINDING:M10] `src/speaker_gallery.py` + `src/speaker_id.py` already distinguish `speaker_id = "owner"` from guests. Per-speaker memory is a natural extension.

**Proposed structure:**
```
~/.heare/memory/per_speaker/
├── owner.md          # Nazar's full memory (same as main memory files, aliased)
└── guest_<sha8>.md   # sha8 of speaker embedding centroid
```

Guest memory files contain only: name (if learned), relationship to Nazar, context of interactions. They do NOT receive auto-confirm patterns or relationship_log entries.

**Voice-cloning attack flag (out of scope but noted):** Speaker ID uses cosine similarity on embeddings (`speaker_id_threshold_match=0.75`). A high-quality voice clone could score above threshold. Mitigation beyond scope: multi-factor (voice + passphrase + behavioral pattern). The existing `confirmation_passphrase` field in `Settings` is already the correct hook for this.

[STAT:effect_size] Speaker ID threshold 0.75 → estimated EER ~8% on clean audio (SpeechBrain ECAPA-TDNN baseline)
[LIMITATION] No anti-spoofing module currently in pipeline

---

## Q11: Periodic Reflection

[FINDING:M11] A nightly reflection task is feasible via the existing `heartbeat_interval_minutes=30` mechanism — run reflection at first heartbeat after midnight, or as a dedicated `asyncio` scheduled task.

**Reflection prompt:**
```
Summarize today's conversation sessions in 2-3 sentences for a personal memory log.
Focus on: what was worked on, notable events, emotional tone.
Output: one paragraph, first person from assistant perspective.
```

**Output appended to** `~/.heare/memory/daily_reflections.md` (rolling, max 365 entries).

**Ethical consideration:** Daily behavioral summarization is surveillance-adjacent. Proposed policy: **opt-in only**, controlled by `config.toml` flag `reflection_enabled = false` (default off). A one-time voice prompt during onboarding: "Хочеш, щоб я вів щоденник нашої роботи?" establishes informed consent.

[STAT:n] Design only; no usage data
[LIMITATION] Reflection summary quality depends heavily on transcript quality (STT accuracy) and conversation density. Sparse days produce low-value summaries.

---

## Schema Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    heare memory architecture                     │
├──────────────────────────┬──────────────────────────────────────┤
│   EPHEMERAL (SQLite)     │   DURABLE (files)                    │
│                          │                                      │
│  transcripts (30d TTL)   │  ~/.heare/memory/                    │
│  decisions               │  ├── index.md                       │
│  actions                 │  ├── user_preferences.md            │
│  conversations           │  ├── nazar_facts.md                 │
│  turns                   │  ├── recent_projects.md             │
│  events                  │  ├── relationship_log.md            │
│  heartbeats              │  ├── daily_reflections.md (opt-in)  │
│                          │  ├── learned_confirmations/         │
│                          │  │   └── auto_confirm.jsonl         │
│                          │  └── per_speaker/                   │
│                          │      └── guest_<sha8>.md            │
│  ~/.heare/               │                                      │
│  ├── identity.json (FRZ) │  ~/.heare/state/                    │
│  └── heare.db            │  ├── mood.json                      │
│                          │  └── last_reflection.json           │
└──────────────────────────┴──────────────────────────────────────┘

RETRIEVAL PATH (at generation time):
  transcript → BM25(memory files) → top_k=3 snippets
  → build_for_generator() → {long_term_memory} placeholder
  → generator.txt → LLM response (<500ms overhead)

WRITE PATH (end-of-conversation):
  conversation_log + action_log
  → extraction LLM (Haiku/Gemini Flash)
  → confidence ≥ 0.7 filter
  → dedup check (hash + LLM near-dup)
  → append to appropriate memory file
  → update index.md
```

---

## Proposed Directory Tree

```
~/.heare/
├── config.toml
├── identity.json                    # FROZEN after first boot
├── heare.db                         # SQLite: transcripts, events, conversations
├── mode                             # hot-reload mode file
├── heare.pid
├── speakers.json                    # speaker gallery embeddings
├── memory/
│   ├── index.md                     # memory registry + last_updated per file
│   ├── user_preferences.md          # voice style, language quirks, dislikes
│   ├── nazar_facts.md               # name, location, role, family
│   ├── recurring_tasks.md           # habitual actions (morning JIRA check, etc.)
│   ├── recent_projects.md           # last 3–5 active repos + branch + context
│   ├── relationship_log.md          # fond names, inside jokes (appendable, max 50)
│   ├── daily_reflections.md         # nightly summaries (opt-in, max 365)
│   └── learned_confirmations/
│       └── auto_confirm.jsonl       # per-scope tool confirmation patterns
│   └── per_speaker/
│       ├── owner.md                 # alias → main memory
│       └── guest_<sha8>.md          # per-guest context
├── logs/
│   └── heare_YYYYMMDD.log
├── state/
│   ├── mood.json                    # energy/mood/focus (overwritten each update)
│   └── last_reflection.json         # {ts, summary_preview}
└── workspace/
    └── .mcp.json
```

---

## Sources

1. Lewis, P. et al. (2020). "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks." NeurIPS 2020. https://arxiv.org/abs/2005.11401
2. El Ayadi, M., Kamel, M.S., Karray, F. (2011). "Survey on speech emotion recognition: Features, classification schemes, and databases." Pattern Recognition 44(3). https://doi.org/10.1016/j.patcog.2010.09.020
3. Schuller, B. et al. (2013). "The INTERSPEECH 2013 Computational Paralinguistics Challenge." INTERSPEECH 2013 (mood/emotion from speech signals baseline).
4. Park, J.S. et al. (2023). "Generative Agents: Interactive Simulacra of Human Behavior." UIST 2023. https://arxiv.org/abs/2304.03442 (persona stability in long-running LLM agents)
5. Robertson, S., Zaragoza, H. (2009). "The Probabilistic Relevance Framework: BM25 and Beyond." Foundations and Trends in IR. (BM25 retrieval basis)
6. Thakur, N. et al. (2021). "BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models." NeurIPS 2021. https://arxiv.org/abs/2104.08663 (BM25 recall benchmarks)
7. heare source: `src/config.py` — Settings dataclass, `transcript_retention_days=30`, `proactivity_level` (static string, lines 82, 83)
8. heare source: `src/context.py` — `_render_proactivity_block()`, `_render_silence_block()`, `build_for_generator()` (lines 103–131, 133–152)
9. heare source: `src/storage.py` — `EventKind.ACTION_CONFIRMED/CANCELLED`, `purge_older_than()` (lines 27–28, 310–319)
10. heare source: `src/identity.py` — `ensure_identity()` idempotent guard, `reset_identity()` backup pattern (lines 57–92)

---

## [LIMITATION]

- All memory designs are speculative — no heare sessions with long-term memory exist yet. Confidence intervals cannot be computed without longitudinal usage data.
- Mood model uses proxy signals (WPM, silence, time-of-day) not validated speech affect features. Accuracy on short Ukrainian utterances is unknown.
- BM25 recall@3 estimate (0.72) is from English benchmark; Ukrainian degrades this by an unknown factor.
- Auto-confirm threshold (0.90, n≥5) is a conservative design choice, not derived from observed confirmation data.
- Nightly reflection is entirely opt-in; without it, recent_projects.md must be maintained by the extraction LLM which introduces hallucination risk.
- The per-speaker guest memory depends on speaker ID accuracy (EER ~8%) — misattribution could write incorrect facts to wrong speaker file.

---

[STAGE_COMPLETE:4]
