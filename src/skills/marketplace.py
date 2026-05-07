"""Marketplace + MCP registry fetchers with hostname/homoglyph/checksum primitives.

Used by ``src.skills.discovery`` to source remote skill / MCP candidates. All errors
are swallowed to ``[]`` — discovery is best-effort and must never raise into
the agent loop.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.parse

import httpx

from src.agent.tools.capability_index import IndexEntry

logger = logging.getLogger("heare.marketplace")

DEFAULT_HOSTNAME_ALLOWLIST: tuple[str, ...] = ("skillsmp.com", "github.com", "githubusercontent.com")

_BRANDS: tuple[str, ...] = ("skillsmp", "github", "githubusercontent")
_SUBSTITUTIONS: dict[str, str] = {"1": "i", "0": "o", "5": "s", "3": "e"}


def _is_allowed_hostname(url: str, allowlist: tuple[str, ...]) -> bool:
    try:
        host = urllib.parse.urlparse(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    host = host.rstrip(".").lower()
    for allowed in allowlist:
        a = allowed.lower()
        if host == a or host.endswith("." + a):
            return True
    return False


def _is_homoglyph_or_lookalike(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).hostname
    except ValueError:
        return True
    if not host:
        return True
    host = host.rstrip(".").lower()
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        return True

    labels = host.split(".")
    if len(labels) < 2:
        return False
    registrable = ".".join(labels[-2:])

    for brand in _BRANDS:
        if brand in labels[:-2] and brand not in registrable:
            return True

    for label in labels:
        substituted = "".join(_SUBSTITUTIONS.get(c, c) for c in label)
        if substituted == label:
            continue
        for brand in _BRANDS:
            if substituted == brand and label != brand:
                return True
    return False


def _verify_checksum(content: bytes, expected_sha256_hex: str) -> bool:
    actual = hashlib.sha256(content).hexdigest()
    return hmac.compare_digest(actual.lower(), expected_sha256_hex.lower())


def _validate_url(url: str, allowlist: tuple[str, ...]) -> bool:
    return _is_allowed_hostname(url, allowlist) and not _is_homoglyph_or_lookalike(url)


def _coerce_launch(raw: object) -> dict | None:
    """Validate and normalize an optional `launch` block from a registry entry.

    Shape: {"command": str (non-empty), "args": list[str], "env"?: dict[str,str]}.
    Returns the normalized dict, or None if absent. Raises ValueError on
    malformed input so the caller can drop the whole entry.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"launch must be a dict, got {type(raw).__name__}")
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("launch.command must be a non-empty string")
    args = raw.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("launch.args must be a list of strings")
    env = raw.get("env")
    if env is not None:
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise ValueError("launch.env must be a dict of str->str")
    out: dict = {"command": command, "args": list(args)}
    if env is not None:
        out["env"] = dict(env)
    return out


def _coerce_entry(raw: dict, source: str, allowlist: tuple[str, ...]) -> IndexEntry | None:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    description = raw.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    install_url = raw.get("install_url")
    if install_url is not None:
        if not isinstance(install_url, str) or not _validate_url(install_url, allowlist):
            logger.warning("rejecting %s entry %r: bad install_url %r", source, name, install_url)
            return None
    args_schema = raw.get("args_schema") if isinstance(raw.get("args_schema"), dict) else None
    network_required = bool(raw.get("network_required", False))
    pop = raw.get("popularity_score")
    popularity_score = float(pop) if isinstance(pop, (int, float)) else None
    checksum = raw.get("checksum") if isinstance(raw.get("checksum"), str) else None
    try:
        launch = _coerce_launch(raw.get("launch"))
    except ValueError as exc:
        logger.warning("rejecting %s entry %r: %s", source, name, exc)
        return None
    return IndexEntry(
        source=source,  # type: ignore[arg-type]
        name=name,
        description=description,
        args_schema=args_schema,
        network_required=network_required,
        popularity_score=popularity_score,
        install_url=install_url,
        checksum=checksum,
        launch=launch,
    )


