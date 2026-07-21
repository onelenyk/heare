import React, { useRef, useEffect, useState } from 'react';
import {
  parseLogs, filterLogs, countByCategory, LOG_CATEGORIES, defaultCategories,
} from '../logParse';

const ALL_CATS = new Set(LOG_CATEGORIES.map(c => c.key));

// Timestamps only carried HH:MM:SS, so yesterday looked like today. Older
// rows get a date stacked above the time — keeps the column narrow enough
// for the sidebar instead of widening it for every row.
function splitTimestamp(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const sameDay =
    d.getDate() === today.getDate() &&
    d.getMonth() === today.getMonth() &&
    d.getFullYear() === today.getFullYear();
  return {
    time: d.toTimeString().slice(0, 8),
    date: sameDay
      ? null
      : `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`,
  };
}

export default function HistoryPanel({
  activity, logs, tab, onTabChange, onClose,
  onLoadOlder, loadingOlder, noMoreActivity,
}) {
  const logRef = useRef(null);
  // Rows are clipped to one line by default; clicking one lets it wrap so the
  // full message is readable without leaving the panel.
  const [expanded, setExpanded] = useState(() => new Set());

  // ── Log filters ──
  const [cats, setCats] = useState(defaultCategories);
  const [minLevel, setMinLevel] = useState('DEBUG');
  const [query, setQuery] = useState('');

  // Sort direction per tab. Defaults differ because the natural reading order
  // does: activity is an audit feed (newest first), logs are a tail (newest
  // last). Both are switchable.
  const [activityDesc, setActivityDesc] = useState(true);
  const [logsDesc, setLogsDesc] = useState(false);

  const logEntries = React.useMemo(() => parseLogs(logs), [logs]);
  const catCounts = React.useMemo(() => countByCategory(logEntries), [logEntries]);
  const shownLogs = React.useMemo(() => {
    const filtered = filterLogs(logEntries, { cats, minLevel, query });
    // Entries arrive in file order (oldest first).
    return logsDesc ? [...filtered].reverse() : filtered;
  }, [logEntries, cats, minLevel, query, logsDesc]);

  // mergeActivity keeps this newest-first; reverse for the other direction.
  const shownActivity = React.useMemo(
    () => (activityDesc ? activity : [...activity].reverse()),
    [activity, activityDesc]
  );

  function toggleCat(key) {
    setCats(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }
  // Click-through on a chip you've already soloed restores everything.
  function soloCat(key) {
    setCats(prev => (prev.size === 1 && prev.has(key)) ? new Set(ALL_CATS) : new Set([key]));
  }

  function toggleRow(key) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  // Follow new lines — which end they arrive at depends on the sort, so stick
  // to whichever edge the newest entry lands on.
  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    if (logsDesc) {
      if (el.scrollTop < 30) el.scrollTop = 0;
    } else {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
      if (atBottom) el.scrollTop = el.scrollHeight;
    }
  }, [logs, logsDesc]);

  return (
    <div className="card">
      <div className="card-header">
        <span style={{flex: 1}}>history</span>
        <button className={"history-tab-btn" + (tab === "activity" ? " active" : "")} onClick={() => onTabChange("activity")}>
          activity ({activity.length})
        </button>
        <button className={"history-tab-btn" + (tab === "logs" ? " active" : "")} onClick={() => onTabChange("logs")}>
          logs ({shownLogs.length}/{logEntries.length})
        </button>
        {onClose && (
          <button className="modal-close" onClick={onClose} style={{marginLeft: 4}}>{'×'}</button>
        )}
      </div>
      {tab === "activity" ? (
        <div className="scroll">
          <table className="history-table">
            <thead>
              <tr>
                <th>time</th>
                <th>who</th>
                {/* Each tab keeps the reading order its content wants, and
                    both say which one they are using. */}
                <th>
                  content
                  <button
                    className="th-sort"
                    onClick={() => setActivityDesc(v => !v)}
                    title="toggle sort direction"
                  >{activityDesc ? 'newest first ↓' : 'oldest first ↑'}</button>
                </th>
              </tr>
            </thead>
            <tbody>
              {shownActivity.length === 0 ? (
                <tr>
                  <td colSpan="3" style={{textAlign: "center", color: "var(--muted)", padding: "24px 12px", fontSize: 13}}>
                    No activity yet {'—'} start speaking
                  </td>
                </tr>
              ) : shownActivity.map((row) => {
                const key = row.id != null ? row.id : `${row.ts}:${row.content}`;
                const isOpen = expanded.has(key);
                const { time, date } = splitTimestamp(row.ts);
                return (
                  <tr
                    key={key}
                    className={"history-row" + (isOpen ? " expanded" : "")}
                    onClick={() => toggleRow(key)}
                  >
                    <td className="cell-muted">
                      {date && <span className="cell-date">{date}</span>}
                      {time}
                    </td>
                    <td className={row.who === "bot" ? "cell-bot" : "cell-you"}>{row.who}</td>
                    <td className="cell-content" title={row.content || ""}>
                      {row.content || ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {activity.length > 0 && onLoadOlder && (
            <div className="history-more">
              {noMoreActivity ? (
                <span className="history-more-note">no older activity</span>
              ) : (
                <button className="btn btn-tiny" onClick={onLoadOlder} disabled={loadingOlder}>
                  {loadingOlder ? 'loading…' : 'load older'}
                </button>
              )}
            </div>
          )}
        </div>
      ) : (
        <>
        <div className="log-filters">
          <div className="log-filter-row">
            <input
              className="log-search"
              type="search"
              value={query}
              placeholder="search messages…"
              onInput={e => setQuery(e.target.value)}
            />
            <button
              className="log-sort-btn"
              onClick={() => setLogsDesc(v => !v)}
              title="toggle sort direction"
            >{logsDesc ? 'newest first ↓' : 'oldest first ↑'}</button>
            <select
              className="log-level-select"
              value={minLevel}
              onChange={e => setMinLevel(e.target.value)}
              title="minimum severity"
            >
              <option value="DEBUG">all</option>
              <option value="INFO">info+</option>
              <option value="WARNING">warn+</option>
              <option value="ERROR">errors</option>
            </select>
          </div>
          <div className="log-chips">
            {LOG_CATEGORIES.map(c => (
              <button
                key={c.key}
                className={'log-chip' + (cats.has(c.key) ? ' on' : '')}
                onClick={e => (e.shiftKey ? soloCat(c.key) : toggleCat(c.key))}
                title="click to toggle · shift-click to show only this"
              >
                {c.label}
                <span className="log-chip-count">{catCounts[c.key] || 0}</span>
              </button>
            ))}
            <button className="log-chip-all" onClick={() => setCats(new Set(ALL_CATS))}>all</button>
            <button className="log-chip-all" onClick={() => setCats(new Set())}>none</button>
          </div>
        </div>
        <div className="scroll scroll-logs" ref={logRef}>
          {shownLogs.length === 0 ? (
            <div className="log-empty">
              {logEntries.length === 0
                ? 'Daemon log lines appear here'
                : `No lines match — ${logEntries.length} hidden by filters`}
            </div>
          ) : shownLogs.map((e, i) => (
            <div key={i} className={'log-card log-' + (e.level.toLowerCase() || 'cont')}>
              {e.level && (
                <div className="log-card-head">
                  <span className="log-level">{e.level}</span>
                  <span className="log-logger" title={e.logger}>{e.logger}</span>
                  <span className="log-time">{e.time}</span>
                </div>
              )}
              {e.message && <div className="log-msg">{e.message}</div>}
              {e.detail.length > 0 && (
                <pre className="log-detail">{e.detail.join('\n')}</pre>
              )}
            </div>
          ))}
        </div>
        </>
      )}
    </div>
  );
}
