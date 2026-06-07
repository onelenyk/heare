import React from 'react';

export default function StatusBar({ state, onProviderChange, onModelChange }) {
  const running = state.running === true;
  const interruptEnabled = state.interrupt_enabled !== false;

  return (
    <div className="status-bar">
      <div className="status-row">
        <div className="status-group">
          <span className={"dot " + (running ? "on" : "off")}></span>
          <span className="identity">{state.agent || "heare"} {state.emoji || ""}</span>
          <span className={"status-badge " + (running ? "on" : "off")}>{running ? "running" : "stopped"}</span>
          {state.uptime != null && state.uptime !== "" && <span className="meta">{state.uptime}</span>}
        </div>
        <div className="status-group">
          <span className="meta">{state.transcripts_count || 0} msgs</span>
        </div>
      </div>
      <div className="status-row">
        <div className="status-group">
          <span className="meta">mode <strong>{state.mode || "?"}</strong></span>
          <span className="meta">provider{" "}
            <select className="compact-select" value={state.provider || ""} onChange={e => onProviderChange(e.target.value)}>
              {(state.providers || []).map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </span>
          <span className="meta">model{" "}
            <select className="compact-select" value={state.model || ""} onChange={e => onModelChange(e.target.value)}>
              {(state.models || []).map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </span>
        </div>
        <div className="status-group">
          {state.pid != null && <span className="meta">pid <strong>{state.pid}</strong></span>}
          <span className="meta">chrome <strong>{state.chrome ? "connected" : "off"}</strong></span>
          {state.version && <span className="meta">{state.version}</span>}
          <span className="meta">interrupt <strong>{interruptEnabled ? "on" : "off"}</strong></span>
        </div>
      </div>
    </div>
  );
}
