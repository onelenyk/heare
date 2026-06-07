import React from 'react';

export default function InjectPanel({ value, onChange, onSend }) {
  function handleKeyDown(e) {
    if (e.key === "Enter") onSend();
  }

  return (
    <div className="card">
      <div className="card-header">
        {'\ud83d\udcac'} text injection <span style={{fontWeight: 400, color: "var(--muted)", fontSize: 9}}>{'\u2014'} simulates voice input</span>
      </div>
      <div className="controls-group">
        <input
          type="text" value={value}
          onInput={e => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="type text and press enter\u2026"
          className="mono-input"
          style={{flex: 1}}
        />
        <button className="btn" onClick={onSend}>send</button>
      </div>
    </div>
  );
}
