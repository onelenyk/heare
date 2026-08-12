---
name: voice-start
description: Start the heare voice daemon in the background from a checkout
---

For an agent working on heare from a terminal — not for heare itself,
which is the daemon this starts.

```bash
nohup uv run python -m src.main start > /dev/null 2>&1 &
echo $!
```

Run it from the repository root. Logs land in `~/.heare/logs/daemon.log`.

To bring up the macOS menu bar instead, which runs the pipeline inside
its own process rather than beside it:

```bash
uv run python -m src.main menubar
```

Confirm with `uv run python -m src.main status`, or read the log for the
startup lines that name the live configuration — the turn-end strategy,
the echo canceller, and the wake phrases.

Do not run this in the foreground under a command timeout. A timeout
kills the process group and takes the daemon with it, which reads
exactly like the assistant going deaf mid-sentence.
