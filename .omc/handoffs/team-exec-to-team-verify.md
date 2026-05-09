## Handoff: team-exec → team-verify
- **Decided**: All 11 tasks completed by 3 workers in parallel chains. Implementation matches plan REVISION 2. Token persistence via `[browser_bridge]` in `config.toml` + 0o600 convenience file. Bridge as background task in `_cmd_start` via `bridge_task` kwarg following `namer_task` pattern. Dashboard reads `~/.heare/browser_bridge.status` (5s staleness window) replacing CDP probe. 13 unit tests with mock WS client.
- **Rejected**: No deviations from plan.
- **Risks for verify**:
  1. Security-sensitive: bridge grants DOM access to every page in the user's signed-in browser. Verify token entropy (`secrets.token_urlsafe(32)` = 256-bit), localhost-only bind, Origin validation enforces `chrome-extension://`, single-connection 4002, audit logging actually fires.
  2. Concurrency: RPC correlation via `dict[str, asyncio.Future]` — verify out-of-order responses route correctly, that disconnected futures resolve with `retryable: True` rather than hanging.
  3. MV3 service worker suspension: keepalive alarm at 0.4 min + exponential backoff reconnect. Hard to unit-test; smoke-test step is manual.
  4. Settings round-trip: `write_browser_bridge_token` must preserve other config sections atomically (tmpfile + os.replace).
- **Files changed**:
  - **New**: `src/agent/browser_bridge.py`, `tests/test_browser_bridge.py`, `extensions/heare-bridge/{manifest.json,background.js,content_script.js,options.html,options.js,icons/*.png}`
  - **Edited**: `src/config.py`, `src/main.py`, `src/agent/tools/{direct.py,registry.py,schemas.py}`, `src/watch/{data.py,widgets.py,app.py}`
  - **Already present**: `pyproject.toml` (websockets>=12.0)
- **Tests run**: full suite 1140 pass / 2 skip after T2/T3/T4/T10. test_browser_bridge.py 13/13 pass in 1.19s. test_direct_tools.py 75/75 pass after T8.
- **Remaining**: verifier sign-off + security review against plan acceptance criteria. Manual smoke test (Step 6) is out-of-band — not a CI blocker.
