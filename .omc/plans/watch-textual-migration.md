# Watch Dashboard: Rich+Live to Textual Migration

**Date:** 2026-05-02 (revised 2026-05-03)
**Status:** REVISED (iteration 1) -- awaiting Architect + Critic re-review
**Scope:** `src/watch.py` (628 LOC) rewrite to `src/watch/` package; `src/watch_controls.py` unchanged; new test files
**Estimated complexity:** MEDIUM-HIGH

---

## Iteration 1 Changes

Summary of revisions addressing Architect and Critic feedback from the initial draft:

1. **`_dispatch_key` / `_handle_input_key` elimination (Critic #1):** Both functions are deleted. Their logic is split between `action_*` methods on `HeareDashboard` in `app.py` (for hotkey dispatch) and `Input` widget event handlers in `ControlsBar` (for text-input mode). Steps 2 and 7 updated.
2. **`tests/test_watch_controls.py` migration guidance (Critic #2):** Added to Appendix and Step 10 with explicit per-test disposition. The 8 `_dispatch_key`/`_handle_input_key` tests are rewritten to use `Pilot.press()`.
3. **UNION ALL query fix (Critic #3):** Added `status` from `actions` and `NULL AS status` from `transcripts`. Section 3 now explains how `status` drives the colored badge in the TYPE column.
4. **Tools panel decision (Critic #4):** Explicitly deferred. New subsection 4b documents the decision. Existing `_tools_table` tests deleted (not ported). Follow-up ticket placeholder added in Section 11.
5. **Per-test migration matrix (Critic #5):** Step 10 now enumerates every test in `test_watch.py` and `test_watch_controls.py` with disposition: port-as-is, rewrite, or delete.
6. **`once` mode committed to option (b) (Critic #6):** Bypasses the Textual App entirely. `data.py` produces a `DashboardSnapshot`; `__init__.py` renders it via `rich.Console` to stdout and exits. New `test_once_mode_outputs_to_stdout` test added.
7. **`idx_actions_ts` index (Critic #7):** Included in Step 2 as a one-line schema patch to `src/storage.py`.
8. **`dashboard.tcss` added (Critic #8):** Added to Appendix and Step 9 with minimal required content spec.
9. **`Binding("M", ...)` syntax resolved (Critic #9):** Textual uses uppercase single character `Binding("M", ...)` for Shift+letter. Plan uses this form consistently throughout.
10. **`_compat.py` eliminated (Architect nit):** The 10-15 line sync wrapper is folded into `__init__.py`. `_compat.py` removed from file list, Section 2, and Step 1.
11. **`DashboardSnapshot` single-call pattern (Architect nit):** `data.py` exposes `fetch_dashboard_state(settings) -> DashboardSnapshot` returning a frozen dataclass. Widgets receive a snapshot, not individual queries.
12. **Legacy import smoke test (Architect nit):** New test `test_legacy_env_var_returns_callable` added to Step 10.
13. **Open questions resolved:** OQ-1 (fold Did) already in plan. OQ-2 (mouse drag) deferred -- keyboard only with 3 presets. OQ-4 (once mode) committed to option (b). OQ-5 (Tools panel) deferred with follow-up.

---

## 1. RALPLAN-DR Summary

### Principles

1. **Scrollback is mandatory.** The primary driver of this migration is that `rich.live` cannot scroll. Every data panel must support native scroll.
2. **Preserve muscle memory.** Every existing hotkey must work identically in the new TUI. Users should not need to re-learn the dashboard.
3. **Read-only DB contract is sacred.** The dashboard never writes to the daemon's SQLite. This invariant is non-negotiable.
4. **Minimal blast radius.** `watch_controls.py`, `config.py`, CLI entrypoint signature, and all other `src/*.py` files are untouched.
5. **Testability first.** The new code must be testable via Textual's `App.run_test()` pilot from day one -- no manual-only verification.

### Decision Drivers (top 3)

| # | Driver | Weight |
|---|--------|--------|
| 1 | Real scrolling inside every data panel (Activity, daemon.log) | Critical |
| 2 | Keyboard-resizable column widths | High |
| 3 | Development velocity: prefer built-in Textual widgets over custom rendering | High |

### Viable Options

#### Option A: Custom widgets from scratch

Build `ActivityWidget`, `LogWidget`, `HeaderWidget` as subclasses of `Widget`, rendering with `Rich.Console` internally via `render()` override.

| Pros | Cons |
|------|------|
| Full control over rendering and scroll behavior | High effort: must re-implement scroll, focus, resize from zero |
| No dependency on Textual widget API stability | ~3x more code than Option B |
| Can match current visual output pixel-for-pixel | Testing requires more fixture setup |

#### Option B: Compose built-in widgets (DataTable + RichLog + Static)

Use `DataTable` for Activity, `RichLog` for daemon.log, `Static` for header/footer. Layout via `Horizontal`/`Vertical` containers with CSS grid sizing.

| Pros | Cons |
|------|------|
| Scrolling, focus, keyboard nav come free | `DataTable` row styling is less flexible than hand-built `Table` |
| ~40% less code to write and maintain | Tied to Textual's widget API surface (churn risk) |
| `App.run_test()` pilot works natively with these widgets | Column resize needs custom CSS class toggling or key handler |
| Built-in accessibility (screen reader labels, focus ring) | Minor visual differences from current Rich output |

#### Option C: Hybrid -- Rich Console rendering inside Textual Static.update()

Keep `_build_header()`, `_activity_table()`, etc. as-is, render them with `Console(record=True)`, and push the ANSI string into `Static.update()` or `RichLog.write()` wrappers.

| Pros | Cons |
|------|------|
| Lowest migration effort -- reuse most existing render functions | **No real scrolling** inside a `Static` -- defeats the primary goal |
| Zero visual regression risk | Cannot focus or keyboard-navigate inside panels |
| Quick path to "running in Textual" | Testing is harder -- still testing Rich output strings, not widget state |

**Verdict:** Option C is **invalidated** because it does not deliver Goal 1 (real scrolling). It collapses to the current `rich.live` limitation wrapped in a Textual shell. Keeping it would make the migration pointless.

**Recommendation: Option B.** Built-in widgets deliver scrolling, focus management, and resize with the least code. The `DataTable` formatting constraint is minor -- column styles are configurable and `RichLog` accepts full `Rich.Text` objects for severity coloring. Option A is viable but the extra effort is not justified given the scope.

### ADR: Architecture Decision Record

- **Decision:** Option B -- Compose built-in Textual widgets (DataTable + RichLog + Static).
- **Drivers:** Scrollability (critical), dev velocity (high), testability via Pilot (high).
- **Alternatives considered:** Option A (custom widgets -- viable but 3x effort), Option C (hybrid Static.update -- invalidated, no scrolling).
- **Why chosen:** Option B delivers all 5 goals with ~40% less code than A. Built-in scroll, focus, and keyboard nav eliminate the need for custom reimplementation. Textual's Pilot testing API works natively with DataTable/RichLog/Input widgets.
- **Consequences:** Tied to Textual 1.x widget API surface. Minor visual differences from current Rich output (DataTable vs hand-built Table). Must pin `textual>=1.0.0,<2.0.0`.
- **Follow-ups:** Monitor Textual 2.x announcements for breaking changes. Evaluate mouse-drag column resize as a post-migration enhancement.

---

## 2. Target Architecture

### Module layout

Convert `src/watch.py` from a single file to a package:

```
src/
  watch/
    __init__.py          # re-exports run_watch(); contains once-mode renderer;
                         #   legacy env-var routing (HEARE_WATCH_LEGACY=1)
    app.py               # HeareDashboard(App) -- main Textual app, bindings,
                         #   action_* methods (replaces _dispatch_key),
                         #   text-input mode (replaces _handle_input_key)
    widgets.py           # HeaderBar, ActivityTable, LogTail, ControlsBar
    data.py              # DB reader functions + DashboardSnapshot frozen dataclass:
                         #   open_db, fetch, counts, load_speaker_labels,
                         #   daemon_status, current_mode, current_provider,
                         #   fetch_dashboard_state() -> DashboardSnapshot,
                         #   read_log_tail
    dashboard.tcss       # Textual CSS: column ratios, focus highlight,
                         #   severity colors, status badge colors
    _legacy.py           # Copy of old watch.py for rollback
  watch_controls.py      # UNCHANGED
```

**Why a package?** The current 628 LOC will grow to ~800-900 with Textual CSS, widget classes, and the data layer. A single file past 800 LOC becomes hard to review. The package keeps each concern under ~250 LOC.

**`_compat.py` eliminated:** The original draft had a `_compat.py` for sync/async bridging. This is unnecessary because `App.run()` is already sync-blocking. The `run_watch()` function lives directly in `__init__.py` (~15 lines: env-var check, once-mode branch, `app.run()` call).

**Import stability:** `from src.watch import run_watch` continues to work via `watch/__init__.py` re-export. `src/main.py` is untouched.

### `_dispatch_key` and `_handle_input_key` elimination

Both functions from the current `watch.py` are **deleted** entirely. Their responsibilities are redistributed:

- **`_dispatch_key` logic** moves into `action_*` methods on `HeareDashboard(App)` in `app.py`. Each hotkey binding (s/x/r/m/M/p) maps directly to an `action_*` method. Textual's binding system replaces the manual key-matching `if/elif` chain. The provider-toggle logic (`p` key) also becomes `action_toggle_provider`.
- **`_handle_input_key` logic** is replaced by Textual's `Input` widget inside `ControlsBar`. The `Input` widget natively handles printable characters, backspace, cursor movement, and paste. `Input.on_submit` (Enter) calls `inject_text()`. An `on_key` handler on the `Input` catches Escape to cancel. No manual buffer management needed.

### DashboardSnapshot pattern

`data.py` exposes a single entry point for all widget data:

```python
@dataclass(frozen=True)
class DashboardSnapshot:
    header: HeaderData           # name, emoji, running, pid, uptime, mode, provider, counts
    activity_rows: list[ActivityRow]  # unified transcript+action feed
    log_lines: list[LogLine]     # daemon.log tail with severity
    is_muted: bool
    is_input_muted: bool

def fetch_dashboard_state(settings: Settings) -> DashboardSnapshot:
    """Single call to fetch all dashboard data. Returns a frozen snapshot."""
    ...
```

Widgets receive the snapshot via `_refresh_data()` on the App, not individual queries. This makes a future `run_worker(thread=True)` migration genuinely a 1-line change (wrap `fetch_dashboard_state` in `self.run_worker()`).

### Main App class skeleton

```python
class HeareDashboard(App):
    CSS_PATH = "dashboard.tcss"
    TITLE = "heare"

    BINDINGS = [
        Binding("s", "start_daemon", "Start", show=True),
        Binding("x", "stop_daemon", "Stop", show=True),
        Binding("r", "restart_daemon", "Restart", show=True),
        Binding("m", "toggle_mute_bot", "Mute bot", show=True),
        Binding("M", "toggle_mute_mic", "Mute mic", show=True),
        Binding("t", "text_input", "Text inject", show=True),
        Binding("p", "toggle_provider", "Provider", show=False),
        Binding("q", "quit", "Quit", show=True),
        # Column resize
        Binding("left", "shrink_left", "Shrink left", show=False),
        Binding("right", "grow_left", "Grow left", show=False),
    ]

    settings: Settings           # passed via constructor
    _refresh_timer: Timer | None

    def action_start_daemon(self) -> None:
        msg = start_daemon(self.settings)
        self.query_one(ControlsBar).update_status(msg)

    def action_stop_daemon(self) -> None:
        msg = stop_daemon(self.settings)
        self.query_one(ControlsBar).update_status(msg)

    def action_restart_daemon(self) -> None:
        msg = restart_daemon(self.settings)
        self.query_one(ControlsBar).update_status(msg)

    def action_toggle_mute_bot(self) -> None:
        muted = toggle_mute(self.settings.mute_file)
        msg = "bot muted" if muted else "bot unmuted"
        self.query_one(ControlsBar).update_status(msg)

    def action_toggle_mute_mic(self) -> None:
        muted = toggle_input_mute(self.settings.mute_input_file)
        msg = "mic muted" if muted else "mic unmuted"
        self.query_one(ControlsBar).update_status(msg)

    def action_toggle_provider(self) -> None:
        pf = self.settings.provider_file
        current = pf.read_text().strip().lower() if pf.exists() else "openrouter"
        new_provider = "zai" if current == "openrouter" else "openrouter"
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(new_provider)
        self.query_one(ControlsBar).update_status(f"provider: {new_provider}")

    def action_text_input(self) -> None:
        self.query_one(ControlsBar).show_input()
```

### Widget tree

```
Screen
  Vertical
    HeaderBar          (Static, height=3, fixed)
    ActivityTable      (DataTable, scrollable, flex=1, full width)
    LogTail            (RichLog, height=8, scrollable)
    ControlsBar        (Static, height=3, fixed)
      [Input]          (hidden by default, shown only in text-inject mode)
```

Note: No separate Did panel. Activity is the sole body panel (see Section 4).

### Data-refresh model

- `set_interval(self._refresh_data, interval)` fires a timer (default 0.5s).
- `_refresh_data()` calls `fetch_dashboard_state(settings)` synchronously, returning a single `DashboardSnapshot`. Each widget's `refresh_data()` method receives the relevant slice of the snapshot.
- SQLite reads are bounded: each query has `LIMIT 50`, `timeout=0.5` on `sqlite3.connect` is kept.
- No `asyncio` threading for DB reads -- reads are fast enough on the Textual event loop. If profiling later shows stalls, wrap `fetch_dashboard_state` in `self.run_worker(thread=True)` (1-line change).
- Log file reading (`read_log_tail`) is also synchronous -- sub-millisecond for last N lines.

---

## 3. Activity Table Design

### Column shape

| Column | Width | Source | Content |
|--------|-------|--------|---------|
| `time` | 8 (fixed) | `ts` from both tables | `HH:MM:SS` via `fmt_time` |
| `WHO` | 12 (min) | speaker logic | See below |
| `TYPE` | 10 (fixed) | row source + status | `said` for transcripts; status badge for actions |
| `content` | flex | `text` or `tool: args -> result` | Truncated to terminal width |

### TYPE column: status-driven badges for actions

For transcript rows, TYPE displays `said` in cyan (user) or magenta (bot).

For action rows, TYPE displays the `status` value as a colored badge:

| Status value | Badge text | Color |
|-------------|-----------|-------|
| `ok` | `ok` | green |
| `done` | `done` | green |
| `error` | `error` | red |
| `cancelled` | `cancelled` | dim |
| `pending` | `pending` | yellow |
| (other) | raw status | white |

This replaces the plain "did" label from the original draft with actionable status information, preserving the visual density of the old `_did_table`'s status column.

### WHO column resolution

- **User transcripts** (`speaker_id IS NULL OR speaker_id != 'bot'`):
  - Look up `speaker_id` in `load_speaker_labels()` result.
  - If label found: display label (e.g., "Alice", "owner").
  - If `speaker_id` is NULL: display "you".
  - Style: `owner` -> bold green, other speakers -> yellow, NULL -> dim.

- **Bot transcripts** (`speaker_id = 'bot'`):
  - WHO = "bot", style = magenta.

- **Actions** (from `actions` table):
  - WHO = tool name if present (e.g., "bash", "web_search"), else "action".
  - Style = yellow.

### When DB has nothing

Display a single centered row: `"-- no activity yet --"` with dim italic style. The `DataTable` supports this via a regular row with merged content.

### Query

Single unified query via UNION ALL (replaces current two separate fetches + Python-side merge):

```sql
SELECT ts, 'said' AS type, text AS content, speaker_id AS who_key,
       NULL AS tool, NULL AS status
FROM transcripts
UNION ALL
SELECT ts, 'did' AS type,
       COALESCE(tool || ': ' || args, 'action') AS content,
       NULL AS who_key,
       tool, status
FROM actions
ORDER BY ts DESC
LIMIT 50
```

The `status` column from `actions` is included; transcripts get `NULL AS status`. The `ActivityRow` NamedTuple carries the status field. The widget uses it to render the colored badge in the TYPE column (see table above).

This is more efficient than the current approach and avoids the O(n log n) Python sort.

---

## 4. Did Panel Decision

### Recommendation: Fold Did into Activity (confirmed)

- The Activity table is the single body panel, full width.
- Actions show `status` as a colored badge in the TYPE column (e.g., `ok` green, `error` red, `pending` yellow) instead of a plain "did" label.
- Pressing Enter on an action row opens a detail overlay (a small modal or expanding row) showing the full `result_summary`. This gives the "deep dive" capability without splitting the layout.
- This satisfies Goal 3 ("collapse redundant body") while preserving all information from the current Did panel.

**If the user prefers to keep Did separate:** The architecture supports it trivially -- add a `DidTable(DataTable)` widget to a `Horizontal` container next to `ActivityTable`. The data layer already fetches actions independently.

### 4b. Tools Panel -- DEFERRED

The current dashboard has a `_tools_table()` panel listing all enabled tools from `tool_registry.TOOLS`. This panel is **explicitly deferred** from the Textual migration for the following reasons:

1. The tools list is static configuration data, not live dashboard state. It does not benefit from the scrolling/refresh goals driving this migration.
2. Adding it increases the widget tree complexity and consumes screen real estate for information that rarely changes.
3. The panel is not mentioned in any of the 5 migration goals.

**Consequence:** The two existing tests (`test_tools_table_lists_every_enabled_registry_tool`, `test_tools_table_shows_execution_kind`) are **deleted** in Step 10. They test a render function (`_tools_table()`) that has no equivalent in the new package.

**Follow-up:** A toggleable tools overlay (shown via a hotkey, e.g., `i` for "info") can be added as a post-migration enhancement. See Section 11.

---

## 5. Hotkey + Input-Mode Mapping

### Bindings table

Textual uses uppercase single-character form for Shift+letter bindings. Per Textual docs, `Binding("M", ...)` matches Shift+M. The `shift+m` form is an alias but `"M"` is the canonical documented syntax for single uppercase letters.

| Current key | Textual `Binding` | Action method | Notes |
|-------------|-------------------|---------------|-------|
| `s` | `Binding("s", "start_daemon", "Start")` | `action_start_daemon` | Calls `start_daemon(settings)` |
| `x` | `Binding("x", "stop_daemon", "Stop")` | `action_stop_daemon` | Calls `stop_daemon(settings)` |
| `r` | `Binding("r", "restart_daemon", "Restart")` | `action_restart_daemon` | Calls `restart_daemon(settings)` |
| `m` | `Binding("m", "toggle_mute_bot", "Mute bot")` | `action_toggle_mute_bot` | Calls `toggle_mute` |
| `M` (Shift+m) | `Binding("M", "toggle_mute_mic", "Mute mic")` | `action_toggle_mute_mic` | Calls `toggle_input_mute` |
| `t` | `Binding("t", "text_input", "Text")` | `action_text_input` | Opens input mode |
| `p` | `Binding("p", "toggle_provider", show=False)` | `action_toggle_provider` | Hidden binding |
| `q` | `Binding("q", "quit", "Quit")` | Built-in `action_quit` | Standard Textual quit |

### Text-inject mode implementation

**Recommended approach: in-place `Input` widget in ControlsBar.**

The current implementation uses `_handle_input_key()` with a manual `input_buffer` string. In Textual, replace this entirely with:

1. `ControlsBar` contains a hidden `Input` widget (`display: none` by default).
2. When `t` is pressed, `action_text_input` on the App:
   - Calls `self.query_one(ControlsBar).show_input()`.
   - Shows the `Input` widget, hides the hotkey hints `Static`.
   - Focuses the `Input` widget.
   - **Temporarily unbinds** single-key hotkeys (s/x/r/m/t/p/q) so they don't fire while typing. Textual supports this via `self.app._bindings.remove("s")` or by using a priority flag on the Input's key handler.
3. `Input.on_submit` (Enter): reads `self.value`, calls `inject_text(settings.inject_dir, text)`, hides Input, restores bindings.
4. `Key("escape")` on the Input: hides it, restores bindings, shows hotkey hints.

**Why not a modal Screen?** A modal screen would overlay the dashboard, hiding status updates. The inline Input keeps the dashboard visible during text composition -- matching current behavior.

**Why not a bare reactive flag + `on_key`?** It would work, but reimplements what `Input` already handles (cursor movement, backspace, printable filtering). The `Input` widget is the idiomatic Textual approach and gives paste support for free.

### Column resize hotkeys

- `Left` / `Right` arrow keys adjust the Activity panel width.
- Implementation: toggle between CSS classes (e.g., `activity-50`, `activity-65`, `activity-80`) that set `width: 50%` / `65%` / `80%` on the Activity container. Three presets, keyboard-only.
- Mouse drag resize: **Deferred.** Textual lacks a native splitter/drag widget. A custom `Splitter` would be ~100 LOC. Not justified for v1. Evaluate as a follow-up.

---

## 6. Step-by-Step Implementation Order

### Step 1: Scaffold the package + dependency

- Create `src/watch/` package with `__init__.py`, `app.py`, `widgets.py`, `data.py`, `dashboard.tcss`.
- `__init__.py` contains `run_watch()` directly (no `_compat.py`), the legacy env-var routing, and the once-mode renderer.
- Add `textual>=1.0.0,<2.0.0` to `pyproject.toml` `[project.dependencies]`.
- Verify: `uv run python -c "from src.watch import run_watch"` succeeds.

**Acceptance:** Import works. Existing `make test` still passes (data-layer functions re-exported from `__init__`).

### Step 2: Extract data layer into `data.py`

Move these functions from current `watch.py` to `data.py`:
- `_fmt_time`, `_truncate`, `_daemon_status`, `_current_mode`, `_current_provider`
- `_open_db`, `_fetch`, `_counts`
- `_load_speaker_labels`, `_speaker_style`

Make them public (drop the underscore prefix) since they are now a module API.

Define the `DashboardSnapshot` frozen dataclass and `fetch_dashboard_state(settings) -> DashboardSnapshot` as the single entry point for all widget data.

Write `fetch_activity(con, limit=50)` returning `list[ActivityRow]` (NamedTuple: ts, who, type_, content, style, status). Uses the UNION ALL query from Section 3 (including `status`).

Write `read_log_tail(log_path, lines=20)` returning `list[LogLine]` (NamedTuple: text, severity).

**`_dispatch_key` and `_handle_input_key` are NOT moved.** They are deleted. Their logic is absorbed by `action_*` methods in `app.py` (Step 7) and the `Input` widget in `ControlsBar` (Step 6).

**Index addition:** Add `CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts DESC)` to `src/storage.py` alongside the existing index definitions. This index supports the `ORDER BY ts DESC` in the UNION ALL query. The `transcripts` table already has `idx_transcripts_ts`.

**Acceptance:** All existing `test_watch.py` data-layer tests pass after updating imports. New unit tests for `fetch_activity`, `read_log_tail`, and `fetch_dashboard_state` pass. The `idx_actions_ts` index is created on DB init.

### Step 3: Build HeaderBar widget

Create `HeaderBar(Static)` in `widgets.py`.

- Constructor takes `settings: Settings`.
- `refresh_data(header: HeaderData)` updates the display from the snapshot's header slice.
- Renders the same two-line layout as current `_build_header`.

**Acceptance:** Unit test creates HeaderBar, calls `refresh_data` with a seeded HeaderData, and asserts the rendered text contains name, status, pid, uptime, mode, provider, counts.

### Step 4: Build ActivityTable widget

Create `ActivityTable(DataTable)` in `widgets.py`.

- Fixed columns: time (8), WHO (12), TYPE (10), content (flex).
- `refresh_data(rows: list[ActivityRow])` clears and re-populates rows.
- TYPE column uses status-driven colored badges for action rows (see Section 3).
- Rows styled per type (said=cyan/magenta, action=yellow/green/red by status).
- Scrollable by default (DataTable supports arrow keys, Page Up/Down, Home/End).

**Acceptance:** Pilot test creates the widget, populates 50 rows, asserts row count, scrolls to bottom, verifies last row content. Verifies an action row with `status="error"` displays a red badge.

### Step 5: Build LogTail widget

Create `LogTail(RichLog)` in `widgets.py`.

- `refresh_data(lines: list[LogLine])` clears and writes styled lines.
- Severity coloring: ERROR=red, WARNING=yellow, INFO=cyan, else=default.
- `max_lines=50` to cap memory.

**Acceptance:** Pilot test writes 30 lines including ERROR/WARNING/INFO, asserts line count, verifies severity styling is applied.

### Step 6: Build ControlsBar + TextInput

Create `ControlsBar(Static)` in `widgets.py`.

- Shows: status indicator, pid, mute states, hotkey hints.
- Contains a hidden `Input` widget for text injection.
- `show_input()` / `hide_input()` toggle between hints and Input.
- `update_status(msg)` displays last-action feedback.
- The `Input` widget's `on_submit` and `on_key(escape)` handlers replace all `_handle_input_key` logic.

**Acceptance:** Pilot test verifies hotkey hints are visible by default. Simulating `t` key shows Input widget. Typing + Enter calls inject callback. Escape hides Input.

### Step 7: Assemble App + bindings

Create `HeareDashboard(App)` in `app.py`.

- Mount: HeaderBar, ActivityTable, LogTail, ControlsBar.
- Bind all hotkeys per Section 5 using `Binding("M", ...)` for Shift+M.
- `set_interval` timer calls `_refresh_data` which fetches `DashboardSnapshot` and distributes to widgets.
- `action_*` methods replace all `_dispatch_key` logic (see skeleton in Section 2).
- `action_text_input` delegates to `ControlsBar.show_input()`.

**Acceptance:** `App.run_test()` boots successfully. Pressing `s` triggers `start_daemon` mock. Pressing `q` exits cleanly. Pressing `M` triggers `toggle_input_mute` mock (not `toggle_mute`).

### Step 8: Implement `run_watch()` in `__init__.py` + `once` mode

`run_watch(settings, interval, once)` lives directly in `__init__.py` (no `_compat.py`):

- **`once=True` (option b -- bypass App):** Call `fetch_dashboard_state(settings)` to get a `DashboardSnapshot`. Pass it to a thin renderer function that builds a `rich.Table` + `rich.Panel` layout using `rich.Console` and prints to stdout. Exit with return code 0. No Textual App instantiation at all.
- **`once=False`:** Instantiate `HeareDashboard(settings=settings, interval=interval)` and call `app.run()` (sync-blocking). This replaces the current `with Live(...)` loop.
- Remove old `_make_key_reader` / termios hack entirely.

**Acceptance:** `uv run python -m src.main watch --once` prints dashboard snapshot to stdout and exits. Output is valid text (not ANSI escape sequences for a full-screen app). `uv run python -m src.main watch` opens interactive TUI.

### Step 9: Column resize + CSS polish (`dashboard.tcss`)

Create `src/watch/dashboard.tcss` with the following minimal content:

```css
/* Column ratios -- 3 presets toggled by Left/Right keys */
.activity-50 { width: 50%; }
.activity-65 { width: 65%; }
.activity-80 { width: 80%; }

/* Focus highlight */
DataTable:focus { border: tall $accent; }
RichLog:focus { border: tall $accent; }

/* Severity colors for LogTail lines (applied via Rich.Text, not CSS,
   but border/background for the widget container) */
LogTail { height: 8; border: round $surface-darken-2; }

/* Status badge colors in ActivityTable TYPE column
   (applied via Rich.Text styling in widget code, documented here for reference) */
/* ok/done = green, error = red, pending = yellow, cancelled = dim */

/* Header and Controls fixed heights */
HeaderBar { height: 3; }
ControlsBar { height: 3; }

/* Input widget inside ControlsBar -- hidden by default */
ControlsBar Input { display: none; }
ControlsBar Input.visible { display: block; }
```

- Add `Left`/`Right` bindings to cycle between the 3 width classes on `ActivityTable`.
- Fine-tune borders, spacing to match current aesthetic.
- Add `TITLE` and `SUB_TITLE` to App.

**Acceptance:** Pressing Left/Right visibly changes panel proportions among 3 presets. Dashboard renders cleanly at 80x24, 120x40, and 200x60 terminal sizes. `dashboard.tcss` loads without Textual CSS parse errors.

### Step 10: Migrate tests + cleanup

See the complete per-test migration matrix below.

- Update `tests/test_watch.py` imports to `src.watch.data` for data-layer tests.
- Add new `tests/test_watch_app.py` with Textual pilot tests (see Section 7).
- Add `tests/test_watch_controls.py` rewrites for key-dispatch tests.
- Delete the old `src/watch.py` file (now replaced by `src/watch/` package).
- Verify `make test`, `make lint`, `make check` all pass.

#### Per-test migration matrix: `tests/test_watch.py`

| Test name | Disposition | Destination | Notes |
|-----------|------------|-------------|-------|
| `test_daemon_status_not_running` | **port-as-is** | `test_watch.py` | Tests `data.daemon_status()`. Update import only. |
| `test_daemon_status_stale_pid` | **port-as-is** | `test_watch.py` | Tests `data.daemon_status()`. Update import only. |
| `test_current_mode_no_file` | **port-as-is** | `test_watch.py` | Tests `data.current_mode()`. Update import only. |
| `test_current_mode_reads_file` | **port-as-is** | `test_watch.py` | Tests `data.current_mode()`. Update import only. |
| `test_fmt_time` | **port-as-is** | `test_watch.py` | Tests `data.fmt_time()`. Update import only. |
| `test_truncate` | **port-as-is** | `test_watch.py` | Tests `data.truncate()`. Update import only. |
| `test_truncate_short_string` | **port-as-is** | `test_watch.py` | Tests `data.truncate()`. Update import only. |
| `test_counts_empty_db` | **port-as-is** | `test_watch.py` | Tests `data.counts()`. Update import only. |
| `test_open_db_readonly` | **port-as-is** | `test_watch.py` | Tests `data.open_db()`. Update import only. |
| `test_open_db_missing_file` | **port-as-is** | `test_watch.py` | Tests `data.open_db()`. Update import only. |
| `test_watch_cli_default_interval_is_half_second` | **port-as-is** | `test_watch.py` | Tests CLI parser, no watch.py dependency. |
| `test_you_table_shows_user_transcripts` | **rewrite** | `test_watch_app.py` | Old test called `_you_table()` Rich function. Rewrite as pilot test: seed DB, boot App, assert ActivityTable rows contain user transcript with WHO="you"/"owner". |
| `test_you_table_shows_speaker_labels` | **rewrite** | `test_watch_app.py` | Rewrite as pilot test: seed DB with labeled speakers, assert ActivityTable WHO column shows labels. |
| `test_did_table_shows_tool_and_args` | **rewrite** | `test_watch_app.py` | Rewrite as pilot test: seed actions, assert ActivityTable shows tool name in WHO, status badge in TYPE, args in content. |
| `test_activity_table_merges_transcripts_and_actions` | **rewrite** | `test_watch_app.py` | Rewrite as pilot test: seed mixed data, assert unified ActivityTable row count and chronological order. |
| `test_build_layout_has_activity_and_three_body_columns` | **delete** | -- | Asserts the old 3-column Rich Layout structure (you/bot/did). This structure no longer exists. Replaced by `test_app_boots_with_seeded_db` which asserts single ActivityTable. |
| `test_build_layout_has_three_column_body` | **delete** | -- | Same rationale as above. Old layout structure is gone. |
| `test_empty_tables_render_none_yet_placeholders` | **rewrite** | `test_watch_app.py` | Rewrite as pilot test: boot App with empty DB, assert ActivityTable shows placeholder row. |
| `test_tools_table_lists_every_enabled_registry_tool` | **delete** | -- | Tools panel deferred (Section 4b). No equivalent in new package. |
| `test_tools_table_shows_execution_kind` | **delete** | -- | Tools panel deferred (Section 4b). No equivalent in new package. |
| `test_bot_table_renders_assistant_responses` | **rewrite** | `test_watch_app.py` | Rewrite as pilot test: seed bot transcripts, assert ActivityTable shows WHO="bot" with magenta style. |
| `test_bot_table_handles_empty_db` | **delete** | -- | Covered by `test_app_boots_with_empty_db` placeholder assertion. |
| `test_you_table_filters_bot_responses` | **rewrite** | `test_watch_app.py` | Rewrite as pilot test: seed both user and bot transcripts, assert both appear in ActivityTable with distinct WHO values (not filtered -- unified feed shows all). |

#### Per-test migration matrix: `tests/test_watch_controls.py`

| Test name | Disposition | Destination | Notes |
|-----------|------------|-------------|-------|
| `test_daemon_pid_returns_none_when_no_pid_file` | **port-as-is** | stays in `test_watch_controls.py` | Tests `watch_controls.daemon_pid()`. No change. |
| `test_daemon_pid_returns_none_for_dead_process` | **port-as-is** | stays | No change. |
| `test_daemon_pid_returns_pid_for_live_process` | **port-as-is** | stays | No change. |
| `test_daemon_pid_returns_none_for_garbage_pid_file` | **port-as-is** | stays | No change. |
| `test_stop_daemon_when_not_running_cleans_stale_pid` | **port-as-is** | stays | No change. |
| `test_stop_daemon_when_no_pid_file` | **port-as-is** | stays | No change. |
| `test_stop_daemon_sigterm_pathway` | **port-as-is** | stays | No change. |
| `test_start_daemon_refuses_when_already_running` | **port-as-is** | stays | No change. |
| `test_start_daemon_spawns_subprocess` | **port-as-is** | stays | No change. |
| `test_dispatch_key_routes_each_action` | **rewrite** | `test_watch_app.py` | Currently calls `watch._dispatch_key(s, "s")`. Rewrite as pilot test: `await pilot.press("s")` then assert `start_daemon` mock was called. Repeat for x, r. |
| `test_dispatch_key_toggles_bot_mute` | **rewrite** | `test_watch_app.py` | Pilot test: `await pilot.press("m")`, assert `toggle_mute` called and status message displayed. |
| `test_dispatch_key_toggles_mic_mute` | **rewrite** | `test_watch_app.py` | Pilot test: `await pilot.press("M")`, assert `toggle_input_mute` called. Verifies `Binding("M", ...)` works. |
| `test_handle_input_key_appends_printable` | **rewrite** | `test_watch_app.py` | Pilot test: `await pilot.press("t"); await pilot.type("hello")`, assert Input widget value is "hello". |
| `test_handle_input_key_backspace` | **rewrite** | `test_watch_app.py` | Pilot test: type chars then press backspace, assert Input value reflects deletion. |
| `test_handle_input_key_escape_cancels` | **rewrite** | `test_watch_app.py` | Pilot test: enter input mode, type, press escape, assert Input hidden and no injection. |
| `test_handle_input_key_enter_injects` | **rewrite** | `test_watch_app.py` | Pilot test: enter input mode, type text, press enter, assert `inject_text` called with correct text. |
| `test_handle_input_key_empty_enter_cancels` | **rewrite** | `test_watch_app.py` | Pilot test: enter input mode, press enter with empty/whitespace input, assert no injection. |
| `test_restart_daemon_returns_combined_status` | **port-as-is** | stays in `test_watch_controls.py` | Tests `watch_controls.restart_daemon()`. No change. |

#### New tests to add

| Test name | File | Purpose |
|-----------|------|---------|
| `test_once_mode_outputs_to_stdout` | `test_watch_app.py` | Call `run_watch(settings, interval=0.5, once=True)`, capture stdout, assert it contains header info + activity data. Assert no Textual App was instantiated. |
| `test_legacy_env_var_returns_callable` | `test_watch_app.py` | Set `HEARE_WATCH_LEGACY=1`, assert `from src.watch import run_watch` returns a callable (the legacy function, not the new one). Verifies the rollback env var works. |
| `test_app_boots_without_db` | `test_watch_app.py` | Smoke test -- see Section 7. |
| `test_app_boots_with_empty_db` | `test_watch_app.py` | Smoke test -- see Section 7. |
| `test_app_boots_with_seeded_db` | `test_watch_app.py` | Smoke test -- see Section 7. |
| `test_provider_toggle_key` | `test_watch_app.py` | Pilot test: press `p`, assert provider file written with toggled value. |

**Acceptance:** Full test suite green. No ruff warnings. `make watch` works end-to-end. Every test in the matrix above is accounted for.

---

## 7. Test Plan

All tests use `pytest` + `pytest-asyncio`. Textual pilot tests are async.

### Smoke tests (`test_watch_app.py`)

```python
async def test_app_boots_without_db():
    """Dashboard starts even when no DB file exists."""
    app = HeareDashboard(settings=_make_settings(tmp))
    async with app.run_test() as pilot:
        assert app.query_one(HeaderBar)
        assert app.query_one(ActivityTable)

async def test_app_boots_with_empty_db():
    """Dashboard starts with an empty (schema-only) DB."""
    ...  # create schema, assert placeholder rows

async def test_app_boots_with_seeded_db():
    """Dashboard shows real data from a populated DB."""
    ...  # seed 10 transcripts + 5 actions, assert row count
```

### Key-binding tests (replace _dispatch_key tests)

```python
async def test_quit_key():
    async with app.run_test() as pilot:
        await pilot.press("q")
        # App should exit

async def test_start_key_calls_start_daemon(monkeypatch):
    monkeypatch.setattr("src.watch.app.start_daemon", mock_start)
    async with app.run_test() as pilot:
        await pilot.press("s")
        assert mock_start.called

async def test_mute_bot_toggle(monkeypatch):
    monkeypatch.setattr("src.watch.app.toggle_mute", mock_toggle)
    async with app.run_test() as pilot:
        await pilot.press("m")
        assert mock_toggle.called

async def test_mute_mic_toggle(monkeypatch):
    """Binding("M", ...) must trigger toggle_input_mute, not toggle_mute."""
    monkeypatch.setattr("src.watch.app.toggle_input_mute", mock_toggle_input)
    async with app.run_test() as pilot:
        await pilot.press("M")
        assert mock_toggle_input.called

async def test_provider_toggle(monkeypatch):
    async with app.run_test() as pilot:
        await pilot.press("p")
        # Assert provider file toggled
```

### Text-input mode tests (replace _handle_input_key tests)

```python
async def test_text_input_mode_enter():
    async with app.run_test() as pilot:
        await pilot.press("t")          # enter input mode
        await pilot.type("hello world")
        await pilot.press("enter")      # submit
        # Assert inject_text was called with "hello world"

async def test_text_input_mode_escape():
    async with app.run_test() as pilot:
        await pilot.press("t")
        await pilot.type("partial")
        await pilot.press("escape")     # cancel
        # Assert input is hidden, no injection happened

async def test_hotkeys_disabled_during_input():
    async with app.run_test() as pilot:
        await pilot.press("t")          # enter input mode
        await pilot.press("s")          # should type "s", not start daemon
        # Assert start_daemon was NOT called
```

### Once-mode test

```python
def test_once_mode_outputs_to_stdout(tmp_path, capsys):
    """once=True must bypass App, render via rich.Console to stdout, and exit."""
    settings = _make_settings(tmp_path)
    _seed_db(settings.db_path)
    rc = run_watch(settings, interval=0.5, once=True)
    assert rc == 0
    captured = capsys.readouterr()
    assert "heare" in captured.out        # header present
    assert len(captured.out) > 100        # non-trivial output
```

### Legacy rollback test

```python
def test_legacy_env_var_returns_callable(monkeypatch):
    """HEARE_WATCH_LEGACY=1 must route to the old run_watch."""
    monkeypatch.setenv("HEARE_WATCH_LEGACY", "1")
    # Force reimport
    import importlib
    import src.watch
    importlib.reload(src.watch)
    assert callable(src.watch.run_watch)
```

### Data layer tests (migrate from current `test_watch.py`)

- `test_counts_empty_db` -- unchanged (update import)
- `test_open_db_readonly` -- unchanged (update import)
- `test_fetch_activity_merges_transcripts_and_actions` -- rewritten to test new `fetch_activity()` returning `ActivityRow` with status field
- `test_speaker_labels` -- unchanged (update import)
- `test_read_log_tail_severity` -- new, tests `read_log_tail` output
- `test_fetch_dashboard_state_returns_frozen_snapshot` -- new, tests the single-call pattern

### Daemon-control mocking strategy

- All `watch_controls` functions (`start_daemon`, `stop_daemon`, `restart_daemon`) are monkeypatched in pilot tests.
- `toggle_mute` / `toggle_input_mute` are monkeypatched.
- `inject_text` is monkeypatched.
- No real daemon processes in tests. No real file I/O for mute files (use tmp dirs).

---

## 8. Risks & Mitigations

### Risk 1: Terminal compatibility (macOS Terminal.app / iTerm2 / tmux / SSH)

**Likelihood:** Medium.

**Impact:** Dashboard may render incorrectly, keys may not register, or mouse events may misbehave.

**Mitigation:**
- Test manually in Terminal.app, iTerm2, and tmux before merging.
- Disable mouse support initially (`App.ENABLE_COMMAND_PALETTE = False`) to reduce the surface area.
- Textual has a built-in `textual diagnose` command for debugging terminal capabilities.

### Risk 2: Textual version churn

**Likelihood:** Medium.

**Impact:** API breakage on updates.

**Mitigation:**
- Pin `textual>=1.0.0,<2.0.0` in `pyproject.toml`.
- Use only stable, documented APIs (`DataTable`, `RichLog`, `Static`, `Input`, `App`, `Binding`). Avoid internal/private APIs.
- The test suite catches regressions on upgrade.

### Risk 3: Regression in hotkey muscle memory

**Likelihood:** Low-Medium.

**Impact:** Keys might behave differently under Textual's event system vs raw termios.

**Mitigation:**
- Explicit key-binding pilot test for every hotkey (see Section 7).
- `Binding("M", ...)` is the canonical Textual form for Shift+M. This is confirmed and used consistently throughout the plan.
- Test that `m` and `M` trigger different actions in the same pilot test suite.

### Risk 4: Blocking SQLite reads stalling the UI thread

**Likelihood:** Low (queries bounded by LIMIT 50, local DB), but non-zero.

**Impact:** Dashboard freezes during refresh cycle.

**Mitigation:**
- All queries have `LIMIT 50` and `timeout=0.5`.
- The `DashboardSnapshot` single-call pattern isolates all DB access in one function. Moving to `self.run_worker(fetch_dashboard_state, thread=True)` is a 1-line change.
- Add a watchdog: if `_refresh_data` takes > 100ms, log a warning.

### Risk 5: `once` mode regression

**Likelihood:** Low.

**Impact:** `heare watch --once` (used in scripts/monitoring) breaks.

**Mitigation:**
- `once` mode bypasses the Textual App entirely (option b). Uses `fetch_dashboard_state()` + `rich.Console` to print a static snapshot to stdout.
- Explicit `test_once_mode_outputs_to_stdout` test validates this path.
- No dependency on Textual for once mode -- if Textual has issues, once mode still works.

### Risk 6: Dependency weight

**Likelihood:** Low. Textual is pure Python, no C extensions.

**Impact:** Adds ~2MB to the dependency tree.

**Mitigation:** Textual's only hard dependency is `rich` (already installed). The incremental cost is minimal.

---

## 9. Acceptance Criteria

Checklist mapping 1:1 to Goals 1-5:

- [ ] **Goal 1 -- Real scrolling:** Activity table supports Page Up/Down/Home/End navigation through 50+ rows. Log tail supports scrolling through 50+ log lines. Scrollbar is visible when content overflows.
- [ ] **Goal 2 -- Resizable columns:** Left/Right arrow keys visibly change the Activity panel width between 3 presets (50%/65%/80%).
- [ ] **Goal 3 -- Collapse redundant body:** The 3-column You/Bot/Did body is replaced by a single Activity table with WHO and TYPE columns. TYPE shows status badges for actions. No duplicate data panels.
- [ ] **Goal 4a -- Header:** Header line shows name+emoji, status (running/stopped), pid, uptime, mode, provider, transcript count, action count.
- [ ] **Goal 4b -- Hotkeys:** s (start), x (stop), r (restart), m (mute bot), M (mute mic), t (text inject), q (quit), p (provider toggle) all work via `action_*` methods.
- [ ] **Goal 4c -- Text injection:** Pressing t opens inline Input widget. Enter submits to `inject_text`. Escape cancels. Backspace works. Hotkeys are suppressed during input. Buffer content is visible.
- [ ] **Goal 4d -- Log panel:** daemon.log tail shows last N lines, colored by severity (ERROR=red, WARNING=yellow, INFO=cyan).
- [ ] **Goal 4e -- Read-only DB:** SQLite connection uses `?mode=ro` URI. No write operations in any code path.
- [ ] **Goal 5 -- Stable CLI:** `run_watch(settings: Settings, interval: float, once: bool = False) -> int` signature is preserved. `from src.watch import run_watch` works. `heare watch`, `heare watch --once`, `heare watch --interval 1.0` all work.
- [ ] **Once mode:** `--once` bypasses App, prints to stdout via `rich.Console`, exits with rc=0. `test_once_mode_outputs_to_stdout` passes.
- [ ] **Tests:** At least 3 smoke tests, 6+ key-binding pilot tests (covering all `action_*` methods), 3+ text-input pilot tests, all data-layer tests ported, once-mode test, legacy env-var test. Every test in the migration matrix accounted for.
- [ ] **No regressions:** `make test`, `make lint`, `make check` all pass.
- [ ] **CSS loads:** `dashboard.tcss` parses without errors. Column ratios, focus highlight, and severity styles render correctly.

---

## 10. Rollback Plan

### Strategy: feature flag via environment variable

Before deleting the old `watch.py`, preserve it as `watch/_legacy.py`.

```python
# watch/__init__.py
import os

if os.environ.get("HEARE_WATCH_LEGACY") == "1":
    from ._legacy import run_watch
else:
    # ... new run_watch defined here
```

This lets any user revert instantly with `HEARE_WATCH_LEGACY=1 heare watch` if the new TUI has issues.

**Legacy import smoke test:** `test_legacy_env_var_returns_callable` verifies this path works (see Section 7).

### Timeline

- Keep `_legacy.py` for 2 release cycles (or ~4 weeks).
- After confirmation that the Textual dashboard is stable, delete `_legacy.py` and the env-var check.
- Tag the commit before deletion as `watch-legacy-last` for emergency archaeology.

### Git safety

- The migration PR should be a single squash-merge to `main` so reverting is one `git revert`.
- Do NOT interleave migration commits with other feature work.

---

## 11. Follow-ups

Post-migration enhancements tracked here. These are explicitly out of scope for the migration PR.

1. **Toggleable tools overlay** -- Add a hotkey (e.g., `i` for "info") that shows a modal or overlay listing enabled tools from `tool_registry.TOOLS` with name, execution kind, and description. Replaces the deferred `_tools_table()` panel.
2. **Mouse-drag column resize** -- Implement a custom `Splitter` widget (~100 LOC) for drag-to-resize between Activity and a future panel. Requires Textual mouse event handling.
3. **`run_worker(thread=True)` for DB reads** -- If profiling shows `_refresh_data` > 16ms, move `fetch_dashboard_state()` into a Textual Worker thread. The `DashboardSnapshot` pattern makes this a 1-line change.
4. **Action detail overlay** -- Pressing Enter on an action row in ActivityTable opens a modal showing full `result_summary`. Requires a Textual `ModalScreen` subclass.
5. **Textual 2.x migration** -- Monitor for breaking changes when Textual 2.x is released. The `<2.0.0` pin protects against surprise breakage.

---

## Appendix: Files Modified

| File | Change |
|------|--------|
| `src/watch.py` | **Deleted** -- replaced by `src/watch/` package |
| `src/watch/__init__.py` | **New** -- `run_watch()`, once-mode renderer, legacy env-var routing |
| `src/watch/app.py` | **New** -- `HeareDashboard(App)`, all `action_*` methods, text-input mode |
| `src/watch/widgets.py` | **New** -- HeaderBar, ActivityTable, LogTail, ControlsBar |
| `src/watch/data.py` | **New** -- `DashboardSnapshot`, `fetch_dashboard_state()`, extracted data-access functions |
| `src/watch/dashboard.tcss` | **New** -- Textual CSS: column ratios, focus highlight, severity colors, status badge colors, Input visibility |
| `src/watch/_legacy.py` | **New** -- copy of old `watch.py` for rollback |
| `src/storage.py` | **Modified** -- add `CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts DESC)` |
| `pyproject.toml` | **Modified** -- add `textual>=1.0.0,<2.0.0` |
| `tests/test_watch.py` | **Modified** -- update imports to `src.watch.data`, delete layout/tools/bot-empty tests |
| `tests/test_watch_app.py` | **New** -- Textual pilot tests, once-mode test, legacy env-var test |
| `tests/test_watch_controls.py` | **Modified** -- delete 8 `_dispatch_key`/`_handle_input_key` tests (moved to `test_watch_app.py` as pilot tests); keep 10 `watch_controls.*` tests unchanged |
| `src/watch_controls.py` | **Unchanged** |
| `src/config.py` | **Unchanged** |
| `src/main.py` | **Unchanged** |
| `Makefile` | **Unchanged** |
