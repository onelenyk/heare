# Proactive Agent Capability Discovery (v1: Discovery-Only)

**Status:** REVISED (Iteration 3/5) — closing all 16 issues from Architect ITERATE + Critic ITERATE
**Mode:** RALPLAN-DR DELIBERATE
**Owner:** planner -> architect -> critic -> executor
**Date:** 2026-05-01
**Scope:** v1 ships discovery + marketplace install hooks ONLY. Codegen / self-authored tool execution is deferred to v2 with explicit gating criteria.

---

## Why this revision exists

Iteration-1 proposed a tiered plan including LLM-authored code execution. Iteration-2 dropped codegen. Iteration-3 (this revision) tightens call-site precision per Architect ITERATE (5 MAJORs) + Critic ITERATE (3 new MAJORs + 5 minors). The architectural shape is unchanged; only spec-level details are sharpened: install path layout, `.mcp.json` write helper, mid-session loader cache invalidation, signature scheme decision (default-off + voice warning), install-flow latency budget, SSRF/supply-chain hardening, cache HMAC, and intent-hash normalization.

---

## RALPLAN-DR Summary

### Principles (4)

1. **P1 — Discovery before refusal.** Before saying "I can't", consult the local capability ledger; on miss, attempt bounded remote discovery. Refusal is a last resort.
2. **P2 — Latency is correctness.** Voice-first: discovery must be cached, tiered, and mostly offline. Remote searches happen behind a TTS filler with a hard 2.5s deadline.
3. **P3 — Capabilities are first-class, persistent, auditable artifacts.** Installed capabilities live at stable filesystem paths with a sidecar `.install.json` provenance record. Marketplace-installed skills are isolated under `~/.heare/skills/_marketplace/<slug>/` so user-authored skills are never touched by revoke flows.
4. **P4 — Failure is a real exit.** When local + bounded remote both fail, agent says so plainly in one short sentence and stops. No "let me research more" loops; no LLM-authored code execution.

### Decision Drivers (top 3)

1. **Voice latency budget.** p50 first-audio <= baseline + 500ms (no remote hit); p95 <= baseline + 3s (with remote hit). Install confirm flow <= 5s human time.
2. **Trust boundary clarity.** Only execute artifacts the user can audit. Marketplace artifacts are reviewable, hostname-allowlisted, and checksum-verified. Codegen deferred to v2.
3. **Scope discipline.** "Simple task" is structurally defined as "discoverable via marketplace search". Bounded, testable, and aligned with how the ecosystem works today.

### Viable Options

#### Option A — Discovery-only with marketplace install hooks (RECOMMENDED v1)

- **Pros:** Lowest risk (no LLM code execution surface). Tightest scope (no sandbox engineering). Highest user value for the long tail. Reusable trust model — marketplace artifacts are reviewable.
- **Cons:** User cannot get a tool that exists in no marketplace. MCP installs require a daemon restart (skills do NOT — see MAJOR-C). Marketplace coverage of "I can't do X" cases is not yet measured.

#### Option B — Discovery + auto-install (skip user confirmation when match score > X) — REJECTED

- **Why rejected:** Marketplace install is a privileged action. Per-action consent is required, not session-wide unlock. Auto-install creates a typosquat / malicious-skill attack vector with no human-in-the-loop checkpoint.

#### Option C — Discovery + codegen — REJECTED for v1, deferred to v2

- **Why rejected for v1:** Cannot be made safe within v1 timeline. (Full rationale in ADR.)
- **v2 entry criteria:** see Follow-ups; v2 entry triggers when ALL criteria met, OR when marketplace coverage of intent classes drops below 50% over 6 months from v1 ship.

### Marketplace-first counter-argument (engaged explicitly)

The user's "if simple task — create own solution" framing is satisfied by marketplace install: from the user's perspective, **installing a marketplace skill IS doing it**. They care that the capability appears and works, not whether the agent authored or fetched it. Marketplace covers the majority of common requests at a fraction of the security cost; codegen is reserved for the long tail that exists in no marketplace, which v2 may address once a sandbox primitive ships.

(Note: prior iterations cited ">95% of cases" without evidence. Soft-claim per Critic NEW-MINOR; quantitative evidence is a v1.1 research item if marketplace coverage proves contentious.)

---

## Pre-mortem (6 scenarios — DELIBERATE mode; minimum 3, well above)

### Scenario 1 — Marketplace skill is malicious / typosquats a popular name

**What happens:** Agent finds `weather_pro` (typosquat) instead of `weather-pro` (legit). User confirms by voice. Skill installed; payload runs on first invocation.

**Mitigations baked into the plan:**
- Hostname allowlist (default `["skillsmp.com", "github.com"]`) — see MAJOR-F.
- SHA-256 checksum verification against registry index value — see MAJOR-F.
- Homoglyph hostname rejection (e.g., `g1thub.com`, `github.com.evil.com`) — see MAJOR-F.
- Name fuzzy-match warning when within edit-distance 2 of more-popular skill.
- Popularity threshold: skills below 100 downloads/stars flagged as "low-trust" with explicit voice warning.
- AC13 specifies one disposition per case (typosquat blocked; homoglyph blocked; unsigned + low-popularity flagged with voice warning; suspicious permissions blocked; trojan signature blocked).

### Scenario 2 — Marketplace returns nothing & user expectation set high

**What happens:** Agent says "let me check"; remote returns empty; user feels misled.

**Mitigations:** Filler tuned to under-promise ("let me check", not "I'll find one"). Graceful refusal text in EN + UK. Surface top-1 closest match by score with "not what you asked for, but the closest available" framing. AC8 covers refusal-replacement for both languages.

