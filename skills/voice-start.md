---
description: Start the heare voice assistant daemon in the background
---
Start the heare voice daemon detached from the terminal so it keeps running
after the shell closes. Logs go to ~/.heare/logs/daemon.log.

```bash
cd /Users/lenyk/myprojects/heare
nohup uv run python -m src.main start > /dev/null 2>&1 &
echo $!
```

Verify with `skills/voice-status.md` or `uv run python -m src.main status`.
