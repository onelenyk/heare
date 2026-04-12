---
description: Stop the heare voice assistant daemon
---
Sends SIGTERM to the running heare daemon via the pid file at
~/.heare/heare.pid. Waits up to 3 seconds and escalates to SIGKILL if
necessary. Safe to run when heare is not running (no-op).

```bash
cd /Users/lenyk/myprojects/heare
uv run python -m src.main stop
```