def _parse_results(payload: object, source: str, allowlist: tuple[str, ...]) -> list[IndexEntry]:
    if not isinstance(payload, dict):
        logger.warning("%s payload not a dict: %r", source, type(payload).__name__)
        return []
    items = _extract_items(payload)
    if items is None:
        logger.warning("%s payload missing items list", source)
        return []
    out: list[IndexEntry] = []
    for raw in items:
        entry = _coerce_skillsmp_entry(raw, source, allowlist) if source == "skill" else _coerce_entry(raw, source, allowlist)
        if entry is not None:
            out.append(entry)
    return out


def _extract_items(payload: dict) -> list | None:
    """Find the items list in either legacy {"results":[...]} or skillsmp {"data":{"skills":[...]}} shape."""
    if isinstance(payload.get("results"), list):
        return payload["results"]
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("skills", "servers", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    return None


def _coerce_skillsmp_entry(raw: dict, source: str, allowlist: tuple[str, ...]) -> IndexEntry | None:
    """Map skillsmp.com /api/v1/skills/search response shape into IndexEntry."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    description = raw.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    github_url = raw.get("githubUrl")
    install_url = github_url if isinstance(github_url, str) and _validate_url(github_url, allowlist) else None
    if install_url is None and github_url is not None:
        logger.warning("rejecting skill %r: bad githubUrl %r", name, github_url)
        return None
    stars = raw.get("stars")
    popularity = float(stars) if isinstance(stars, (int, float)) else None
    return IndexEntry(
        source=source,  # type: ignore[arg-type]
        name=name,
        description=description,
        args_schema=None,
        network_required=True,
        popularity_score=popularity,
        install_url=install_url,
        checksum=None,
    )


async def _fetch_json(url: str, timeout: float) -> object | None:
    import time as _time
    started = _time.monotonic()
    logger.info("[MARKETPLACE] → GET %s (timeout=%.1fs)", url, timeout)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        latency_ms = int((_time.monotonic() - started) * 1000)
        body = resp.text
        preview = body[:500] + ("…" if len(body) > 500 else "")
        logger.info(
            "[MARKETPLACE] ← status=%d bytes=%d latency_ms=%d body=%s",
            resp.status_code, len(body), latency_ms, preview,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        logger.warning(
            "[MARKETPLACE] ✗ fetch %s failed after %dms: %s",
            url, latency_ms, exc,
        )
        return None


async def fetch_skill_candidates(
    query: str, *, settings, timeout: float = 2.5
) -> list[IndexEntry]:
    base = getattr(settings, "marketplace_url", "") or ""
    if not base:
        logger.info("[MARKETPLACE] skill search skipped: marketplace_url not configured")
        return []
    url = f"{base.rstrip('/')}/api/v1/skills/search?q={urllib.parse.quote(query)}"
    if not _validate_url(url, DEFAULT_HOSTNAME_ALLOWLIST):
        logger.warning("[MARKETPLACE] marketplace_url rejected: %r", base)
        return []
    payload = await _fetch_json(url, timeout)
    if payload is None:
        return []
    results = _parse_results(payload, "skill", DEFAULT_HOSTNAME_ALLOWLIST)
    logger.info("[MARKETPLACE] skill search query=%r → %d entries after validation", query, len(results))
    return results


# Built-in MCP catalog: a small curated set so `discover_capability` returns
# something useful even when ``mcp_registry_url`` is not configured. Entries
# must be installable out of the box (no required env vars, no required path
# args). Network-sourced entries take precedence on slug collision so a
# registry can override these.
_BUILTIN_MCP_CATALOG: tuple[IndexEntry, ...] = (
    # ------------------------------------------------------------------
    # Zero-config MCPs — install and run with no further setup.
    # ------------------------------------------------------------------
    IndexEntry(
        source="mcp",
        name="chrome-devtools",
        description=(
            "Browser automation via Chrome DevTools Protocol — navigate, click, "
            "evaluate JS, capture network logs, take screenshots. Headless and "
            "isolated profile by default."
        ),
        install_url="https://github.com/ChromeDevTools/chrome-devtools-mcp",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "chrome-devtools-mcp@latest", "--isolated", "--headless=true"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="fetch",
        description="Fetch content from a URL and convert to markdown for the agent.",
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={"command": "uvx", "args": ["mcp-server-fetch"]},
    ),
    IndexEntry(
        source="mcp",
        name="memory",
        description="Persistent key-value memory store for cross-session recall.",
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=False,
        launch={"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
    ),
    IndexEntry(
        source="mcp",
        name="time",
        description="Current time and timezone conversions — IANA zones, DST-aware.",
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=False,
        launch={"command": "uvx", "args": ["mcp-server-time"]},
    ),
    IndexEntry(
        source="mcp",
        name="sequential-thinking",
        description=(
            "Structured chain-of-thought scratchpad — lets the agent break "
            "complex problems into ordered steps with revisable thoughts."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=False,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="puppeteer",
        description=(
            "Browser automation via Puppeteer — navigate, screenshot, fill "
            "forms, run JS. Use when a Chromium-only feature is needed."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="playwright",
        description=(
            "Cross-browser automation via Playwright — Chromium, Firefox, "
            "WebKit. Snapshot-based interactions, accessibility tree access."
        ),
        install_url="https://github.com/microsoft/playwright-mcp",
        network_required=True,
        launch={"command": "npx", "args": ["-y", "@playwright/mcp@latest"]},
    ),
    IndexEntry(
        source="mcp",
        name="context7",
        description=(
            "Fetch up-to-date library/SDK documentation — version-specific "
            "code examples for any package, sourced live."
        ),
        install_url="https://github.com/upstash/context7",
        network_required=True,
        launch={"command": "npx", "args": ["-y", "@upstash/context7-mcp@latest"]},
    ),
    # ------------------------------------------------------------------
    # Path-required MCPs — discovery surfaces them, but the user must
    # supply a path arg via ``register_mcp_server`` after install.
    # ------------------------------------------------------------------
    IndexEntry(
        source="mcp",
        name="filesystem",
        description=(
            "Sandboxed filesystem access — list, read, write, search files "
            "under one or more allowed roots. Pass each root as an arg."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=False,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="git",
        description=(
            "Read git repository state — log, diff, blame, branch, status. "
            "Pass the repository path with ``--repository <path>``."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=False,
        launch={"command": "uvx", "args": ["mcp-server-git"]},
    ),
    IndexEntry(
        source="mcp",
        name="sqlite",
        description=(
            "SQLite database access — query, schema introspection. "
            "Pass the database path with ``--db-path <path>``."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=False,
        launch={"command": "uvx", "args": ["mcp-server-sqlite"]},
    ),
    IndexEntry(
        source="mcp",
        name="postgres",
        description=(
            "PostgreSQL database access — query and schema. Pass the "
            "connection string as an arg, e.g. ``postgresql://user@host/db``."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-postgres"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="redis",
        description=(
            "Redis key-value store access — get, set, list keys, run "
            "commands. Pass the URL as an arg, e.g. ``redis://localhost``."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-redis"],
        },
    ),
    # ------------------------------------------------------------------
    # API-key-required MCPs — discovery surfaces them; install needs the
    # key supplied via ``register_mcp_server`` env.
    # ------------------------------------------------------------------
    IndexEntry(
        source="mcp",
        name="github",
        description=(
            "GitHub API — issues, pull requests, files, commits, search. "
            "Requires GITHUB_PERSONAL_ACCESS_TOKEN env var."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="gitlab",
        description=(
            "GitLab API — issues, merge requests, files, pipelines. "
            "Requires GITLAB_PERSONAL_ACCESS_TOKEN env var."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-gitlab"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="slack",
        description=(
            "Slack API — read channels, send messages, manage threads. "
            "Requires SLACK_BOT_TOKEN and SLACK_TEAM_ID env vars."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-slack"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="brave-search",
        description=(
            "Web search via Brave Search API — text and image. "
            "Requires BRAVE_API_KEY env var (free tier available)."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="google-maps",
        description=(
            "Google Maps — geocode, directions, place lookup, distance "
            "matrix. Requires GOOGLE_MAPS_API_KEY env var."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-google-maps"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="gdrive",
        description=(
            "Google Drive — list and read files, search by query. "
            "Requires Google OAuth credentials (one-time browser auth)."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-gdrive"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="sentry",
        description=(
            "Sentry — fetch error and performance issues by ID. "
            "Requires SENTRY_AUTH_TOKEN env var."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-sentry"],
        },
    ),
    IndexEntry(
        source="mcp",
        name="notion",
        description=(
            "Notion — read and update pages, databases, comments. "
            "Requires NOTION_API_KEY env var (Notion integration token)."
        ),
        install_url="https://github.com/makenotion/notion-mcp-server",
        network_required=True,
        launch={"command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"]},
    ),
    IndexEntry(
        source="mcp",
        name="linear",
        description=(
            "Linear — issues, projects, cycles. "
            "Requires LINEAR_API_KEY env var."
        ),
        install_url="https://github.com/jerhadf/linear-mcp-server",
        network_required=True,
        launch={"command": "npx", "args": ["-y", "mcp-linear"]},
    ),
    IndexEntry(
        source="mcp",
        name="everart",
        description=(
            "Image generation via EverArt — multiple models. "
            "Requires EVERART_API_KEY env var."
        ),
        install_url="https://github.com/modelcontextprotocol/servers",
        network_required=True,
        launch={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-everart"],
        },
    ),
)


# Keyword aliases per slug — natural-language queries that should surface
# the entry even when they don't substring-match name/description.
_BUILTIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "chrome-devtools": (
        "browser", "chrome", "devtools", "automate", "automation",
        "screenshot", "navigate", "click", "scrape", "web page", "headless",
        "web", "navigate", "click", "form", "javascript", "cdp",
    ),
    "fetch": ("fetch", "url", "http", "download", "request", "web content", "scrape", "markdown"),
    "memory": ("memory", "remember", "recall", "store", "persist", "key-value", "kv", "storage"),
    "time": ("time", "clock", "timezone", "date", "timestamp", "iana", "dst"),
    "sequential-thinking": (
        "thinking", "reasoning", "chain of thought", "scratchpad", "steps",
        "plan", "break down", "thought", "reason",
    ),
    "puppeteer": (
        "browser", "puppeteer", "chromium", "automation", "screenshot",
        "scrape", "headless", "chrome", "navigate", "click", "form",
    ),
    "playwright": (
        "browser", "playwright", "automation", "cross-browser", "firefox",
        "webkit", "screenshot", "scrape", "headless", "navigate", "click",
    ),
    "context7": ("docs", "documentation", "library", "sdk", "examples", "reference", "code"),
    "filesystem": (
        "file", "files", "filesystem", "directory", "folder", "read", "write",
        "search", "list", "path", "disk", "storage", "access",
    ),
    "git": (
        "git", "repository", "repo", "version control", "commit", "diff",
        "log", "blame", "branch", "status", "code", "history",
    ),
    "sqlite": (
        "database", "sqlite", "db", "sql", "query", "schema", "table",
        "sqlite3", "local database",
    ),
    "postgres": (
        "database", "postgres", "postgresql", "db", "sql", "query", "schema",
        "table", "rdbms", "relational",
    ),
    "redis": ("redis", "cache", "key-value", "kv", "store", "database", "nosql"),
    "github": (
        "github", "git", "issue", "pr", "pull request", "repository", "repo",
        "commit", "code", "search", "api", "integration",
    ),
    "gitlab": (
        "gitlab", "issue", "merge request", "pipeline", "repository", "repo",
        "commit", "code", "api", "ci/cd",
    ),
    "slack": (
        "slack", "chat", "message", "channel", "thread", "team", "communication",
        "workspace", "im",
    ),
    "brave-search": (
        "search", "web search", "brave", "query", "internet", "lookup",
        "find", "google alternative",
    ),
    "google-maps": (
        "maps", "google maps", "location", "geocode", "directions", "route",
        "distance", "places", "address", "navigation", "gps",
    ),
    "gdrive": (
        "google drive", "gdrive", "drive", "files", "google", "cloud storage",
        "documents", "sheets", "storage",
    ),
    "sentry": (
        "sentry", "error", "errors", "monitoring", "logging", "performance",
        "issues", "bugs", "crash", "tracking",
    ),
    "notion": (
        "notion", "notes", "database", "wiki", "docs", "pages", "knowledge",
        "documentation", "collaboration",
    ),
    "linear": (
        "linear", "issue", "project", "task", "bug", "cycle", "sprint",
        "planning", "management", "workflow",
    ),
    "everart": ("image", "generate", "ai", "art", "everart", "create", "visual"),
}


def _query_builtin_catalog(query: str) -> list[IndexEntry]:
    """Filter the built-in catalog by case-insensitive substring match against
    name, description, and per-slug keyword aliases.

    Special "list all" queries return the full catalog: "all", "available",
    "list", "everything", "mcp", "mcps".
    """
    q = query.lower().strip()
    if not q:
        return []
    # Special case: list all entries for broad "show me everything" queries
    list_all_triggers = ("all", "available", "list", "everything", "mcp", "mcps")
    if q in list_all_triggers:
        return list(_BUILTIN_MCP_CATALOG)
    out: list[IndexEntry] = []
    for entry in _BUILTIN_MCP_CATALOG:
        haystack_parts = [entry.name.lower(), entry.description.lower()]
        haystack_parts.extend(_BUILTIN_KEYWORDS.get(entry.name, ()))
        haystack = " ".join(haystack_parts)
        if q in haystack or any(tok in haystack for tok in q.split()):
            out.append(entry)
    return out


async def fetch_mcp_candidates(
    query: str, *, settings, timeout: float = 2.5
) -> list[IndexEntry]:
    """Return MCP candidates for ``query`` from the built-in catalog plus any
    configured remote registry. Network results take precedence on slug
    collision so a registry can override the bundled entry.
    """
    builtin = _query_builtin_catalog(query)

    base = getattr(settings, "mcp_registry_url", "") or ""
    network: list[IndexEntry] = []
    if base:
        url = f"{base.rstrip('/')}/api/search?q={urllib.parse.quote(query)}"
        if _validate_url(url, DEFAULT_HOSTNAME_ALLOWLIST):
            payload = await _fetch_json(url, timeout)
            if payload is not None:
                network = _parse_results(payload, "mcp", DEFAULT_HOSTNAME_ALLOWLIST)
                logger.info(
                    "[MARKETPLACE] mcp search query=%r → %d network entries after validation",
                    query, len(network),
                )
        else:
            logger.warning("[MARKETPLACE] mcp_registry_url rejected: %r", base)
    else:
        logger.info(
            "[MARKETPLACE] mcp_registry_url not configured — using built-in catalog only"
        )

    # Network results take precedence on slug collision so an external
    # registry can override the bundled entry. Built-in catalog acts as
    # a fallback for hosts without a registry configured.
    network_slugs = {e.name for e in network}
    return network + [e for e in builtin if e.name not in network_slugs]


__all__ = [
    "DEFAULT_HOSTNAME_ALLOWLIST",
    "fetch_skill_candidates",
    "fetch_mcp_candidates",
]