### Scenario 3 — MCP server install requires API keys / OAuth

**What happens:** Server installed but fails on first invocation due to missing key.

**Mitigations:** MCP manifests declare `requires_secrets`; installer surfaces this. Voice flow: "this needs a `<secret-name>`; I've added the server but you'll need to set `<env-var>` and restart heare." Sidecar records `requires_secrets`.

### Scenario 4 — Latency cascade (remote slow + LLM stall + TTS filler loop)

**What happens:** Remote slow (3s+); filler plays; discovery times out at 2.5s; LLM follow-up takes another 2s; user hung up.

**Mitigations:** Hard 2.5s timeout, single-shot filler, fail-back path on timeout ("let me think"), 24h cache. AC9 splits latency budget into local-only and remote-hit cases.

### Scenario 5 — Marketplace API outage at session start (NEW)

**What happens:** Session starts; first turn requires remote discovery; marketplace API is down.

**Why plausible:** Third-party services have outages. Without graceful handling, sessions silently degrade to "I can't help with anything."

**Mitigations:**
- 24h cache TTL: warm cache serves discovery during outage for previously-seen intents.
- If cache is empty AND remote is down: graceful voice message "I can't search for tools right now; tell me later or check `~/.heare/cache/discovery/`." Session continues; refusal is local-only, NOT silent.
- Circuit breaker (3 net-fails -> remote disabled for session) prevents repeated 2.5s timeouts.

### Scenario 6 — Marketplace returns malformed JSON / mid-stream schema change (NEW)

**What happens:** Marketplace ships a schema change; old client parses fail.

**Why plausible:** External APIs evolve; clients lag.

