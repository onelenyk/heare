---
name: voice-stop
description: Stop the running heare voice daemon
---

If you are heare, use the `stop_daemon` tool, and only when the user
asked for it in as many words.

From a terminal:

```bash
uv run python -m src.main stop
```

Sends SIGTERM via the pid file at `~/.heare/heare.pid`, waits up to three
seconds, then escalates to SIGKILL. Safe when nothing is running.

Note that this stops a daemon started by `src.main start`. When heare
runs from the menu bar the pipeline lives inside that process, so there
is no separate daemon to signal — quit the menu bar app instead.
