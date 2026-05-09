## Handoff: team-verify → team-fix
- **Verifier**: APPROVED — 13/13 bridge tests pass, 1153/2 skipped full suite, every acceptance criterion verified with file:line.
- **Security-reviewer**: APPROVED WITH FOLLOW-UPS — 0 CRITICAL, 2 HIGH must-fix.
- **Code-reviewer**: CHANGES REQUIRED — 1 CRITICAL, 4 MAJOR.

### Required fixes (team-fix scope)

**CRITICAL** (lifecycle race in shutdown)
1. `src/agent/browser_bridge.py:115-132` + `src/main.py` `_cmd_start` finally — `bridge.stop()` races with `run_until_stopped`'s cancel of `bridge_task`. Fix:
   - Make `BrowserBridge.stop()` idempotent (early return if already stopped; null `self._server` after close).
   - In `_cmd_start` finally, do NOT both cancel bridge_task (via watch_set) and `await bridge.stop()` from the outside without coordination. Pick one path: cancel bridge_task and let its `finally:` clean up, OR call `bridge.stop()` and skip cancel. Recommendation: keep `bridge.stop()` as the cleanup path, drop reliance on cancel-first behavior, ensure `_handle_connection` finally only resolves pending if not already resolved (idempotent `_fail_pending`).

**HIGH** (security)
2. `src/agent/browser_bridge.py:157` — replace `msg.get("token") != self._token` with `not secrets.compare_digest(str(msg.get("token", "")), self._token)`. Add `import secrets` at top if not already.
3. `src/config.py:541` (`write_browser_bridge_token`) — add `os.chmod(tmp_path, 0o600)` BEFORE `os.replace(tmp_path, config_path)`. Apply same fix to `_cmd_rotate_browser_token` in `src/main.py:528-535` (or share a helper) — replace `Path.write_text` with `os.open(..., O_WRONLY|O_CREAT|O_TRUNC, 0o600)` pattern to avoid the write-then-chmod window.

**MAJOR**
4. `src/agent/browser_bridge.py:212-218` — pong fire-and-forget: hold strong refs in `self._pong_tasks: set[asyncio.Task]` with `task.add_done_callback(self._pong_tasks.discard)`. Wrap `ws.send` in try/except `ConnectionClosed`.
5. `src/agent/browser_bridge.py:243` — replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()`.
6. `tests/test_browser_bridge.py:431-473` (`test_protocol_version`) — inside the `respond()` coroutine, after `req = json.loads(...)`, add `assert req["v"] == WIRE_VERSION`. Remove the duplicate assert on line 465 (or keep both, but the inner one must exist).
7. `tests/test_browser_bridge.py:409-428` (`test_token_persistence`) — monkeypatch `src.agent.browser_bridge.write_browser_bridge_token` to a `MagicMock`, run `bridge.start()`, assert mock was NOT called, then perform an auth round-trip with `"persistent-token-xyz"` and verify `auth_result.ok=true`.
8. `extensions/heare-bridge/background.js:142, 166` — add `if (isBlocked(params.url)) return blockedError(params.url);` (or equivalent) at the top of `handleNavigate` and `handleOpenTab` so the URL blocklist applies to navigation methods too.

### Out of scope (skip in this fix loop)
- MINOR-1..6 from code-reviewer (style/dead-code/UX).
- LOW security findings (explicit `max_size` on `serve()`, rotate-token tmp-write race window — already mitigated by `os.chmod`).
- The `_authed` flag dedup — leave as-is since current impl is correct.

### After fixes
- Re-run `pytest tests/test_browser_bridge.py -v` (expect 13 still pass).
- Re-run `pytest tests/` (expect 1153/2 skipped, no regressions).
- Smoke check: `python -m src.main rotate-browser-token` writes 0o600 file.
- Verify `config.toml` is mode 0o600 after rotate.
