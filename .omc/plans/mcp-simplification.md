# MCP Simplification — RALPLAN-DR

**Date:** 2026-04-23
**Branch:** s2s-realtime
**Status:** DRAFT — awaiting user confirmation

---

## RALPLAN-DR Summary

### Principles (5)

1. **Single source of truth per concern.** One file decides which MCP servers exist and launch; one mechanism decides which are callable. No dual-list gate.
2. **User edits files, not CLI commands.** MCP server management happens in well-known config files, not through `heare mcp enable/disable` subcommands.
3. **The agent SDK already reads `.mcp.json`.** Heare should not duplicate or shadow what the SDK already does natively.
4. **The generator prompt still needs to know what tools exist.** Removing the catalog is fine only if we replace its role as the source of Ukrainian descriptions and tool-name patterns.
5. **Two-pipe architecture is forward-compatible.** *(DEFERRED — Option C naturally evolves from Option A when the first built-in MCP server ships.)* Separating "heare-bundled servers" from "user-provided servers" keeps the door open for future built-in MCP servers without re-architecting.

### Decision Drivers (top 3)

| # | Driver | Weight |
|---|--------|--------|
| 1 | **Minimize moving parts** — remove the dual-list gate (`.mcp.json` + `enable_mcp_servers`) and the catalog indirection layer | HIGH |
| 2 | **Preserve prompt injection** — the generator LLM must still see a list of available MCP tools with human-readable descriptions | HIGH |
| 3 | **No breakage for existing users** — users with `enable_mcp_servers` in config.toml and entries in `.mcp.json` must have a smooth migration | MEDIUM |

### Viable Options

#### Option A: `.mcp.json` as single source of truth (RECOMMENDED)

Delete the catalog layer entirely. Read `workspace/.mcp.json` directly at startup. Every server key in `mcpServers` is automatically allowed (no separate allowlist). Derive prompt descriptions from a new optional `description` field in each `.mcp.json` entry, falling back to the server name.

**Pros (bounded):**
- Removes ~1,000 LOC (mcp.py + test_mcp.py + catalog JSON + CLI commands + ConfigWatcher + config.toml expansion)
- One file is the truth: `.mcp.json` controls both launch and allowlist
- Users already know `.mcp.json` format from Claude Code ecosystem
- No config.toml coordination required

**Cons (bounded):**
- Loses the curated Ukrainian descriptions from `mcp_catalog.json` (mitigated: descriptions can be added inline in `.mcp.json` or derived from server name)
- `.mcp.json` is a workspace file, not a user-config file — mixing launch config with description metadata is slightly impure
- Hot-reload of MCP list requires restart (acceptable per user confirmation)
- Existing `enable_mcp_servers` users need a one-time migration

#### Option B: Keep `enable_mcp_servers` as sole config, drop catalog

Keep `enable_mcp_servers` in config.toml as the only heare-side config. Drop the catalog, drop `.mcp.json` management code, drop ConfigWatcher. Agent SDK still reads `.mcp.json` (user-managed) but heare only tracks which names are allowed.

**Pros:**
- Minimal code change — mostly deletion
- Preserves the "explicit opt-in" safety model

**Cons:**
- Still a dual-list gate: server must be in BOTH `.mcp.json` AND `enable_mcp_servers`
- Users must keep two files in sync manually — the core problem remains
- No source for prompt descriptions without the catalog

**INVALIDATED:** This option preserves the exact dual-list problem the user wants to eliminate. The user explicitly confirmed "no voice UX for toggling MCP servers" and "users edit config by hand; restart-on-change is acceptable," which removes the rationale for a separate allowlist.

#### Option C: Two-pipe split (`mcp_builtin.py` + raw `.mcp.json` passthrough)

Create `src/mcp_builtin.py` as a hardcoded registry of heare-owned servers (Pipe A). User servers come from `.mcp.json` passthrough (Pipe B). No catalog JSON file.

**Pros:**
- Clean conceptual separation of heare-managed vs user-managed
- Forward-compatible for when heare ships built-in MCP servers

**Cons:**
- Adds a new module for a feature that does not yet exist (no built-in servers today)
- YAGNI — premature abstraction for Pipe A
- Still need to solve the prompt-description problem for Pipe B

**DEFERRED:** The two-pipe model is the right *future* architecture, but building Pipe A now (with zero built-in servers) adds code for no immediate value. Option A can evolve into Option C later by adding `mcp_builtin.py` when the first built-in server ships.

