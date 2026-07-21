import React, { useRef, useEffect, useState } from 'react';

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

  function toggleRow(key) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  // Auto-scroll logs to bottom when new lines arrive
  useEffect(() => {
    if (logRef.current) {
      const el = logRef.current;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
      if (atBottom) el.scrollTop = el.scrollHeight;
    }
  }, [logs]);

  return (
    <div className="card">
      <div className="card-header">
        <span style={{flex: 1}}>history</span>
        <button className={"history-tab-btn" + (tab === "activity" ? " active" : "")} onClick={() => onTabChange("activity")}>
          activity ({activity.length})
        </button>
        <button className={"history-tab-btn" + (tab === "logs" ? " active" : "")} onClick={() => onTabChange("logs")}>
          logs ({logs.length})
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
                {/* The two tabs run in opposite directions — say so rather
                    than surprise the reader. */}
                <th>content <span className="th-hint">newest first</span></th>
              </tr>
            </thead>
            <tbody>
              {activity.length === 0 ? (
                <tr>
                  <td colSpan="3" style={{textAlign: "center", color: "var(--muted)", padding: "24px 12px", fontSize: 13}}>
                    No activity yet {'—'} start speaking
                  </td>
                </tr>
              ) : activity.map((row) => {
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
        <div className="scroll scroll-logs" ref={logRef}>
          <table>
            <tbody>
              {logs.length === 0 ? (
                <tr>
                  <td style={{textAlign: "center", color: "var(--muted)", padding: "16px 12px", fontSize: 12}}>
                    Daemon log lines appear here
                  </td>
                </tr>
              ) : logs.map((line, i) => (
                <tr key={i}>
                  <td style={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    fontSize: 11,
                    color: line.toUpperCase().includes("ERROR")
                      ? "var(--accent-red)"
                      : line.toUpperCase().includes("WARN")
                        ? "var(--accent-yellow)"
                        : "var(--muted)"
                  }}>{line}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
