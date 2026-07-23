# src/skills/

Agent Skills (agentskills.io format) marketplace, installation, MCP server utilities, and capability discovery.

## STRUCTURE

```
skills/
├── marketplace.py      # Skill/MCP registry fetchers (GitHub, hostname validation)
├── installer.py        # Security-gated installer (tarball, checksum, consent)
├── discovery.py        # Capability discovery (local + remote + MCP)
├── agent_skills.py     # SkillsLoader — SKILL.md parser with YAML frontmatter
└── mcp_utils.py        # ~/.heare/workspace/.mcp.json reader
```

## CAPABILITY INDEX

`CapabilityIndex` in `agent/tools/capability_index.py` provides a unified read-only view across: built-in tools + dynamic tools + installed skills + MCP servers. Built at startup, used for discovery and tool-deny gating.

## CONVENTIONS

- Best-effort everywhere — marketplace fetches never block startup
- Cached 24h with HMAC integrity checks
- Tarfile extraction has path traversal protection

## GOTCHAS

- Skill installation requires `user_confirmed=True` + consent passphrase
- Default hostname allowlist in `marketplace.py` — `DEFAULT_HOSTNAME_ALLOWLIST`
- All MCP errors are swallowed: `must never block daemon startup`
- Skills are optional — pipeline runs without them
- `Marketplace.py:591-line` `fetch_skill_candidates()` is the largest function — network I/O + security validation in one place