---

## ADR Stub

**Decision:** Option A — `.mcp.json` as single source of truth

**Drivers:**
1. Eliminate the dual-list gate that forces users to keep `.mcp.json` and `enable_mcp_servers` in sync
2. Reduce MCP management code from ~1,400 LOC across 8 files to ~80 LOC in 2 files
3. Align with the claude-agent-sdk's native `.mcp.json` consumption model

**Alternatives considered:**
- Option B (keep `enable_mcp_servers` only): Invalidated — preserves the dual-list problem
- Option C (two-pipe split): Deferred — YAGNI for Pipe A; Option A evolves into C naturally

**Why chosen:**
Option A is the only option that achieves the user's stated goal ("one source of truth, no dual-list gate") while remaining minimal. The SDK already reads `.mcp.json` to spawn subprocesses. Making heare read the same file for allowlist + prompt descriptions collapses three data sources into one.

**Consequences:**
- `heare mcp list/enable/disable/status/setup/edit-catalog` CLI commands are removed
- `enable_mcp_servers` config key is deprecated (ignored with a warning on startup if present)
- Ukrainian descriptions for MCP tools are derived from an optional `description` field in `.mcp.json` entries or from the server key name
- Hot-reload of MCP list is removed (restart required); this is acceptable per user confirmation
- The bundled catalog (`data/mcp_catalog.json`) and custom catalog (`~/.heare/custom_mcp_catalog.json`) concepts are eliminated
- **Side-benefit:** Eliminates a pre-existing double-expansion bug where both `config.py:208-215` AND `agent_sdk_cli.py:160-163` independently expanded `enable_mcp_servers` into `allowed_tools`, producing duplicate wildcard patterns. The new single-derivation path through `mcp_utils` makes this class of bug structurally impossible.

**Follow-ups:**
- When the first heare-built-in MCP server ships, add `src/mcp_builtin.py` (Pipe A) that merges its entries into the allowlist + prompt descriptions alongside `.mcp.json` entries
- Consider adding a `heare mcp doctor` command that validates `.mcp.json` entries can actually launch

---

## Migration Story

### For users with `enable_mcp_servers` in config.toml + entries in `.mcp.json`:

1. On startup, `load_settings()` detects `enable_mcp_servers` is set and logs a deprecation warning: `"enable_mcp_servers is deprecated. All servers in workspace/.mcp.json are now automatically enabled. Remove enable_mcp_servers from config.toml. Servers listed in enable_mcp_servers that are not yet in workspace/.mcp.json must be added there manually — the server will not launch otherwise."`
2. The field is silently ignored — it no longer gates anything.
3. `.mcp.json` entries continue to work as before (the SDK reads them).
4. The user removes `enable_mcp_servers` from config.toml at their leisure.

### For users with entries in `mcp_catalog.json` or `custom_mcp_catalog.json`:

1. These files are no longer read. The deprecation log message mentions this.
2. Any servers the user wants must already be in `workspace/.mcp.json` (which the `enable_server` function previously wrote to).

### Net effect: zero breakage if `.mcp.json` is already populated (which it is for any user who ran `heare mcp enable <name>`).

---

## Implementation Plan

### Step 1: Add `.mcp.json` reader utility (new: `src/mcp_utils.py`, ~40 LOC)

Create a small utility module that:
- Reads `workspace/.mcp.json` and returns the dict of `mcpServers`
- **Must gracefully handle:** missing file, invalid JSON, missing `mcpServers` key, wrong types — return empty set + log warning in all error cases (<10 LOC)
- For each server entry, extracts an optional `description` field (new convention) or falls back to the server key name
- Builds the `mcp__<name>__*` wildcard patterns for `allowed_tools`
- Builds the Ukrainian prompt description block (replacing `_get_enabled_mcp_descriptions`)

**Acceptance criteria:**
- `read_mcp_servers(workspace_dir) -> dict[str, MCPServerInfo]` returns parsed server entries
- `build_mcp_allowed_patterns(servers) -> list[str]` returns `["mcp__<name>__*", ...]`
- `build_mcp_prompt_block(servers) -> str` returns formatted Ukrainian text or empty string
- Unit tests cover: empty `.mcp.json`, populated `.mcp.json`, `.mcp.json` with description field, missing file, malformed JSON, missing `mcpServers` key, wrong types (all error cases return empty set + log warning)

