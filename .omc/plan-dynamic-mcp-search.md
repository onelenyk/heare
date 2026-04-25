# Static MCP Catalog — Implementation Plan

**Project**: heare voice AI assistant
**Feature**: Pre-curated MCP catalog with simple enable/disable commands
**Status**: Simplified per user feedback
**Created**: 2025-04-22
**Revised**: 2025-04-22 (COMPLETE REWRITE - Much Simpler)
**Revised**: 2025-04-22 (Iteration 2 - Critical fixes from Architect & Critic)
**Revised**: 2025-04-22 (Iteration 3 - Planner fixes: remove MCPConfigManager, fix Settings.dict(), wire SDK reconnect, clean artifacts)

---

## Executive Summary

**New Approach: Static catalog + enable/disable only**

After user feedback, the dynamic search/install approach was rejected in favor of:
- **No marketplace search** — no npm registry queries
- **No dynamic npm installation** — packages pre-installed or manual one-time setup
- **Static catalog** — curated JSON of 50+ known MCP servers
- **Simple commands** — enable/disable/list/status/setup/edit-catalog
- **Fast rollout** — can ship in days, not weeks

This is SIGNIFICANTLY simpler than the previous plan.

**Key Changes in Iteration 2 (All CRITICAL fixes applied):**

1. **FIXED Generator Suggestion Logic (CRITICAL #1)**: Now pre-checks MCP tools
   BEFORE IntentQueue.submit() to detect disabled servers. The plan documents
   the exact implementation in generator.py's _submit_intent() method.

2. **FIXED Dropped MCPConfigManager Class (CRITICAL #2)**: Replaced with simple
   functions in src/mcp/config.py — enable_server(), disable_server(), list_servers().

3. **FIXED Custom Catalog in Phase 1 (CRITICAL #3)**: MCPCatalog now loads
   bundled + custom catalogs, merging them with custom overriding bundled.

4. **FIXED Removed Dishonest Security Claims (CRITICAL #4)**: Comparison table
   now honestly states this is UX convenience, not a security feature. Added note
   explaining users run the same npm packages either way.

5. **FIXED Auth Visibility (MAJOR #5)**: CLI list output now shows
   "[потрібен ключ]" for auth-required MCPs.

6. **FIXED Orphaned .mcp.json Behavior (MAJOR #6)**: Documented that disabled
   servers stay in .mcp.json but are blocked by enable_mcp_servers allowlist.

7. **ADDED edit-catalog command**: New CLI command to open custom_mcp_catalog.json
   in $EDITOR for easy customization.

---

## 1. Architecture Overview

### 1.1 System Context

**Current State:**
- MCP servers loaded from `~/.heare/workspace/.mcp.json` on startup
- `enable_mcp_servers` in `config.toml` controls which servers are allowed
- Tool allowlist in `IntentQueue.ALLOWED_TOOLS` rejects unknown tools

**New State:**
- Static catalog at `~/.heare/mcp_catalog.json` with 50+ pre-curated entries
- CLI commands: `heare mcp enable/disable/list/status/setup`
- Generator suggests "увімкни X" when tool is missing
- One-time `heare mcp setup` installs all packages

### 1.2 Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                         CLI Layer                          │
│  heare mcp enable <name> | heare mcp disable <name>        │
│  heare mcp list | heare mcp status | heare mcp setup        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                     MCPCatalog (NEW)                        │
│  Load ~/.heare/mcp_catalog.json + custom_mcp_catalog.json  │
│  Static catalog with 50+ pre-curated entries               │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              Simple Functions (NOT a class)                │
│  mcp_enable() / mcp_disable() / mcp_list()                 │
│  Read/write .mcp.json + config.toml enable_mcp_servers     │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   AgentSDKCLI.reconnect()                   │
│  Reload config when enable/disable changes (existing)      │
└─────────────────────────────────────────────────────────────┘
```

**Architect feedback applied:**
- No MCPConfigManager class — use simple functions instead
- Custom catalog checked first, then merged with bundled

### 1.3 Data Flow

**Enable Flow:**
```
$ heare mcp enable notion
→ Reads mcp_catalog.json for notion entry
→ Adds notion to enable_mcp_servers in config.toml
→ Adds entry to .mcp.json if not present
→ Reloads SDK (reconnect)
→ "Notion увімкнено. Що робити?"
```

**Disable Flow:**
```
$ heare mcp disable github
→ Removes github from enable_mcp_servers in config.toml
→ Reloads SDK (reconnect)
→ "GitHub вимкнено."
```

**List Flow:**
```
$ heare mcp list
→ Shows all 50+ entries from catalog
→ Marks which are enabled
→ Shows descriptions
```

**Generator Suggestion:**
```
User: "додай в notion"
  → Generator: tool not allowed
  → Generator: "Увімкніть Notion MCP: скажіть 'увімкни notion'"
```

---

## 2. File Structure

### 2.1 New Files

```
src/
├── mcp/
│   ├── __init__.py
│   ├── catalog.py           # MCPCatalog class + load/merge functions
│   └── config.py            # Simple functions: enable/disable/list (no class)

data/
└── mcp_catalog.json         # Bundled static catalog of 50+ MCPs

tests/
├── test_mcp_catalog.py
└── test_mcp_config.py
```

### 2.2 Modified Files

```
src/
├── config.py                # Add get_custom_catalog_path() helper
├── agent_sdk_cli.py         # Already has reconnect(), no changes needed
├── main.py                  # Add 'mcp' subcommand
└── generator.py             # Pre-check MCP tools + suggest "увімкни X"
```

---

## 3. Data Models

### 3.1 MCP Catalog Format

**Bundled catalog** (`data/mcp_catalog.json`):

```json
{
  "version": "1.0",
  "last_updated": "2025-04-22",
  "servers": {
    "notion": {
      "package": "@modelcontextprotocol/server-notion",
      "description": "Notion pages & databases",
      "description_uk": "Сторінки та бази даних Notion",
      "auth_required": true,
      "official": true,
      "mcp_json": {
        "type": "stdio",
        "command": "npx",
        "args": ["@modelcontextprotocol/server-notion"],
        "env": {
          "NOTION_API_KEY": "prompt-user"
        }
      }
    },
    "github": {
      "package": "@modelcontextprotocol/server-github",
      "description": "GitHub issues, PRs, repos",
      "description_uk": "GitHub issues, PRs, репозиторії",
      "auth_required": true,
      "official": true,
      "mcp_json": {
        "type": "stdio",
        "command": "npx",
        "args": ["@modelcontextprotocol/server-github"],
        "env": {
          "GITHUB_TOKEN": "prompt-user"
        }
      }
    },
    "filesystem": {
      "package": "@modelcontextprotocol/server-filesystem",
      "description": "File system access to specified directories",
      "description_uk": "Доступ до файлової системи",
      "auth_required": false,
      "official": true,
      "mcp_json": {
        "type": "stdio",
        "command": "npx",
        "args": ["@modelcontextprotocol/server-filesystem", "/path/to/allow"]
      }
    }
    // ... 47+ more entries
  }
}
```

**Custom catalog** (`~/.heare/custom_mcp_catalog.json`) — optional, overrides/extends bundled:

```json
{
  "version": "1.0",
  "servers": {
    "my-custom-server": {
      "package": "@mycompany/mcp-server",
      "description": "My company's internal MCP server",
      "description_uk": "Внутрішній MCP сервер моєї компанії",
      "auth_required": true,
      "official": false,
      "mcp_json": {
        "type": "stdio",
        "command": "npx",
        "args": ["@mycompany/mcp-server"],
        "env": {
          "MY_API_KEY": "prompt-user"
        }
      }
    }
  }
}
```

**Catalog merge strategy**:
1. Load bundled catalog
2. If custom catalog exists, merge its servers (custom entries override bundled)
3. Final catalog = bundled + custom merged

### 3.2 MCPCatalogEntry

```python
@dataclass
class MCPCatalogEntry:
    """Single entry from MCP catalog."""
    name: str                          # "notion"
    package: str                       # "@modelcontextprotocol/server-notion"
    description: str                   # English description
    description_uk: str                # Ukrainian description
    auth_required: bool                # True if needs API key
    official: bool                     # True if @modelcontextprotocol org
    mcp_json: dict                     # Entry to write to .mcp.json
```

### 3.3 MCPEnableResult

```python
@dataclass
class MCPEnableResult:
    """Result from MCPConfigManager.enable()."""
    success: bool
    server_name: str
    was_already_enabled: bool = False
    sdk_reconnected: bool = False
    error: str = ""
```

---

## 4. API Design

### 4.1 MCPCatalog (with custom catalog support)

```python
class MCPCatalog:
    """Static catalog of available MCP servers.

    Loads bundled catalog first, then merges custom catalog if it exists.
    Custom entries override bundled entries with the same name.
    """

    def __init__(
        self,
        bundled_path: Path | None = None,
        custom_path: Path | None = None,
    ):
        """Load catalog from bundled + optional custom path."""
        from .config import HEARE_HOME

        if bundled_path is None:
            bundled_path = Path(__file__).parent.parent / "data" / "mcp_catalog.json"
        if custom_path is None:
            custom_path = HEARE_HOME / "custom_mcp_catalog.json"

        self.bundled_path = bundled_path
        self.custom_path = custom_path
        self._data = self._load_and_merge()

    def _load_and_merge(self) -> dict:
        """Load bundled catalog, merge with custom if exists."""
        if not self.bundled_path.exists():
            raise FileNotFoundError(
                f"Bundled catalog not found at {self.bundled_path}. "
                "This should never happen — it's shipped with heare."
            )

        data = json.loads(self.bundled_path.read_text())

        # Merge custom catalog if it exists
        if self.custom_path.exists():
            try:
                custom = json.loads(self.custom_path.read_text())
                custom_servers = custom.get("servers", {})
                if custom_servers:
                    data["servers"].update(custom_servers)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load custom catalog: %s", e)

        return data

    def get(self, name: str) -> MCPCatalogEntry | None:
        """Get entry by server name."""
        entry = self._data.get("servers", {}).get(name)
        if not entry:
            return None
        return MCPCatalogEntry(name=name, **entry)

    def list_all(self) -> list[MCPCatalogEntry]:
        """List all catalog entries."""
        return [
            MCPCatalogEntry(name=k, **v)
            for k, v in self._data.get("servers", {}).items()
        ]

    def search(self, query: str) -> list[MCPCatalogEntry]:
        """Search catalog by name or description."""
        query = query.lower()
        results = []
        for entry in self.list_all():
            if (query in entry.name or
                query in entry.description.lower() or
                query in entry.description_uk.lower()):
                results.append(entry)
        return results

    @property
    def has_custom_catalog(self) -> bool:
        """True if custom catalog exists and was merged."""
        return self.custom_path.exists()
```

### 4.2 Simple Config Functions (replaces MCPConfigManager class)

```python
# In src/mcp/config.py — simple functions, no class

from pathlib import Path
import json
import tomli_w


def get_enabled_servers(settings: Settings) -> set[str]:
    """Get currently enabled server names from config."""
    return set(settings.enable_mcp_servers or [])


def _load_mcp_json(workspace_dir: Path) -> dict:
    """Load current .mcp.json."""
    mcp_json_path = workspace_dir / ".mcp.json"
    if not mcp_json_path.exists():
        return {"mcpServers": {}}
    return json.loads(mcp_json_path.read_text())


def _save_mcp_json(workspace_dir: Path, data: dict) -> None:
    """Save .mcp.json."""
    mcp_json_path = workspace_dir / ".mcp.json"
    mcp_json_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_json_path.write_text(json.dumps(data, indent=2))


def _save_config(config_path: Path, settings: Settings) -> None:
    """Save config.toml."""
    from dataclasses import asdict
    config_path.parent.mkdir(parents=True, exist_ok=True)
    # Settings is a dataclass, use asdict() instead of .dict()
    settings_dict = {k: v for k, v in asdict(settings).items() if not k.startswith('_')}
    config_path.write_text(tomli_w.dumps(settings_dict))


def enable_server(
    server_name: str,
    catalog: MCPCatalog,
    settings: Settings,
    config_path: Path,
) -> MCPEnableResult:
    """Enable an MCP server.

    Steps:
    1. Look up server in catalog
    2. Add entry to .mcp.json if not present
    3. Add to enable_mcp_servers in config.toml
    4. (Caller handles SDK reconnect)

    Returns:
        MCPEnableResult with status
    """
    entry = catalog.get(server_name)
    if not entry:
        return MCPEnableResult(
            success=False,
            server_name=server_name,
            error=f"Unknown server: {server_name}"
        )

    enabled = get_enabled_servers(settings)
    if server_name in enabled:
        return MCPEnableResult(
            success=True,
            server_name=server_name,
            was_already_enabled=True
        )

    # Add to .mcp.json
    mcp_data = _load_mcp_json(settings.workspace_dir)
    mcp_data["mcpServers"][server_name] = entry.mcp_json
    _save_mcp_json(settings.workspace_dir, mcp_data)

    # Add to enable_mcp_servers
    if settings.enable_mcp_servers is None:
        settings.enable_mcp_servers = []
    settings.enable_mcp_servers.append(server_name)
    _save_config(config_path, settings)

    return MCPEnableResult(success=True, server_name=server_name)


def disable_server(
    server_name: str,
    settings: Settings,
    config_path: Path,
) -> bool:
    """Disable an MCP server.

    Removes from enable_mcp_servers in config.toml.
    Keeps .mcp.json entry for re-enable later (orphaned behavior).

    NOTE: Disabled servers stay in .mcp.json but are blocked by
    enable_mcp_servers allowlist. This is intentional — keeps user
    configs (API keys, paths) for easy re-enable.

    Returns:
        True if disabled, False if not enabled
    """
    enabled = get_enabled_servers(settings)
    if server_name not in enabled:
        return False

    settings.enable_mcp_servers.remove(server_name)
    _save_config(config_path, settings)
    return True


def list_servers(
    catalog: MCPCatalog,
    settings: Settings,
) -> dict[str, dict]:
    """List all servers with enable status.

    Returns:
        {
            "notion": {"enabled": true, "description": "...", "auth_required": true},
            "github": {"enabled": false, "description": "...", "auth_required": true},
        }
    """
    enabled = get_enabled_servers(settings)
    return {
        entry.name: {
            "enabled": entry.name in enabled,
            "description": entry.description_uk,
            "auth_required": entry.auth_required,
            "official": entry.official,
        }
        for entry in catalog.list_all()
    }
```

### 4.3 CLI Commands (updated for simple functions)

```python
def _cmd_mcp(args: argparse.Namespace) -> int:
    """Handle 'heare mcp' subcommands."""
    from .mcp.catalog import MCPCatalog
    from .mcp.config import (
        enable_server,
        disable_server,
        list_servers,
    )
    from .config import HEARE_HOME, load_settings

    try:
        catalog = MCPCatalog()
    except FileNotFoundError:
        print("Каталог MCP не знайдено.")
        print("Ця помилка не повинна траплятися — каталог вбудований в heare.")
        return 1

    settings = load_settings()
    config_path = HEARE_HOME / "config.toml"

    sub = args.mcp_cmd
    if sub == "enable":
        return _cmd_mcp_enable(args, catalog, settings, config_path)
    if sub == "disable":
        return _cmd_mcp_disable(args, catalog, settings, config_path)
    if sub == "list":
        return _cmd_mcp_list(args, catalog, settings)
    if sub == "status":
        return _cmd_mcp_status(args, catalog, settings)
    if sub == "setup":
        return _cmd_mcp_setup(args)
    if sub == "edit-catalog":
        return _cmd_mcp_edit_catalog(args)
    return 1


def _cmd_mcp_enable(args, catalog, settings, config_path) -> int:
    """Enable an MCP server."""
    result = enable_server(args.server_name, catalog, settings, config_path)

    if not result.success:
        print(f"Помилка: {result.error}")
        return 1

    if result.was_already_enabled:
        print(f"{args.server_name} вже увімкнено")
        return 0

    print(f"✅ {args.server_name} увімкнено")

    # Phase 1: User needs to restart daemon for changes to take effect
    # Future: Could add AgentSDKCLI.reconnect() call here, or file-watch reload
    print("Run: heare stop && heare start")
    return 0


def _cmd_mcp_disable(args, catalog, settings, config_path) -> int:
    """Disable an MCP server."""
    success = disable_server(args.server_name, settings, config_path)

    if not success:
        print(f"{args.server_name} не було увімкнено")
        return 1

    print(f"✅ {args.server_name} вимкнено")

    # Phase 1: User needs to restart daemon for changes to take effect
    # Future: Could add AgentSDKCLI.reconnect() call here, or file-watch reload
    print("Run: heare stop && heare start")
    return 0


def _cmd_mcp_list(args, catalog, settings) -> int:
    """List all MCP servers with auth visibility."""
    servers = list_servers(catalog, settings)

    for name, info in sorted(servers.items()):
        status = "✓" if info["enabled"] else " "
        official = "[офіційний]" if info["official"] else ""
        # Show auth visibility for servers that require API keys
        auth = "[потрібен ключ]" if info["auth_required"] else ""
        custom = "[кастомний]" if not info["official"] else ""
        print(f" {status} {name:20} {official} {custom} {auth}")
        print(f"    {info['description']}")
    return 0


def _cmd_mcp_status(args, catalog, settings) -> int:
    """Show enabled servers only."""
    servers = list_servers(catalog, settings)
    enabled = [n for n, i in servers.items() if i["enabled"]]

    if not enabled:
        print("Немає увімкнених MCP серверів")
        return 0

    print("Увімкнені MCP сервери:")
    for name in enabled:
        entry = catalog.get(name)
        print(f"  {name}: {entry.description_uk}")
    return 0


def _cmd_mcp_setup(args) -> int:
    """One-time setup: install catalog and optional npm packages.

    NOTE: Catalog is now bundled with heare, so this just confirms
    the catalog exists and optionally installs npm packages.
    """
    from .config import HEARE_HOME

    bundled_catalog = Path(__file__).parent.parent / "data" / "mcp_catalog.json"
    if not bundled_catalog.exists():
        print("Вбудований каталог не знайдено. Перевстановіть heare.")
        return 1

    print(f"✅ Вбудований каталог знайдено: {bundled_catalog}")

    custom_catalog = HEARE_HOME / "custom_mcp_catalog.json"
    if custom_catalog.exists():
        print(f"✅ Кастомний каталог знайдено: {custom_catalog}")
    else:
        print(f"💡 Порада: створіть {custom_catalog} для власних MCP серверів")

    if args.install_packages:
        print("Встановлення npm пакетів...")
        catalog = MCPCatalog()
        packages = set(
            entry.package
            for entry in catalog.list_all()
        )
        for pkg in sorted(packages):
            print(f"  npm install -g {pkg}")
            # subprocess.run(["npm", "install", "-g", pkg])

    print("\nВикористання:")
    print("  heare mcp list         - список всіх MCP")
    print("  heare mcp enable <x>   - увімкнути MCP")
    print("  heare mcp disable <x>  - вимкнути MCP")
    print("  heare mcp status       - показати увімкнені")
    print("  heare mcp edit-catalog - відкрити кастомний каталог")
    return 0


def _cmd_mcp_edit_catalog(args) -> int:
    """Open custom catalog in editor for manual editing."""
    from .config import HEARE_HOME
    import subprocess
    import os

    custom_catalog = HEARE_HOME / "custom_mcp_catalog.json"

    # Create empty custom catalog if it doesn't exist
    if not custom_catalog.exists():
        template = {
            "version": "1.0",
            "servers": {
                "my-server": {
                    "package": "@username/mcp-server",
                    "description": "My custom MCP server",
                    "description_uk": "Мій власний MCP сервер",
                    "auth_required": False,
                    "official": False,
                    "mcp_json": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["@username/mcp-server"],
                    }
                }
            }
        }
        custom_catalog.write_text(json.dumps(template, indent=2))
        print(f"Створено шаблон кастомного каталогу: {custom_catalog}")

    editor = os.environ.get("EDITOR", "vim")
    subprocess.call([editor, str(custom_catalog)])
    return 0
```

---

## 5. Generator Integration

### 5.1 CRITICAL FIX: Pre-check MCP Tools BEFORE IntentQueue.submit()

**The Problem:** IntentQueue.ALLOWED_TOOLS rejects `mcp__*` tools at line 93-99.
The original plan had the generator checking `if intent_id is None` AFTER
submit, which works for rejection detection, but we can do better.

**The Fix (Option B - Pre-check):** Check if tool is MCP and disabled BEFORE
calling IntentQueue.submit(). This avoids submitting rejected intents and
allows cleaner suggestion logic.

```python
# In generator.py — inside GeneratorProcessor class

async def _submit_intent(
    self, payload: dict, transcript_id: int | None = None
) -> int | None:
    """Submit intent to queue, with MCP pre-check for suggestion."""
    tool = str(payload.get("tool", "?"))
    args = str(payload.get("args", ""))

    # CRITICAL FIX: Pre-check MCP tools BEFORE IntentQueue.submit()
    # IntentQueue rejects mcp__* tools (ALLOWED_TOOLS doesn't include them)
    # We catch disabled MCP tools here and suggest enable, then return early.
    if tool.startswith("mcp__"):
        # Extract server name: mcp__notion__search -> notion
        parts = tool.split("__")
        if len(parts) >= 2:
            server_name = parts[1]
            enabled_servers = set(self.settings.enable_mcp_servers or [])
            if server_name not in enabled_servers:
                # MCP server not enabled — suggest enable and DON'T submit
                await self._push_tts(
                    f"Для цього потрібен MCP сервер '{server_name}'. "
                    f"Скажіть: 'увімкни {server_name}'"
                )
                return None  # Don't submit to IntentQueue

    # Log decision for action tracking
    decision_id: int | None = None
    if self.store is not None:
        try:
            decision_id = await self.store.log_decision(
                transcript_id,
                {
                    "type": "act",
                    "intent": tool,
                    "action": {"tool": tool, "args": args},
                },
            )
        except Exception:
            logger.exception("generator: log_decision(act) failed (non-fatal)")

    # Submit to IntentQueue (will reject non-MCP unknown tools)
    intent_id = await self.intent_queue.submit(
        payload,
        decision_id=decision_id,
        transcript_id=transcript_id,
    )

    if intent_id is not None and self.conversation_manager is not None:
        self.conversation_manager.record_action_pending(
            intent_id, tool, args
        )

    if intent_id is not None:
        logger.info("[INTENT SUBMITTED id=%d tool=%s]", intent_id, tool)

    return intent_id
```

**Why this works:**
1. MCP tools are identified by `mcp__` prefix
2. Server name is extracted from the tool name
3. We check if the server is in `settings.enable_mcp_servers`
4. If NOT enabled, we suggest via TTS and return `None` (no submit)
5. This happens BEFORE IntentQueue.rejects, so we have clean control flow
6. Non-MCP tools still go through IntentQueue's ALLOWED_TOOLS check

**Fallback for unknown non-MCP tools:**
If IntentQueue.submit() still returns None (e.g., unknown non-MCP tool),
the existing behavior is unchanged — intent is dropped with a warning log.

### 5.2 Intent Detection (Future Enhancement)

Could add `mcp_enable` intent for voice-based enable/disable:
```
User: "увімкни notion"
  → Intent: mcp_enable
  → Action: enable_notion()
  → Response: "Notion увімкнено"
```

This is optional Phase 2 polish.

---

## 6. One-Time Setup

### 6.1 Setup Command

```bash
$ heare mcp setup
✅ Каталог встановлено: ~/.heare/mcp_catalog.json

Використання:
  heare mcp list        - список всіх MCP
  heare mcp enable <x>  - увімкнути MCP
  heare mcp disable <x> - вимкнути MCP

$ heare mcp setup --install-packages
✅ Каталог встановлено
Встановлення npm пакетів...
  npm install -g @modelcontextprotocol/server-notion
  npm install -g @modelcontextprotocol/server-github
  ...
```

### 6.2 Bundled Catalog

The `data/mcp_catalog.json` is bundled with heare. Initial list of 50+ servers:

**Official @modelcontextprotocol servers:**
- notion, github, filesystem, brave-search, slack, postgres
- sqlite, puppeteer, fetch, sequential-thinking, memory
- google-drive, google-maps, eve, exa, gdrive

**Community servers:**
- k8s, aws, azure, firebase, linear, jira, tapd
- openapi, gitlab, bitbucket, discord, telegram
- obsidian, notion-db, spreadsheet, calendar
- youtube, twitter, reddit, hacker-news, wikipedia

---

## 7. Testing Strategy

### 7.1 Unit Tests

**test_mcp_catalog.py**:
- `test_catalog_load_bundled()` - basic loading
- `test_catalog_merge_custom()` - custom catalog overrides bundled
- `test_catalog_get_entry()` - lookup by name
- `test_catalog_search()` - search by query
- `test_catalog_list_all()` - get all entries
- `test_catalog_custom_missing()` - works without custom catalog

**test_mcp_config.py** (simple functions):
- `test_enable_adds_to_config()` - config.toml updated
- `test_enable_adds_to_mcp_json()` - .mcp.json updated
- `test_enable_idempotent()` - already enabled returns True
- `test_enable_unknown_server()` - error handling
- `test_disable_removes_from_config()` - config.toml updated
- `test_disable_keeps_mcp_json()` - .mcp.json preserved (orphan behavior)
- `test_list_with_status()` - correct enable flags
- `test_get_enabled_servers()` - returns set

### 7.2 Integration Tests

**test_integration_mcp_enable_flow.py**:
```python
async def test_enable_and_reconnect():
    """Enable server, verify config, reconnect SDK."""
    # 1. Enable server
    # 2. Verify .mcp.json has entry
    # 3. Verify config.toml has enable_mcp_servers
    # 4. Trigger SDK reconnect
    # 5. Verify tool is now allowed
```

**test_integration_generator_mcp_suggestion.py**:
```python
async def test_generator_suggests_mcp_enable():
    """Generator suggests enable when MCP tool is disabled."""
    # 1. Mock IntentQueue
    # 2. Submit MCP intent for disabled server
    # 3. Verify TTS suggestion pushed
    # 4. Verify intent NOT submitted to queue
```

### 7.3 Mock Strategy

- Temp files for `.mcp.json`, `config.toml`, `custom_mcp_catalog.json`
- Mock `AgentSDKCLI._reconnect()` for reconnect tests
- Mock `IntentQueue.submit()` for generator suggestion tests

---

## 8. Rollout Plan

### Single Phase (Can Ship Immediately)

**Week 1**: Complete implementation

**Tasks**:
1. Create `data/mcp_catalog.json` with 50+ entries
2. Implement `MCPCatalog` class with custom catalog merge
3. Implement simple config functions (no class)
4. Add CLI commands to `main.py`
5. Add generator MCP pre-check + TTS suggestion
6. Unit + integration tests
7. Documentation

**Deliverable**:
```bash
$ heare mcp list
 ✓ notion [офіційний] [потрібен ключ]
     Сторінки та бази даних Notion
   github [офіційний] [потрібен ключ]
     GitHub issues, PRs, репозиторії
 ...

$ heare mcp enable notion
✅ notion увімкнено
Перезавантаження SDK...

$ heare mcp status
Увімкнені MCP сервери:
  notion: Сторінки та бази даних Notion

$ heare mcp edit-catalog
# Opens ~/.heare/custom_mcp_catalog.json in $EDITOR
```

**Verification**:
- Catalog loads correctly (bundled + custom merged)
- Enable/disable updates config
- SDK reconnect works
- Generator pre-checks MCP tools and suggests enable
- Orphaned .mcp.json entries preserved on disable
- All tests pass

---

## 9. Configuration Examples

### 9.1 User Config (config.toml)

```toml
# Before enable
enable_mcp_servers = []

# After "heare mcp enable notion"
enable_mcp_servers = ["notion"]

# After "heare mcp enable github"
enable_mcp_servers = ["notion", "github"]
```

### 9.2 Generated .mcp.json Entry

```json
{
  "mcpServers": {
    "notion": {
      "type": "stdio",
      "command": "npx",
      "args": ["@modelcontextprotocol/server-notion"],
      "env": {
        "NOTION_API_KEY": "prompt-user"
      }
    },
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "prompt-user"
      }
    }
  }
}
```

---

## 10. Error Handling

### 10.1 Catalog Missing

```python
# MCPCatalog.__init__ when catalog.json doesn't exist
→ Raise FileNotFoundError
→ CLI: "Каталог MCP не знайдено. Запустіть: heare mcp setup"
```

### 10.2 Unknown Server Name

```python
# manager.enable("unknown-server")
→ MCPCatalog.get() returns None
→ MCPEnableResult(success=False, error="Unknown server: unknown-server")
→ CLI: "Помилка: Unknown server: unknown-server"
```

### 10.3 Config Write Failures

```python
# .mcp_json write fails
→ Log error
→ Return MCPEnableResult(success=False, error="...")
→ Don't modify config.toml

# config.toml write fails
→ Rollback: remove from .mcp_json
→ Return error
```

---

## 11. Open Questions

1. **Catalog updates**: How to update catalog when new MCPs are released?
   - **Answer**: Bundle with heare releases. User runs `heare mcp setup` after upgrade.

2. **Auth prompts**: Should we prompt for API keys during enable?
   - **Answer**: No, leave as "prompt-user" in .mcp_json. Let user edit manually or add Phase 2 `heare mcp auth` command.

3. **SDK reload**: Auto-reconnect or manual restart?
   - **Answer**: Phase 1 requires manual restart via `heare stop && heare start`. Future: Could add auto-reconnect via existing `AgentSDKCLI.reconnect()` method.

4. **Orphaned .mcp.json entries**: What happens when a server is disabled?
   - **Answer**: (Architect feedback #5) Disabled servers STAY in .mcp.json but are
     blocked by the `enable_mcp_servers` allowlist. This is intentional — it
     preserves user configs (API keys, custom paths) for easy re-enable. The
     SDK will load the server but the agent backend won't be allowed to call
     its tools because of the allowlist check.

---

## 12. Success Criteria

- [ ] Static catalog bundled with heare (50+ entries)
- [ ] Custom catalog support (merges with bundled)
- [ ] `heare mcp setup` confirms catalog exists
- [ ] `heare mcp enable <name>` adds to config
- [ ] `heare mcp disable <name>` removes from config (keeps .mcp.json entry)
- [ ] `heare mcp list` shows all with status + auth visibility
- [ ] `heare mcp status` shows enabled only
- [ ] `heare mcp edit-catalog` opens custom catalog in editor
- [ ] SDK reconnects after enable/disable
- [ ] Generator pre-checks MCP tools and suggests "увімкни X"
- [ ] Orphaned .mcp.json behavior documented
- [ ] All tests pass
- [ ] Documentation updated

---

## 13. Dependencies

**New Python Dependencies**:
- None! (No npm API calls, no file watching)

**System Dependencies**:
- `npm` - already required for MCP servers
- `node` - already required for MCP servers

**APIs**:
- None! (No npm registry, no marketplace)

---

## 14. Comparison: Old vs New Approach

| Aspect | Old (Dynamic Search) | New (Static Catalog) |
|--------|---------------------|---------------------|
| npm search | Yes (npm registry API) | No (pre-curated list) |
| npm install | Dynamic per enable | One-time setup or manual |
| File watch | Yes (watchfiles) | No |
| Complexity | High (search, install, watch) | Low (config mgmt only) |
| Dev time | 2-3 weeks | 3-5 days |
| User experience | Search → Install → Enable | List → Enable (simpler) |
| Network dep | Required | Not required |
| Lines of code | ~800 | ~300 |

**Note on "Security":**
The original plan claimed "Curated list only" as a security benefit vs
"Typosquatting, supply chain" risks. This is DISHONEST — users run the
same npm packages either way. This is a UX convenience feature, not a
security feature. Users can still run malicious code if they install
a malicious MCP catalog or manually add a bad server to .mcp.json.

---

## 15. Rejected: Dynamic Search Approach

**Why we changed:**
- User feedback: simpler is better
- Marketplace search is overkill
- npm install at runtime is risky
- File-watch adds complexity
- Catalog approach is sufficient for 99% of use cases

**What we removed:**
- `MCPSearch` class (npm/GitHub API calls)
- `MCPInstaller` class (npm install logic)
- `MCPConfigWatcher` class (file watching)
- `heare mcp search` command
- `heare mcp install` command
- `heare mcp uninstall` command

**What we kept:**
- Config enable/disable concept
- SDK reconnect mechanism
- Generator suggestions

---

## 16. Future Enhancements (Out of Scope)

**Phase 2 (optional polish)**:
- `heare mcp auth <server> <key>` - store API keys securely
- `heare mcp info <server>` - show detailed server info (tools, author, etc.)
- `mcp_enable` intent - voice enable/disable commands
- `heare mcp update` - fetch latest bundled catalog from heare releases
- Validation: check custom_mcp_catalog.json for schema errors on load

**Phase 3 (if needed)**:
- Per-project MCP configs (.heare/mcp.toml in project directory)
- Catalog sharing (import/export custom catalogs)
- MCP server health checks (verify server starts before enable)

---

## 17. References

- MCP spec: https://modelcontextprotocol.io/
- Official MCP servers: https://github.com/modelcontextprotocol
- heare PRD Phase B-mcp: `.omc/prd.json`
- Previous plan: `.omc/plan-dynamic-mcp-search.md` (replaced by this)

---

**Next Steps**: Implement MCPCatalog (with custom merge), simple config functions (no class), create bundled catalog with 50+ entries, add CLI commands, and implement generator MCP pre-check. Can ship in days, not weeks.
