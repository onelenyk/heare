"""Workspace-scoped capability enumeration for ``ask_cli_agent``.

The heare daemon runs ``claude -p`` from ``settings.workspace_dir``. To
keep advertised capabilities in sync with what claude actually has access
to, we enumerate from the workspace itself:

* **MCP servers** come from ``<workspace>/.mcp.json`` (already seeded by
  ``workspace.ensure_workspace_mcp``).
* **Skills** come from ``<workspace>/.claude/skills/<name>/SKILL.md`` —
  drop in or symlink only the skills you want voice-accessible.

Cache lives at ``settings.capabilities_file`` and invalidates on age
(default 24h) or whenever the workspace ``.mcp.json`` or skills tree
mtime is newer than the cache.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


logger = logging.getLogger("heare.claude_capabilities")

WORKSPACE_MCP_FILENAME = ".mcp.json"
WORKSPACE_SKILLS_SUBPATH = (".claude", "skills")

CAPABILITIES_PROMPT = (
    "List your capabilities as JSON. Return ONLY a JSON object (no prose, "
    "no markdown fences) with this shape: "
    '{"skills": [{"name": "...", "description": "one line"}], '
    '"mcp_servers": [{"name": "...", "tools": ["..."]}], '
    '"core_tools": ["Read", "Edit", "Bash", ...]}. '
    "Include every skill you have access to via the Skill tool, every MCP "
    "server connected to you with its tool list, and every built-in/deferred "
    "tool name. Do not invent items."
)

DEFAULT_TIMEOUT_SEC = 120.0
DEFAULT_MAX_AGE_HOURS = 24.0


@dataclass
class Skill:
    name: str
    description: str = ""


@dataclass
class MCPServer:
    name: str
    tools: list[str] = field(default_factory=list)


@dataclass
class Capabilities:
    """Snapshot of the workspace's skill/MCP scope."""

    skills: list[Skill] = field(default_factory=list)
    mcp_servers: list[MCPServer] = field(default_factory=list)
    core_tools: list[str] = field(default_factory=list)
    fetched_at: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not (self.skills or self.mcp_servers or self.core_tools)

    def age_seconds(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) - self.fetched_at


def _workspace_mcp_path(workspace_dir: Path) -> Path:
    return workspace_dir / WORKSPACE_MCP_FILENAME


def _workspace_skills_dir(workspace_dir: Path) -> Path:
    return workspace_dir.joinpath(*WORKSPACE_SKILLS_SUBPATH)


# ---------------------------------------------------------------------------
# JSON parsing for the LLM mode (kept as opt-in fallback)
# ---------------------------------------------------------------------------


def _parse_capabilities_json(raw: str) -> Capabilities:
    raw = raw.strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        if first_nl >= 0:
            raw = raw[first_nl + 1 :]
        if raw.endswith("```"):
            raw = raw[: -3]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"capabilities response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"capabilities root must be an object, got {type(data).__name__}")

    skills: list[Skill] = []
    seen_skills: set[str] = set()
    for item in data.get("skills") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen_skills:
            continue
        seen_skills.add(name)
        skills.append(Skill(name=name, description=str(item.get("description") or "").strip()))

    mcps: list[MCPServer] = []
    seen_mcp: set[str] = set()
    for item in data.get("mcp_servers") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen_mcp:
            continue
        seen_mcp.add(name)
        deduped: list[str] = []
        seen_tool: set[str] = set()
        for t in (item.get("tools") or []):
            tname = str(t).strip()
            if tname and tname not in seen_tool:
                seen_tool.add(tname)
                deduped.append(tname)
        mcps.append(MCPServer(name=name, tools=deduped))

    core: list[str] = []
    seen_core: set[str] = set()
    for item in data.get("core_tools") or []:
        name = str(item).strip()
        if name and name not in seen_core:
            seen_core.add(name)
            core.append(name)

    return Capabilities(skills=skills, mcp_servers=mcps, core_tools=core)


