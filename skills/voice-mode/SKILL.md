---
name: voice-mode
description: Switch heare's mode — ambient, focus, silent, assistant or meeting
---

If you are heare, use the `set_mode` tool. It changes the live session
immediately and needs no shell.

From a terminal, against a checkout:

```bash
uv run python -m src.main mode silent
```

The mode lives in the state database. An earlier version of this note
said it was written to `~/.heare/mode` and reread by a DeciderProcessor;
neither is true any more — the processor was deleted and `mode_file` is
declared in the settings but read by nothing.

What the modes actually change:

- **ambient** — ordinary conversation, medium proactivity. The default.
- **focus** — terse and fast. No chit-chat, no follow-up questions.
- **silent** — speech is muted; replies are written, not heard.
- **assistant** — proactive; offers follow-ups and does multi-step work.
- **meeting** — passive note-taker. Speech muted, and the only profile
  that actually blocks tools: bash, write, and daemon control.

Worth knowing before you promise a behaviour: apart from `meeting`, the
profiles differ in tone, output channels and proactivity — not in what
the agent is permitted to do.