### Step 2: Rewire `config.py` and `agent_sdk_cli.py` to use `.mcp.json` directly (~60 LOC changed)

- **`src/config.py`:**
  - **Keep** `enable_mcp_servers: list[str] = field(default_factory=list)` in the `Settings` dataclass, marked `# DEPRECATED — remove in next release`. The field is retained for exactly one release so that `load_settings()` can detect its presence in TOML data and emit a deprecation warning. It is NOT used for any runtime logic (no expansion, no allowlist gating).
  - Remove the "Phase MCP-wiring" expansion block (lines 208-220) that expanded `enable_mcp_servers` into `allowed_tools`
  - Remove `ConfigWatcher` class entirely (lines 225-313)
  - Add deprecation warning in `load_settings()` if `enable_mcp_servers` key exists in TOML data (migration path)
  - **Follow-up (next release):** Remove the `enable_mcp_servers` field from the dataclass entirely and drop the deprecation warning code.

- **`src/agent_sdk_cli.py`:**
  - In `_open_client()`, replace the `enable_mcp_servers` expansion (lines 160-163) with a call to `mcp_utils.read_mcp_servers()` + `build_mcp_allowed_patterns()`
  - The allowed_tools list is now: base tools + patterns derived from `.mcp.json`

**Acceptance criteria:**
- `ConfigWatcher` class no longer exists
- `agent_sdk_cli._open_client()` builds allowed_tools from `.mcp.json` without referencing `enable_mcp_servers`
- Deprecation warning is logged when `enable_mcp_servers` is in config.toml
- `make test` passes (existing agent_sdk_cli tests adapted)

### Step 3: Rewire `generator.py` to use `mcp_utils` instead of catalog (~40 LOC changed)

- **`src/generator.py`:**
  - Replace `_get_enabled_mcp_descriptions()` (lines 80-104) with a call to `mcp_utils.build_mcp_prompt_block()`
  - Remove `ConfigWatcher` integration from `GeneratorProcessor.__init__` (lines 184-196)
  - Remove `_on_mcp_config_changed` callback (lines 198-214)
  - Remove the first `shutdown()` method (lines 216-220) that cleaned up the watcher (the second shutdown at line 505 stays)
  - `_mcp_descriptions` is now computed once at init from `.mcp.json` (no hot-reload; restart required)

**Acceptance criteria:**
- Generator prompt still contains MCP server descriptions when servers exist in `.mcp.json`
- No `ConfigWatcher` references remain in generator.py
- No `from .mcp import` references remain in generator.py
- `make test` passes (generator tests adapted)

### Step 4: Delete catalog layer, CLI commands, and old tests (~1,000 LOC removed)

- **Delete files:**
  - `src/mcp.py` (372 lines)
  - `data/mcp_catalog.json` (756 lines)
  - `tests/test_mcp.py` (248 lines)

- **Modify `src/main.py`:**
  - Remove `_cmd_mcp_setup`, `_cmd_mcp_list`, `_cmd_mcp_status`, `_cmd_mcp_enable`, `_cmd_mcp_disable`, `_cmd_mcp_edit_catalog`, `_cmd_mcp` (lines 722-850)
  - Remove the `mcp` argparse subparser (lines 904-913)
  - Remove `if cmd == "mcp"` routing (line 944-945)
  - Keep `_ensure_workspace_mcp()` (lines 106-132) — it seeds `.mcp.json` from `~/.claude.json` on first run and is still valuable
  - **Mitigate seeding escalation (sub-task 4a):** Modify `_ensure_workspace_mcp()` to log a prominent `WARNING`-level message at startup listing all server names that were auto-seeded from `~/.claude.json` into `workspace/.mcp.json`. Example: `"Auto-authorized MCP servers from ~/.claude.json: github, notion, filesystem. All servers in workspace/.mcp.json are now callable by the agent. Review and remove any unwanted entries."` This is <=10 LOC, adds zero config surface, and keeps the user informed of the privilege widening. The warning fires only when seeding actually occurs (first run or new servers added), not on every startup.

**Acceptance criteria:**
- `src/mcp.py` does not exist
- `data/mcp_catalog.json` does not exist
- `tests/test_mcp.py` does not exist
- `heare mcp` CLI is gone (running it produces argparse "unknown command" error)
- `_ensure_workspace_mcp()` still seeds `.mcp.json` on first run
- `_ensure_workspace_mcp()` logs a WARNING listing auto-seeded server names when seeding occurs
- `make test` passes