async def query_capabilities(
    *,
    claude_cli: str = "claude",
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> Capabilities:
    """Opt-in: ask ``claude -p`` for a JSON snapshot of its capabilities.

    Slow (~60s) and costs money. Default ``mode="local"`` skips this.
    """
    argv = [claude_cli, "-p", CAPABILITIES_PROMPT, "--output-format", "json"]
    logger.info("[capabilities] querying claude (timeout=%.0fs)", timeout)
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"claude capabilities query timed out after {timeout}s")
    except FileNotFoundError as exc:
        raise RuntimeError(f"claude CLI not found: {exc}") from exc

    if proc.returncode != 0:
        err = stderr_bytes.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"claude exited {proc.returncode}: {err[:500]}")

    raw = stdout_bytes.decode("utf-8", errors="replace")
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"claude transport output was not JSON: {exc}") from exc

    inner = envelope.get("result") if isinstance(envelope, dict) else None
    if not isinstance(inner, str) or not inner.strip():
        raise ValueError("claude transport JSON had no .result string")

    caps = _parse_capabilities_json(inner)
    caps.fetched_at = time.time()
    logger.info(
        "[capabilities] LLM fetch took %.1fs: %d skills, %d MCPs, %d core tools",
        time.monotonic() - started,
        len(caps.skills),
        len(caps.mcp_servers),
        len(caps.core_tools),
    )
    return caps


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save(path: Path, caps: Capabilities) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(caps)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_cached(path: Path) -> Capabilities | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[capabilities] cache unreadable at %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    skills = [
        Skill(name=str(s.get("name") or ""), description=str(s.get("description") or ""))
        for s in data.get("skills") or []
        if isinstance(s, dict) and s.get("name")
    ]
    mcps = [
        MCPServer(
            name=str(m.get("name") or ""),
            tools=[str(t) for t in (m.get("tools") or []) if t],
        )
        for m in data.get("mcp_servers") or []
        if isinstance(m, dict) and m.get("name")
    ]
    core = [str(t) for t in (data.get("core_tools") or []) if t]
    fetched_at = float(data.get("fetched_at") or 0.0)
    return Capabilities(skills=skills, mcp_servers=mcps, core_tools=core, fetched_at=fetched_at)


# ---------------------------------------------------------------------------
# Workspace enumeration
# ---------------------------------------------------------------------------


def _parse_skill_frontmatter(skill_md: Path) -> tuple[str, str] | None:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    name = ""
    description = ""
    for line in text[3:end].splitlines():
        m = re.match(r"^(\w[\w-]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip().strip('"\'')
        if key == "name":
            name = value
        elif key == "description":
            description = value
    if not name:
        name = skill_md.parent.name
    return (name, description) if name else None


def _enumerate_workspace_skills(workspace_dir: Path) -> list[Skill]:
    """Read every ``<workspace>/.claude/skills/<name>/SKILL.md`` (incl. symlinks)."""
    skills_dir = _workspace_skills_dir(workspace_dir)
    if not skills_dir.is_dir():
        return []
    skills: list[Skill] = []
    seen: set[str] = set()
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        sm = child / "SKILL.md"
        if not sm.exists():
            continue
        parsed = _parse_skill_frontmatter(sm)
        if parsed is None:
            continue
        name, description = parsed
        if name in seen:
            continue
        seen.add(name)
        skills.append(Skill(name=name, description=description))
    return skills


def _read_workspace_mcp_servers(workspace_dir: Path) -> list[MCPServer]:
    """Read MCP server names from ``<workspace>/.mcp.json``."""
    mcp_path = _workspace_mcp_path(workspace_dir)
    if not mcp_path.exists():
        return []
    try:
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[capabilities] %s unreadable: %s", mcp_path, exc)
        return []
    raw = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return []
    servers: list[MCPServer] = []
    for name in sorted(raw):
        if not name:
            continue
        servers.append(MCPServer(name=name, tools=[]))
    return servers


