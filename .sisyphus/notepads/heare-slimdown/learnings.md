# Learnings — heare-slimdown QA

- Migration 007 uses table recreation (CREATE transcripts_new → INSERT → DROP → RENAME) because SQLite lacks ALTER TABLE DROP COLUMN for older versions. This is the standard SQLite pattern.
- `pyaudio` was only used in `test_recognizer.py` (deleted with speaker); actual runtime uses `sounddevice`.
- The `onnxruntime` match in `grep -r src/` is a stale comment in config.py line 243; no actual import exists.
- Storage test suite (test_storage.py) has 42 tests, all pass at v7 schema.
