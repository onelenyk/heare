# src/skills — skills the assistant can run

Agent Skills (agentskills.io format): markdown procedures in
`~/.heare/skills/`, listed with `list_skills` and run with `run_skill`.
`mcp_utils.py` reads `~/.heare/mcp/.mcp.json` for the MCP bridge.

The marketplace, installer and capability index were removed on
12 September 2026: switched off from birth, never called. Skills and
MCP servers are added by hand — a markdown file, a line in `.mcp.json`.