**Mitigations:**
- Pydantic schema validation on every parse; reject malformed entries individually (don't crash the batch).
- Malformed-entry parser exceptions count toward circuit breaker as net-fail (treated equivalent to a 5xx).
- Schema-version field in `IndexEntry` extension; client logs warning if marketplace ships unknown version, falls back to known fields only.

---

## Threat Model

### Assets

- User secrets: `~/.ssh/`, `~/.aws/`, `.env`, `~/.heare/identity.json`, `~/.heare/speakers.json`.
- Local files (read access via installed skills/MCPs).
- Network egress (any installed MCP server can reach the internet).
- Voice channel integrity (transcripts feed LLM and install confirmations).
- Discovery cache integrity: `~/.heare/cache/discovery/` (HMAC-protected per MAJOR-G).

### Adversaries

- **Malicious marketplace skill author** — typosquats, hides payload, ships malicious manifest.
- **Malicious MCP server** — exfiltrates context on each invocation.
- **Malicious URL via web_search** — content suggests installing a name. Lower priority since v1 NEVER auto-installs from web content.
- **Smart-speaker audio bleed / replay / family-member utterance** — relevant for install confirmations. Speaker-ID enrollment OR `confirmation_passphrase` is a HARD requirement for installs (no "where available" hedge).
- **Cache poisoner** — local attacker tampers with `~/.heare/cache/discovery/*` to inject malicious install candidates. Mitigated by HMAC signing with key from `~/.heare/identity.json` + `0700` cache dir permissions (MAJOR-G + NEW-MAJOR-I).

### Trust boundaries

- Marketplace artifacts: hostname-allowlisted + checksum-verified + user-confirmed before install. Signature scheme: default `installation_signature_required: bool = False` for v1 (no skillsmp.com signing scheme exists yet); voice warning fires before each unsigned install ("this skill is unsigned; install anyway?"). v1.1 flips default to `True` when signing ships.
- MCP servers: user-confirmed before `workspace_dir / .mcp.json` edit (via `mcp_utils.write_mcp_servers()` helper).
- No LLM-authored code executes in v1.
- All install paths leave a sidecar `.install.json` provenance record at `~/.heare/skills/_marketplace/<slug>/.install.json`.

### Attack trees

- **Typosquat -> wrong skill installed.** Mitigations: name fuzzy-match warning, popularity threshold, hostname allowlist, checksum verification, voice confirmation includes hostname.
- **MCP middleman -> exfiltrates context.** Mitigations: user-confirm before adding, sidecar provenance, revoke flow.
- **Replay attack on install confirmation.** Mitigations: confirmation prompt is single-use; speaker-ID OR passphrase HARD-required (not "where available"); install refused if neither configured.
- **Cache poisoning -> malicious install candidate served to user.** Mitigations: HMAC each cache file with key from `~/.heare/identity.json`; validate HMAC on read; reject + log + delete on mismatch; fall through to remote.

---

## Plan Body

### Discovery flow (every user turn)

1. **System prompt addendum.** `llm_context_injector.render_native_system_prompt` gains a "Capability protocol" block instructing the LLM to call `discover_capability(intent=...)` on capability-miss instead of refusing generically.
2. **Top-K capability hints.** Per turn, inject the top-K (default 5) most-relevant capabilities from the index into the system prompt. Replaces existing skill-name-only injection.
3. **`discover_capability(intent: str)`** — Synchronous local query, p99 < 10ms. Substring + inverted-index over unified `CapabilityIndex`.
4. **`discover_capability_remote(intent: str)`** — Async, 2.5s deadline, kicked off behind TTS filler.
   - Tier 1: `marketplace.fetch_skill_candidates(intent)` -> skillsmp.com search.
   - Tier 2: `marketplace.fetch_mcp_candidates(intent)` -> MCP registry search.
   - Caches: `~/.heare/cache/discovery/<intent_hash>.json` (mode `0700` dir; HMAC-signed file; 24h TTL).
   - `intent_hash = SHA-256(NFKC.lowercase().collapse_whitespace(intent))`.
5. **`install_skill(slug, source_url, checksum)`** — Voice-confirmed install.
   - Hostname allowlist check (default `["skillsmp.com", "github.com"]`); homoglyph rejection.
   - SHA-256 checksum verification against `IndexEntry.checksum`.
   - Signature check: if `installation_signature_required=False` (v1 default) AND skill unsigned, voice warning + extra confirmation; if `=True`, refuse unsigned.
   - Speaker-ID enrollment OR `confirmation_passphrase` REQUIRED; if neither, install refused with voice message.
   - Refuses to overwrite existing `_marketplace/<slug>/` directory without `--replace` confirmation.
   - Tarball -> `~/.heare/skills/_marketplace/<slug>/SKILL.md`; sidecar `_marketplace/<slug>/.install.json`.
   - Calls `loader.invalidate()` AND `capability_index.rebuild()` post-install — skill is callable at turn N+1 without daemon restart.
6. **`install_mcp_server(name, registry_url, checksum)`** — Voice-confirmed install.
   - Same hostname/checksum/speaker-ID gates as `install_skill`.
   - Reads existing config via `mcp_utils.read_mcp_servers()`; writes via `mcp_utils.write_mcp_servers()`. Both helpers operate on `workspace_dir / ".mcp.json"`. Never writes JSON directly.
   - Sidecar at `~/.heare/skills/_marketplace/<name>/.install.json` records `requires_secrets`.
   - Voice: "restart required" — MCP servers (unlike skills) require daemon restart.
7. **`revoke_capability(name)`** — Voice-confirmed removal.
   - Refuses to delete any directory lacking an `.install.json` sidecar (user-authored skills at `~/.heare/skills/<name>/` are protected; only `~/.heare/skills/_marketplace/<slug>/` is touchable).
   - Disambiguates if multiple capabilities match.
   - Removes filesystem entry (skill case) or `.mcp.json` entry via `mcp_utils.write_mcp_servers()` (MCP case).
   - Calls `loader.invalidate()` + `capability_index.rebuild()` post-revoke.

### Capability-search hierarchy (priority order)

1. Static tools (`TOOLS` in `tool_registry.py`) — instant.
2. Active MCP servers (parsed from `workspace_dir/.mcp.json` at session start) — instant.
3. Dynamic tools (`_DYNAMIC_TOOLS`) — instant.
4. Installed skills (`SkillsLoader.discover()` over `~/.heare/skills/<name>/` AND `~/.heare/skills/_marketplace/<slug>/`) — instant.
5. Remote marketplaces (skillsmp.com + MCP registry) — async, 2.5s budget, with TTS filler.

### Unified `IndexEntry` schema

```python
@dataclass
class IndexEntry:
    source: Literal["tool", "dynamic_tool", "skill", "mcp"]
    name: str
    description: str
    args_schema: dict | None
    network_required: bool
    popularity_score: float | None     # marketplace candidates only
    install_url: str | None            # marketplace candidates only
    checksum: str | None               # SHA-256 hex; marketplace candidates only
    schema_version: str | None         # for forward-compat with marketplace schema changes
```

### MCP discovery integration

- Session start: `capability_index.py` calls `mcp_utils.read_mcp_servers()` (operating on `workspace_dir/.mcp.json`), parses each server's declared tools, projects each into `IndexEntry(source="mcp", ...)`.
- Mid-session MCP additions require daemon restart (voice-explained). Mid-session SKILL additions do NOT require restart (loader cache invalidated; index rebuilt).

### Voice templates (canonical)

#### Install-confirm prompt (trimmed per MAJOR-E to 2 attributes: hostname + 1-sentence summary)

- **EN (no red flags):** "Found `<slug>` from `<hostname>`. `<one-sentence-summary>` Install it?"
- **UK (no red flags):** "Знайшов `<slug>` з `<hostname>`. `<one-sentence-summary>` Встановити?"
- **EN (red flag — unsigned / low popularity / secrets-required):** "Found `<slug>` from `<hostname>`. `<summary>` Heads up: `<flag-reason>`. Install anyway?"
- **UK (red flag):** "Знайшов `<slug>` з `<hostname>`. `<summary>` Увага: `<flag-reason>`. Все одно встановити?"

Total flow target: <=5s human time from "I found a tool" to user yes/no (AC15).

#### Refusal text (capability missing) — bound to existing multi-language system-prompt template

- **EN:** "I don't have a tool for that. Want me to look one up?"
- **UK:** "Не маю інструменту для цього. Хочеш, я пошукаю?"

#### Install refusal (no speaker-ID + no passphrase)

- **EN:** "Speaker ID or passphrase required to install tools. Configure in settings."
- **UK:** "Для встановлення інструментів потрібен Speaker ID або пароль. Налаштуй у settings."

#### Marketplace outage fallback

- **EN:** "I can't search for tools right now; tell me later."
- **UK:** "Зараз не можу шукати інструменти; скажи мені пізніше."

### TTS filler injection

- **Spike (Step 5):** 1-day investigation in `src/pipeline.py` to identify pipecat frame type / processor for filler injection. Document in `.omc/research/`.
- Implementation: when remote discovery fires, push a single filler frame ("let me check"). On result or timeout, normal LLM output continues. Single-shot per turn.

### Files to touch

| File | Change |
|---|---|
| `src/capability_index.py` (NEW) | Unified `IndexEntry` schema; `CapabilityIndex` with substring + inverted-index query; `rebuild()` method; builder merges all 4 sources at session start. |
| `src/discovery.py` (NEW) | `discover_capability` (local) + `discover_capability_remote` (async, 2.5s timeout). `intent_hash = SHA-256(NFKC.lowercase().collapse_whitespace(intent))`. |
| `src/marketplace.py` (NEW) | skillsmp.com fetcher + MCP registry fetcher; pydantic schema validation; hostname allowlist + homoglyph rejection; SHA-256 checksum verification; cache layer (HMAC-signed; `0700` perms). |
| `src/installer.py` (NEW) | `install_skill`, `install_mcp_server`, `revoke_capability`; voice-confirm pipelines; speaker-ID/passphrase gate; sidecar `.install.json` write/read; `_marketplace/` subtree; refuse-overwrite-without-replace. Calls `loader.invalidate()` + `capability_index.rebuild()` post-install/revoke. |
| `src/agent_skills.py` | Add `SkillsLoader.invalidate()` (sets `self._discovered = False`). Extend loader to scan BOTH `~/.heare/skills/<name>/` (1-level) AND `~/.heare/skills/_marketplace/<slug>/` (2-level subtree). Read `<slug>/.install.json` sidecar; surface `installed_via_discovery: bool` in `SkillManifest`. |
| `src/mcp_utils.py` | Add `write_mcp_servers(workspace_dir, servers: dict)` helper mirroring existing `read_mcp_servers()`. Both operate on `workspace_dir / ".mcp.json"` as single source of truth. |
| `src/llm_context_injector.py` | Inject top-K (default 5) most-relevant capabilities per turn from `CapabilityIndex.query(last_user_transcript)`. Add Capability protocol block. Bind EN + UK refusal strings to existing multi-language template. |
| `src/direct_tools.py` | Register `_execute_discover_capability`, `_execute_discover_capability_remote`, `_execute_install_skill`, `_execute_install_mcp_server`, `_execute_revoke_capability`, `_execute_list_installed_capabilities`. |
| `src/tool_registry.py` | Register the 6 new direct tools. |
| `src/config.py` | Add: `marketplace_url: str = "https://skillsmp.com"`, `mcp_registry_url: str`, `discovery_remote_enabled: bool = True`, `discovery_remote_timeout_s: float = 2.5`, `discovery_cache_ttl_h: int = 24`, `installation_signature_required: bool = False` (v1 default; flip to `True` in v1.1 once signing ships), `installation_popularity_threshold: int = 100`, `marketplace_hostname_allowlist: list[str] = ["skillsmp.com", "github.com"]`, `top_k_capability_hints: int = 5`, `telemetry_enabled: bool = False`. |
| `src/pipeline.py` | TTS filler frame injection point per Step 5 spike. |
| `tests/unit/test_capability_index.py` (NEW) | Per test plan. |
| `tests/unit/test_marketplace.py` (NEW) | Per test plan; includes hostname allowlist + checksum + homoglyph + schema validation. |
| `tests/unit/test_installer.py` (NEW) | Per test plan; includes `_marketplace/` path + sidecar + speaker-ID gate + replace-protection + loader invalidation. |
| `tests/unit/test_revocation.py` (NEW) | Per test plan; includes sidecar protection (refuse to delete user-authored skill). |
| `tests/unit/test_discovery.py` (NEW) | Per test plan; includes intent_hash normalization + cache HMAC. |
| `tests/unit/test_intent_to_skill_matcher.py` (NEW) | Per test plan. |
| `tests/unit/test_mcp_utils.py` (NEW) | `read_mcp_servers()` / `write_mcp_servers()` round-trip; idempotency. |
| `tests/integration/test_end_to_end_discovery_turn.py` (NEW) | Per test plan. |
| `tests/integration/test_end_to_end_install_turn.py` (NEW) | Per test plan; includes mid-session callability of newly installed skill. |
| `tests/integration/test_mcp_install_restart_flow.py` (NEW) | Per test plan. |
| `tests/integration/test_circuit_breaker.py` (NEW) | Per test plan; includes adversarial mixed-failure scenario. |
| `tests/e2e/test_voice_e2e_discovery.py` (NEW) | Per test plan. |

---

## Expanded Test Plan

### Unit (`tests/unit/`)

- `test_capability_index.py` — Index builds from `TOOLS` + `_DYNAMIC_TOOLS` + `SkillsLoader.discover()` + MCP servers from `mcp_utils.read_mcp_servers()`. Build < 50ms over 100 skills + 50 tools + 10 MCP servers. Query p99 < 10ms over 1000 calls. `rebuild()` is idempotent and picks up newly added entries.
- `test_marketplace.py` — Fetchers parse via pydantic; honor 24h cache; fail closed at 2.5s; hostname allowlist enforced; homoglyph rejected (`g1thub.com`, `github.com.evil.com`); SHA-256 checksum mismatch blocks install; malformed JSON entries individually rejected (don't crash batch); malformed-entry exception increments circuit breaker as net-fail.
- `test_installer.py` — `install_skill` writes to `~/.heare/skills/_marketplace/<slug>/SKILL.md` + `_marketplace/<slug>/.install.json`. `install_mcp_server` calls `mcp_utils.read_mcp_servers()` then `mcp_utils.write_mcp_servers()` (never writes JSON directly). Both record provenance. Both refuse without confirmation token. Both refuse without speaker-ID enrollment AND without `confirmation_passphrase`. `install_skill` refuses to overwrite existing `_marketplace/<slug>/` without `--replace`. Post-install: `loader.invalidate()` AND `capability_index.rebuild()` are both called.
- `test_revocation.py` — `revoke_capability` removes installed skill or MCP entry; requires confirmation; logs `capability_revoke`; idempotent. **Refuses to delete any skill directory lacking `.install.json` sidecar** (user-authored skills protected). Post-revoke: `loader.invalidate()` + `capability_index.rebuild()`.
- `test_discovery.py` — Local discovery within budget; remote cancels at 2.5s; cache hits skip network; cache miss + network error -> graceful empty. **`intent_hash`: two intents differing only in whitespace/case (`"Send Email"` vs `"send  email"`) produce identical hash.** Cache file HMAC validated on read; tampered file rejected + deleted. Cache directory permissions = `0700`.
- `test_intent_to_skill_matcher.py` — Top-K match; substring beats fuzzy; ties broken by popularity.
- `test_mcp_utils.py` — `read_mcp_servers()` / `write_mcp_servers()` round-trip preserves servers; both operate on `workspace_dir / ".mcp.json"`; idempotent.

### Integration (`tests/integration/`)

- `test_end_to_end_discovery_turn.py` — Missing-capability transcript -> local miss -> remote (mocked) -> top-3 candidates -> LLM proposes. No install without explicit confirmation.
- `test_end_to_end_install_turn.py` — Mocked candidate -> proposal -> mocked confirmation -> tarball cloned to `~/.heare/skills/_marketplace/<slug>/` -> sidecar written -> **same session, next turn, the new skill is callable** (loader invalidated, index rebuilt).
- `test_mcp_install_restart_flow.py` — Mocked MCP -> proposal -> confirm -> `mcp_utils.write_mcp_servers()` updates `workspace_dir/.mcp.json` -> "restart required" voice -> simulated restart -> server discoverable.
- `test_circuit_breaker.py` — 3 net-fails -> remote disabled. **Adversarial mixed-failure scenario:** sig-fail + net-fail + safety-block alternating; verify safety-blocks don't count; verify breaker doesn't reset on intervening safety-block.

### End-to-end (`tests/e2e/`)

- `test_voice_e2e_discovery.py`:
  - "what's the weather in Kyiv?" -> filler -> install proposal OR graceful refusal in EN/UK.
  - "I need a Slack tool" -> propose `mcp-slack` -> user "yes" -> install + restart-required message -> after restart, `slack_send_message` callable.
  - **Install confirm flow latency: <=5s human time** from "I found a tool" to user yes/no (AC15).
- Latency: p50 <= baseline + 500ms (local-only); p95 <= baseline + 3s (with remote hit).

### Observability

- Action_log subtypes: `capability_discovery`, `capability_install`, `capability_revoke` with structured fields.
- Metric counters: `discovery_attempts_total`, `discovery_local_hits`, `discovery_remote_hits`, `discovery_timeouts`, `install_attempts`, `install_successes`, `install_signature_blocks`, `install_checksum_blocks`, `install_hostname_blocks`, `install_speaker_id_blocks`, `revoke_count`, `marketplace_fetch_errors`, `cache_hmac_failures`.
- Daily rollup printed by `hearectl status`.
- Telemetry: opt-in only; `telemetry_enabled = false` default; raw transcripts NEVER leave device.

---

## Acceptance Criteria (testable)

1. **AC1 — Index build budget.** `CapabilityIndex.build()` < 50ms over 100 skills + 50 tools + 10 MCP servers.
2. **AC2 — Index query budget.** `CapabilityIndex.query()` p99 < 10ms over 1000 calls.
3. **AC3 — Unified index schema (strengthened).** Index merges `TOOLS`, `_DYNAMIC_TOOLS`, `SkillsLoader.discover()`, and MCP servers (via `mcp_utils.read_mcp_servers()`) into unified schema. **Verified by integration test asserting >0 entries from EACH of the 4 sources after a populated session-start.**
4. **AC4 — Remote discovery deadline.** Remote returns within 2.5s OR times out gracefully; on timeout, returns empty without raising.
5. **AC5 — Per-install consent.** Marketplace install requires explicit user voice confirmation per install action. No filesystem write or `.mcp.json` edit without confirmation token.
6. **AC6 — Skill install lands at marketplace subtree path with sidecar + replace-protection.** Installed skill lives at `~/.heare/skills/_marketplace/<slug>/SKILL.md`; sidecar `_marketplace/<slug>/.install.json` records `source_url`, `version`, `installed_at`, `user_confirmed_at`, `signature_verified`, `checksum_verified`, `requires_secrets`. Loader picks it up on next `discover()`. **Install refuses to overwrite existing `<slug>` directory without `--replace` confirmation.**
7. **AC7 — MCP install + restart.** `install_mcp_server` calls `mcp_utils.write_mcp_servers()` (never writes JSON directly), writes sidecar, voice-message includes "restart required". Server discoverable on next session start.
8. **AC8 — Replacement of generic refusal in EN + UK.** Local-miss voice response is bound to existing multi-language template:
   - **EN:** "I don't have a tool for that. Want me to look one up?"
   - **UK:** "Не маю інструменту для цього. Хочеш, я пошукаю?"
9. **AC9 — Latency budgets.** p50 first-audio (local-only) <= baseline + 500ms. p95 (remote hit) <= baseline + 3s. 20-sample turn measurement.
10. **AC10 — User can list installed-via-discovery items by voice + length-bound.** "What tools did you install?" returns voice readout of all `_marketplace/<slug>/.install.json` items. **When count >5, voice uses summary form: "I have 12 installed tools — want me to name them all, or filter by category?"**
11. **AC11 — User can revoke by voice + sidecar protection.** Revoke flow disambiguates, confirms, deletes, logs. **Revoke refuses to delete any skill directory lacking an `.install.json` sidecar (user-authored skills at `~/.heare/skills/<name>/` are protected).**
12. **AC12 — Provenance recorded.** Every install writes sidecar containing `source_url`, `version`, `installed_at` (ISO), `user_confirmed_at` (ISO), `signature_verified` (bool), `checksum_verified` (bool), `hostname` (str), `requires_secrets` (list[str]).
13. **AC13 — Adversarial install corpus, one disposition per case.**
    - (a) Typosquat of popular name -> **blocked**.
    - (b) Homoglyph hostname (`g1thub.com`, `github.com.evil.com`) -> **blocked**.
    - (c) Unsigned + low-popularity (<100) -> **flagged with voice warning** (still installable on extra confirmation since v1 default `installation_signature_required=False`).
    - (d) Suspicious requested permissions in manifest (e.g., `requires_secrets` matches `~/.ssh/*`) -> **blocked**.
    - (e) Trojan signature (manifest claims one thing, impl does another — detected via signed manifest mismatch where signing exists) -> **blocked**.
    - Bonus: install with hostname not in allowlist -> **blocked**. Install with checksum mismatch -> **blocked**.
14. **AC14 — Circuit breaker scope.** Triggers only on execution-errors (net failure, 5xx, parser exception, malformed JSON). Safety-blocks (sig fail, hostname block, checksum block, homoglyph block) do NOT count. Session-scoped (no cross-restart persistence). **Adversarial test:** mixed sig-fail + net-fail + safety-block alternating verifies safety-blocks don't count and breaker doesn't reset on intervening safety-block.
15. **AC15 — Install confirm flow latency.** From "I found a tool" voice frame to user yes/no <= 5s human time. Voice readout uses 2 attributes (hostname + 1-sentence summary); popularity/secrets/unsigned warnings ONLY on red flags.
16. **AC16 — Mid-session skill callability.** Skill installed at turn N is callable at turn N+1 within the same session (no daemon restart). Verified by integration test. (MCP servers still require restart — distinct path.)
17. **AC17 — `.mcp.json` single source of truth.** All read/write of `workspace_dir / ".mcp.json"` goes through `mcp_utils.read_mcp_servers()` / `mcp_utils.write_mcp_servers()`. Verified by code search: zero direct JSON I/O on `.mcp.json` outside `mcp_utils.py`.
18. **AC18 — `intent_hash` algorithm.** `SHA-256(NFKC.lowercase().collapse_whitespace(intent))`. Two intents differing only in whitespace/case produce identical hash. Cache directory `~/.heare/cache/discovery/` permissions = `0700`.
19. **AC19 — Cache HMAC integrity.** Each cache file HMAC-signed using key from `~/.heare/identity.json`. Tampered file -> rejected + logged + deleted; discovery falls through to remote. Verified by unit test that flips a byte and asserts rejection.
20. **AC20 — Speaker-ID OR passphrase HARD requirement for installs.** Install with no speaker-ID enrollment AND no `confirmation_passphrase` configured -> install refused with EN/UK voice message ("Speaker ID or passphrase required to install tools. Configure in settings.").
21. **AC21 — Hostname allowlist + checksum verification.** Install with hostname not in `marketplace_hostname_allowlist` -> blocked. Install with SHA-256 checksum mismatching `IndexEntry.checksum` -> blocked. Homoglyph hostname -> blocked.
22. **AC22 — Marketplace API outage graceful fallback.** Session start with marketplace down: turns requiring remote serve from cache where available; cache-empty cases yield "I can't search right now; tell me later." (EN/UK), session continues, refusal is local-only.

---

## Implementation Steps

1. **Build `IndexEntry` + `CapabilityIndex`.** Create `src/capability_index.py` with unified schema, substring + inverted-index query, `rebuild()` method, and builder stub for 4 source iterables. Unit tests cover AC1, AC2.
2. **Add `mcp_utils.write_mcp_servers()` + standardize on `workspace_dir/.mcp.json`.** Add helper to `src/mcp_utils.py` mirroring existing `read_mcp_servers()`. Audit codebase: replace all bare `.mcp.json` references with `workspace_dir / ".mcp.json"`. Unit tests cover round-trip + idempotency. Closes MAJOR-B; covers AC17.
3. **Wire all 4 sources into the index at session start.** Connect `TOOLS`, `_DYNAMIC_TOOLS`, `SkillsLoader.discover()` (scanning BOTH `~/.heare/skills/<name>/` 1-level AND `~/.heare/skills/_marketplace/<slug>/` 2-level subtree), and MCP servers via `mcp_utils.read_mcp_servers()`. Integration test covers AC3.
4. **Add `discover_capability` direct tool.** Local-only top-3 query. Register in `tool_registry.py` + `direct_tools.py`. Latency unit test (AC2).
5. **Build remote discovery layer with hardening.** Create `src/discovery.py` + `src/marketplace.py`. Implement:
   - skillsmp.com fetch + MCP registry fetch with pydantic schema validation.
   - 2.5s timeout; 24h cache.
   - `intent_hash = SHA-256(NFKC.lowercase().collapse_whitespace(intent))` (AC18).
   - Cache HMAC using key from `~/.heare/identity.json`; cache dir `0700` (AC19).
   - Hostname allowlist (`marketplace_hostname_allowlist` config); homoglyph rejection.
   - SHA-256 checksum verification (AC21).
   - Malformed JSON entries individually rejected; parser exceptions count toward circuit breaker as net-fail.
   Closes MAJOR-F, NEW-MAJOR-H, NEW-MAJOR-I; covers AC4, AC18, AC19, AC21.
6. **Spike: pipecat TTS filler hook.** 1-day investigation in `src/pipeline.py`. Document hook in `.omc/research/`.
7. **Add filler injection.** Single-shot per turn; on result/timeout, normal LLM output continues.
8. **Build `install_skill` tool with full hardening.** Voice-confirm flow. Install path = `~/.heare/skills/_marketplace/<slug>/`. Refuse-overwrite-without-`--replace`. Hostname allowlist + checksum + homoglyph gates. Speaker-ID OR `confirmation_passphrase` HARD-required (AC20). `installation_signature_required: bool = False` (v1 default) -> voice warning "this skill is unsigned, install anyway?" before proceeding. Sidecar `.install.json` write. Post-install: `loader.invalidate()` + `capability_index.rebuild()` (AC16). Trim install-confirm voice readout to 2 attributes (hostname + summary); flags only on red flags (AC15). Closes MAJOR-A, MAJOR-D, MAJOR-E, MAJOR-F, carry-over CRITICAL-5; covers AC5, AC6, AC12, AC13, AC15, AC16, AC20, AC21.
9. **Build `install_mcp_server` tool.** Voice-confirm. Reads via `mcp_utils.read_mcp_servers()`; writes via `mcp_utils.write_mcp_servers()`. Sidecar at `_marketplace/<name>/.install.json`. "Restart required" voice. Same speaker-ID + hostname + checksum gates. Closes MAJOR-B; covers AC7.
10. **Build `revoke_capability` tool with sidecar protection.** Refuse to delete skill directories without `.install.json` sidecar (AC11). Disambiguation. Removes filesystem (skill) or `.mcp.json` entry (via `mcp_utils.write_mcp_servers()`). Post-revoke: `loader.invalidate()` + `capability_index.rebuild()`. Logs `capability_revoke`. Closes MAJOR-A.
11. **Update `llm_context_injector.py`.** Top-K (default 5) injection per turn. Capability protocol block. Bind EN + UK refusal strings to existing multi-language template (NEW-MINOR-K). Snapshot test for AC8.
12. **Add observability.** Emit `capability_discovery`, `capability_install`, `capability_revoke` action_log subtypes with structured fields. Add metric counters incl. `cache_hmac_failures`, `install_hostname_blocks`, `install_checksum_blocks`, `install_speaker_id_blocks`. Tests assert events emit.
13. **Add `list_installed_capabilities` direct tool.** Reads all `_marketplace/<slug>/.install.json` sidecars. Voice-readable summary; uses summary form when count >5 (AC10).
14. **End-to-end smoke.** Implement `tests/e2e/test_voice_e2e_discovery.py`. Capture latency numbers (AC9, AC15). Verify mid-session skill callability (AC16). Verify EN + UK paths.

(Note: previous Step 12 — `proactivity_level` config gate — has been **CUT from v1** per Critic NEW-MAJOR-G option (b). v1 always remote-discovers when local index misses, subject to circuit breaker + cache. Proactivity gating deferred to v1.1.)

---

## ADR

### Decision

Ship **Option A** (discovery + marketplace install hooks) for v1. Defer **Option C** (discovery + codegen) to v2 behind explicit security primitives.

### Drivers

1. Voice latency budget (p50 <= baseline + 500ms; p95 <= baseline + 3s; install confirm <= 5s).
2. Trust boundary clarity (hostname allowlist + checksum + signature scheme + speaker-ID gate; codegen deferred until v2 sandbox exists).
3. Scope discipline ("simple task" defined as "discoverable via marketplace search").

### Alternatives considered

- **Option B (auto-install).** REJECTED. Per-install consent is non-negotiable; session-wide unlock would be a footgun.
- **Option C (discovery + codegen).** REJECTED for v1; deferred to v2 with concrete entry criteria. Rationale:
  1. No working sandbox primitive on heare's target platforms (`sandbox-exec` deprecated since macOS 10.15; `bwrap` requires user namespaces, often disabled on stock RHEL).
  2. Voice-channel unlock is itself prompt-injectable (smart-speaker bleed, TTS self-loopback, replay, family-member utterance).
  3. AST-level bash classifier needs a research-grade adversarial corpus.
  4. Python `exec` with restricted `__builtins__` is structurally broken.
- **Marketplace-first vs codegen-first framing.** Marketplace artifacts are reviewable, hostname-allowlisted, checksum-verified, and not LLM-authored. They satisfy the user's "if simple, just do it" intent because installing IS doing it. Codegen is for the long tail v1 explicitly does not own.

### Why chosen

Highest user value (marketplace covers the majority of common requests), lowest security risk (no LLM code execution), tightest scope (no sandbox engineering required), best ratio of v1 ship time to v1 user value. Leaves a clean v2 boundary.

### Consequences

- (positive) No new code-execution surface; existing `dynamic_tools.execute_*` is not made more eager.
- (positive) Reviewable persistent artifacts; users can audit, version-control, revoke.
- (positive) Mid-session skill installability without daemon restart (loader invalidation).
- (positive) `.mcp.json` access standardized through `mcp_utils` helpers.
- (negative) Users cannot get tools that exist in no marketplace.
- (negative) MCP installs require daemon restart.
- (negative) `installation_signature_required=False` default in v1 means unsigned installs are possible (voice-warned). v1.1 flips default once signing scheme exists.
- (negative) Discovery latency adds up to 2.5s on remote-hit turns. Mitigated by filler + 24h cache.

### Follow-ups

**v1.1 (post-v1 hardening, before v2):**
- Flip `installation_signature_required` default to `True` once skillsmp.com ships a signing scheme.
- Optional: re-introduce `proactivity_level` config gate if user feedback demands it.
- Optional: research bullet — examine N=20 sample requests, classify marketplace-coverable vs not, report rate to validate "majority of common requests" claim.

**v2 (codegen entry criteria — must ALL be met OR marketplace coverage drops below 50% of intent classes after 6 months from v1 ship):**
1. Concrete sandbox profile (checked-in `sandbox-exec` SBPL profile for macOS, OR `bwrap` argv for Linux with explicit mount/syscall restrictions).
2. TTS-roundtrip nonce or equivalent voice-channel binding; speaker-ID FAR/FRR validated against benchmark dataset.
3. 30+ adversarial bash AST classifier corpus (`awk system()`, `sed e`, `curl --upload-file`, `jq @sh`, `cat <(...)`, command substitution, here-documents, redirection variants).
4. Restricted-Python via subprocess interpreter + seccomp-bpf (NOT `exec` in parent).
5. Audio-channel adversary pre-mortem documented + mitigated.

**Other v2 items:**
- Federated capability index.
- Marketplace API integration (replace web-scraping).
- Mid-session MCP hot-reload.

---

## Platform matrix (v1)

- **macOS** (primary target): supported.
- **Linux** (Ubuntu, Debian, RHEL/Fedora): supported. RHEL with disabled user namespaces is fine for v1 since no sandbox runs.
- **Windows / WSL**: out of scope for v1.
- **Containerized deployments**: best-effort. `~/.heare/` paths assume writable home; ephemeral-FS deployments need volume mounts.

---

## Open Questions (route to `.omc/plans/open-questions.md`)

- **OQ-1 (marketplace selection):** skillsmp.com is assumed primary. Canonical MCP registry URL?
- **OQ-2 (signature verification):** RESOLVED (this iteration) — v1 ships `installation_signature_required: bool = False` default + voice warning per unsigned install. v1.1 flips default to `True` when skillsmp.com signing scheme exists.
- **OQ-3 (popularity threshold):** Default 100. Per-marketplace or global?
- **OQ-4 (revocation by voice):** Exact disambiguation prompt script needs voice-UX iteration.
- **OQ-5 (intent-to-skill match algorithm):** rapidfuzz token-set-ratio acceptable as fallback? Benchmark in Step 1.
- **OQ-6 (Top-K injection size):** Default K=5; tunable.
- **OQ-7 (telemetry endpoint):** If opt-in, default endpoint? Consent flow on first opt-in?
- **OQ-8 (marketplace coverage measurement):** Run N=20 sample classification post-v1-ship to validate "majority" claim quantitatively (v1.1 research).

---

## Changelog (Iteration 3 — 16 fixes closed)

- **Fix 1 (MAJOR-A):** Marketplace installs go to `~/.heare/skills/_marketplace/<slug>/`; revoke refuses directories without `.install.json` sidecar (user-authored protected). AC6, AC11 updated. SkillsLoader scans 2-level `_marketplace/` subtree.
- **Fix 2 (MAJOR-B):** All `.mcp.json` references resolve to `workspace_dir / ".mcp.json"`. New `mcp_utils.write_mcp_servers()` helper. `install_mcp_server` reads/writes ONLY through `mcp_utils`. AC17 added.
- **Fix 3 (MAJOR-C):** `SkillsLoader.invalidate()` method added; `install_skill` calls it + `capability_index.rebuild()` post-install. AC16 added: skill installed at turn N callable at turn N+1.
- **Fix 4 (MAJOR-D):** Signature scheme decision baked in: `installation_signature_required: bool = False` default for v1; voice warning per unsigned install; v1.1 flips to `True` once signing exists.
- **Fix 5 (MAJOR-E):** Install confirm flow latency budget AC15 added (<=5s human time). Voice readout trimmed to 2 attributes (hostname + summary); warnings only on red flags. EN + UK templates specified.
- **Fix 6 (MAJOR-F):** SSRF/supply-chain hardening: hostname allowlist (default `["skillsmp.com", "github.com"]`), SHA-256 checksum verification (added to `IndexEntry.checksum`), homoglyph rejection. AC13 + AC21 cover all three blocks.
- **Fix 7 (NEW-MAJOR-G):** Step 12 (`proactivity_level`) CUT from v1 per Critic recommendation (b); always remote-discover on local miss; defer gating to v1.1.
- **Fix 8 (NEW-MAJOR-H):** Cache HMAC using key from `~/.heare/identity.json`; tampered files rejected + deleted; cache dir permissions `0700`. AC19 added.
- **Fix 9 (NEW-MAJOR-I):** `intent_hash = SHA-256(NFKC.lowercase().collapse_whitespace(intent))`. Cache dir `0700`. AC18 added.
- **Fix 10 (NEW-MINOR-J):** Adversarial mixed-failure circuit-breaker test added to `test_circuit_breaker.py` (sig-fail + net-fail + safety-block alternating).
- **Fix 11 (NEW-MINOR-K):** UK translations bound to existing multi-language system-prompt template; AC8 covers EN + UK refusal strings. Install-refusal + outage-fallback EN/UK templates also specified.
- **Fix 12 (carry-over CRITICAL-5):** Speaker-ID OR `confirmation_passphrase` is HARD requirement for installs (no "where available" hedge). AC20 added. Refusal voice template specified EN + UK.
- **Fix 13 (pre-mortem additions):** Scenario 5 (marketplace API outage) + Scenario 6 (malformed JSON / schema change) added; total 6 scenarios (deliberate min is 3).
- **Fix 14 (AC3 strengthening):** AC3 now requires >0 entries from EACH of 4 sources after populated session.
- **Fix 15 (AC10 + AC13 specifics):** AC10 length bound (count >5 -> summary form). AC13 specifies one disposition per case (block/flag).
- **Fix 16 (>95% claim softened):** Removed quantitative claim; rephrased to "majority of common requests"; v1.1 research item added (OQ-8) to validate quantitatively post-ship.

---

## Plan generation status

Status: REVISED (Iteration 3/5). All 16 fixes from Architect ITERATE + Critic ITERATE addressed. Ready for re-review.

Next step: hand to Architect + Critic for verdict on iteration 3.