### Step 5: Update tests and documentation (~100 LOC changed/added)

- **New tests in `tests/test_mcp_utils.py`:**
  - `test_read_mcp_servers_empty` — empty `.mcp.json`
  - `test_read_mcp_servers_populated` — returns correct server names
  - `test_build_allowed_patterns` — produces `mcp__<name>__*` list
  - `test_build_prompt_block_with_description` — description field used
  - `test_build_prompt_block_without_description` — falls back to name
  - `test_read_mcp_servers_missing_file` — returns empty dict
  - `test_read_mcp_servers_malformed_json` — returns empty dict + logs warning
  - `test_read_mcp_servers_missing_key` — `.mcp.json` without `mcpServers` key returns empty dict + logs warning
  - `test_read_mcp_servers_wrong_types` — `mcpServers` is not a dict returns empty dict + logs warning

- **Adapt existing tests:**
  - `tests/test_config.py`: Remove `test_enable_mcp_servers_defaults_empty`, `test_readme_documents_mcp_workflow`. Add `test_deprecated_enable_mcp_servers_warning`.
  - `tests/test_agent_sdk_cli.py`: Update `test_sdk_mcp_servers_*` tests to mock `.mcp.json` instead of `enable_mcp_servers`.
  - `tests/test_generator.py`: No MCP-specific tests to change (existing tests don't test MCP paths directly).
  - `tests/test_generator_prompt.py`: Update `test_template_has_exactly_expected_placeholders` (mcp_servers placeholder stays). Update `test_substitution_leaves_no_placeholders`.

- **Update `README.md`:**
  - Replace the two-step MCP setup instructions with single-step: "Edit `workspace/.mcp.json` to add servers; restart heare."
  - Remove references to `enable_mcp_servers`.

**Acceptance criteria:**
- `make test` passes with zero failures
- No test references `enable_mcp_servers` as a live feature (only deprecation tests)
- README documents the new single-file workflow

---

## File-Level Change List

### Files deleted (3)
| File | Lines | Reason |
|------|-------|--------|
| `src/mcp.py` | 372 | Catalog layer replaced by `mcp_utils.py` |
| `data/mcp_catalog.json` | 756 | Bundled catalog no longer needed |
| `tests/test_mcp.py` | 248 | Tests for deleted catalog layer |

### Files created (2)
| File | Est. Lines | Purpose |
|------|-----------|---------|
| `src/mcp_utils.py` | ~60 | Read `.mcp.json` (with graceful error handling), build allowed patterns + prompt block |
| `tests/test_mcp_utils.py` | ~120 | Unit tests for mcp_utils (including malformed JSON, missing keys, wrong types) |

### Files modified (8)
| File | Change scope |
|------|-------------|
| `src/config.py` | Remove ConfigWatcher (~90 lines), deprecation warning for enable_mcp_servers |
| `src/agent_sdk_cli.py` | Replace enable_mcp_servers expansion with mcp_utils call (~10 lines) |
| `src/generator.py` | Replace _get_enabled_mcp_descriptions + ConfigWatcher with mcp_utils call (~40 lines) |
| `src/main.py` | Remove MCP CLI commands (~130 lines) |
| `tests/test_config.py` | Adapt MCP-related tests (~15 lines) |
| `tests/test_agent_sdk_cli.py` | Adapt MCP expansion tests (~30 lines) |
| `tests/test_generator_prompt.py` | Minor: placeholder set assertion stays same (~5 lines) |
| `README.md` | Update MCP setup instructions (~10 lines) |

### Files unchanged
| File | Why |
|------|-----|
| `prompts/generator.txt` | `{mcp_servers}` placeholder stays; content injected by mcp_utils |
| `src/context.py` | No MCP references |
| `~/.heare/config.toml` | User file; deprecated key ignored with warning |

---

## Test Impact

| Category | Count | Details |
|----------|-------|---------|
| Tests deleted | 8 | All tests in `tests/test_mcp.py` |
| Tests adapted | 6 | 3 in test_agent_sdk_cli, 2 in test_config, 1 in test_generator_prompt |
| New tests | 9 | All in `tests/test_mcp_utils.py` (6 original + 3 error-handling cases) |
| Net change | +7 tests | More focused, less indirection, better error coverage |

---

## Rough LOC Delta

| Category | Lines |
|----------|-------|
| Deleted (mcp.py + catalog + test_mcp + ConfigWatcher + CLI commands) | **-1,596** |
| Added (mcp_utils.py + test_mcp_utils.py + error handling + seeding warning) | **+180** |
| Modified (config, agent_sdk_cli, generator, main, test adaptations) | **~100 net removed** |
| **Net delta** | **~-1,516** |

---

## Acceptance Criteria (post-change verification)

1. `make test` passes with zero failures
2. Agent can call `mcp__<name>__*` tools when the server is defined in `workspace/.mcp.json`. **Verification:** Add an integration test (or smoke-test script) that enables one dummy MCP server in a test `.mcp.json`, runs heare in test mode, asserts that `agent_sdk_allowed_tools` contains `mcp__<dummy>__*`, and sends a test intent that routes to the MCP pipe.
3. Generator prompt contains a list of available MCP tools (server names + descriptions) when `.mcp.json` has entries
4. Generator prompt has empty/absent MCP block when `.mcp.json` has no entries
5. `heare mcp` CLI is gone (argparse error)
6. `src/mcp.py` and `data/mcp_catalog.json` do not exist
7. No `ConfigWatcher` class exists anywhere in the codebase
8. Startup with `enable_mcp_servers` in config.toml logs a deprecation warning but does not crash
9. `_ensure_workspace_mcp()` still seeds `.mcp.json` from `~/.claude.json` on first run
10. `workspace/.mcp.json` entries with an optional `description` field produce Ukrainian prompt text; entries without it fall back to the server key name

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Users who only have `enable_mcp_servers` but never ran `heare mcp enable` (so `.mcp.json` is empty) lose MCP access silently | LOW (enable_server always wrote to .mcp.json) | MEDIUM | Deprecation warning explicitly tells them to populate .mcp.json |
| Generator prompt quality degrades without curated Ukrainian descriptions | LOW | LOW | Descriptions can be added inline in .mcp.json; server names are already meaningful (github, notion, etc.) |
| Future Pipe A (built-in servers) requires re-architecture | LOW | LOW | mcp_utils.py is designed to be extended; add a `get_builtin_servers()` function when needed |
| Seeding escalation: `_ensure_workspace_mcp()` auto-seeds from `~/.claude.json`, and under Option A every seeded server becomes immediately authorized — privilege widening vs. the old `enable_mcp_servers` gate | MEDIUM | MEDIUM | Sub-task 4a: log a prominent WARNING listing all auto-seeded server names so the user can review and remove unwanted entries before the agent calls them |

---

## Deferred Decisions

| # | Decision | Status | Why deferred | Revisit trigger |
|---|----------|--------|-------------|-----------------|
| D1 | **`.mcp.json` description field naming convention** — Should the field be `description`, `description_uk`, or both? If both, which takes priority for the Ukrainian prompt? | DEFERRED | Non-blocking for v1. The `description` field works as a reasonable default; the naming can be refined later without breaking changes since the field is optional and heare-specific. | If heare adds multi-language prompt support, or if upstream `.mcp.json` spec adopts a `description` field with conflicting semantics. |
| D2 | **Prompt quality without curated Ukrainian descriptions** — After catalog deletion, descriptions fall back to server key names (e.g., "notion" instead of "Сторінки та бази даних Notion"). Is the quality regression acceptable? | DEFERRED | Non-blocking for v1. Server names are already meaningful, and users can add inline `description` fields. A hardcoded mapping of common server names to Ukrainian descriptions can be added to `mcp_utils.py` later if needed. | If prompt quality regresses per acceptance criterion #3 (generator prompt quality), or if user reports degraded agent tool selection accuracy. |
| D3 | **One-time migration helper for `enable_mcp_servers` entries** — Should seeding also copy servers from the deprecated `enable_mcp_servers` + `mcp_catalog.json` into `.mcp.json` as a one-time migration, or is the deprecation warning sufficient? | DEFERRED | The deprecation warning (revised in this plan to explicitly tell users to populate `.mcp.json` manually) is sufficient for v1. A migration helper adds complexity for a transient problem that self-resolves as users follow the warning. | If user reports indicate that the warning-only approach causes significant friction or silent MCP access loss. |