async def enumerate_capabilities_local(*, workspace_dir: Path) -> Capabilities:
    """Enumerate workspace-scoped skills and MCP servers."""
    started = time.monotonic()
    skills = _enumerate_workspace_skills(workspace_dir)
    mcps = _read_workspace_mcp_servers(workspace_dir)
    caps = Capabilities(
        skills=skills,
        mcp_servers=mcps,
        core_tools=[],
        fetched_at=time.time(),
    )
    logger.info(
        "[capabilities] workspace enum took %.2fs: %d skills, %d MCPs (workspace=%s)",
        time.monotonic() - started,
        len(skills),
        len(mcps),
        workspace_dir,
    )
    return caps


def _workspace_changed_since(workspace_dir: Path, cached_at: float) -> bool:
    """Cache invalidation: workspace .mcp.json or skills tree newer than cache."""
    candidates: list[Path] = [_workspace_mcp_path(workspace_dir)]
    skills_dir = _workspace_skills_dir(workspace_dir)
    if skills_dir.is_dir():
        candidates.append(skills_dir)
        for child in skills_dir.iterdir():
            sm = child / "SKILL.md"
            if sm.exists():
                candidates.append(sm)
    for p in candidates:
        try:
            if p.stat().st_mtime > cached_at:
                return True
        except FileNotFoundError:
            continue
    return False


async def refresh_capabilities(
    path: Path,
    *,
    workspace_dir: Path,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    force: bool = False,
    claude_cli: str = "claude",
    timeout: float = DEFAULT_TIMEOUT_SEC,
    mode: str = "local",
) -> Capabilities | None:
    """Refresh the on-disk cache if it's stale, missing, or workspace changed.

    Modes:
      * ``"local"`` (default) — read ``workspace/.mcp.json`` and
        ``workspace/.claude/skills/``. Fast, free, deterministic.
      * ``"llm"`` — ask ``claude -p`` to enumerate. Slow, costs money.

    Returns the refreshed snapshot, the cached value on failure, or
    ``None`` when there's nothing to fall back to.
    """
    cached = load_cached(path)
    fresh_by_age = (
        cached is not None and cached.age_seconds() < max_age_hours * 3600
    )
    workspace_changed = (
        cached is not None and _workspace_changed_since(workspace_dir, cached.fetched_at)
    )

    if not force and fresh_by_age and not workspace_changed:
        logger.debug(
            "[capabilities] cache fresh (age=%.1fh, max=%.1fh) — skipping refresh",
            cached.age_seconds() / 3600,
            max_age_hours,
        )
        return cached
    if workspace_changed:
        logger.info("[capabilities] workspace files changed — refreshing")

    try:
        if mode == "llm":
            caps = await query_capabilities(claude_cli=claude_cli, timeout=timeout)
        elif mode == "local":
            caps = await enumerate_capabilities_local(workspace_dir=workspace_dir)
        else:
            raise ValueError(f"unknown capabilities mode: {mode!r}")
    except (RuntimeError, ValueError) as exc:
        logger.warning("[capabilities] refresh failed (%s) — keeping cache", exc)
        return cached

    try:
        save(path, caps)
    except OSError as exc:
        logger.warning("[capabilities] could not write cache to %s: %s", path, exc)

    return caps


def format_summary(caps: Capabilities | None, *, max_skills: int = 12, max_mcps: int = 6) -> str:
    """Human-readable summary for prompts/tool descriptions.

    No defaults — when the cache is missing or empty, returns a clear
    "no capabilities advertised" string so the operator notices.
    """
    if caps is None:
        return "(capabilities not yet enumerated)"
    if caps.is_empty:
        return "(no skills or MCPs configured in workspace)"

    parts: list[str] = []
    if caps.core_tools:
        parts.append(f"Core: {', '.join(caps.core_tools[:8])}.")
    if caps.mcp_servers:
        names = [m.name for m in caps.mcp_servers[:max_mcps]]
        parts.append(f"MCP: {', '.join(names)}.")
    if caps.skills:
        names = [s.name for s in caps.skills[:max_skills]]
        suffix = "" if len(caps.skills) <= max_skills else f" (+{len(caps.skills) - max_skills} more)"
        parts.append(f"Skills: {', '.join(names)}{suffix}.")
    return " ".join(parts)
