# Learnings

## 2026-05-31: Removed optional-dependencies from pyproject.toml

- Removed all 5 extras (`local`, `memory`, `speaker`, `audio-event`, `overlay`) from `[project.optional-dependencies]` in pyproject.toml (lines 19-44)
- `uv lock` resolved 94 packages — previously 72 transitive packages were pruned from uv.lock (pyaudio, fastapi, uvicorn, pywebview, pyobjc-*, fastmcp, onnxruntime, numpy, huggingface-hub and all their transitive deps)
- Core deps (sounddevice, pipecat-ai, etc.) and dev deps (pytest, mypy, ruff) left untouched
- Pre-existing minor issue noted: line `"pyobjc-framework-cocoa>=10;"` in overlay had a double `>=` — removed as part of the section deletion
