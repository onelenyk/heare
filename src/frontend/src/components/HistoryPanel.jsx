import React, { useRef, useEffect } from 'react';

export default function HistoryPanel({ activity, logs, tab, onTabChange, onClose }) {
  const logRef = useRef(null);

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
          <button className="modal-close" onClick={onClose} style={{marginLeft: 4}}>{'\u00d7'}</button>
        )}
      </div>
      {tab === "activity" ? (
        <div className="scroll">
          <table>
            <thead>
              <tr><th>time</th><th>who</th><th>content</th></tr>
            </thead>
            <tbody>
              {activity.length === 0 ? (
                <tr>
                  <td colSpan="3" style={{textAlign: "center", color: "var(--muted)", padding: "24px 12px", fontSize: 13}}>
                    No activity yet {'\u2014'} start speaking
                  </td>
                </tr>
              ) : activity.map((row, i) => (
                <tr key={i}>
                  <td className="cell-muted" style={{width: 56}}>{new Date(row.ts * 1000).toTimeString().slice(0, 8)}</td>
                  <td className={row.who === "bot" ? "cell-bot" : "cell-you"} style={{width: 36}}>{row.who}</td>
                  <td style={{overflow: "hidden", textOverflow: "ellipsis"}}>{(row.content || "").substring(0, 80)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="scroll" style={{maxHeight: 240}} ref={logRef}>
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
