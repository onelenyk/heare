---
description: Switch heare's mode between silent, focus, and ambient
---
Hot-reload heare's mode without restarting the daemon. Writes the new mode
to ~/.heare/mode; the DeciderProcessor rereads the file on its next frame.

Usage:
```bash
cd /Users/lenyk/myprojects/heare
uv run python -m src.main mode silent   # listen only, never speak/act
uv run python -m src.main mode focus    # speak only when addressed
uv run python -m src.main mode ambient  # heartbeats + casual speech
```
