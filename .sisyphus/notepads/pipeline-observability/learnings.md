# Learnings — pipeline-observability

## 2026-05-31: Created src/daemon/events.py

- Module is dependency-free — no pipecat, no async I/O. Works for sync use in `process_frame`.
- Three output channels: human-readable `daemon.log`, machine-parseable `events.jsonl`, in-memory ring buffer.
- Ring buffer uses `collections.deque(maxlen=200)` — O(1) append, auto-evicts oldest entries.
- `rate_limited()` uses module-level dict for last-emit timestamps; single-thread safety assumed.
- `setup()` takes `Path` object (not `str`); called once at daemon startup.
- STAGE_LABELS provides emoji-prefixed labels for readable log output.
- `emit()` is silent on filesystem errors — silently passes if `events.jsonl` unwritable.
