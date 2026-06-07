import React, { useRef } from 'react';

export default function SettingsPanel({ state, onSave }) {
  const groqRef = useRef(null);
  const deepseekRef = useRef(null);

  function handleSave() {
    const g = groqRef.current ? groqRef.current.value : '';
    const d = deepseekRef.current ? deepseekRef.current.value : '';
    onSave(g, d);
  }

  return (
    <div className="card">
      <div className="card-header">
        {'\u2699'} settings
      </div>
      <div className="settings-panel">
        <div>
          <label style={{fontSize: 11, color: "var(--muted)"}}>
            Groq API Key {state.key_groq_api_key && <span style={{color: "var(--accent)"}}>{'\u2713'}</span>}
          </label>
          <input ref={groqRef} id="set-groq" type="password" placeholder="gsk_..." />
        </div>
        <div>
          <label style={{fontSize: 11, color: "var(--muted)"}}>
            DeepSeek API Key {state.key_deepseek_api_key && <span style={{color: "var(--accent)"}}>{'\u2713'}</span>}
          </label>
          <input ref={deepseekRef} id="set-deepseek" type="password" placeholder="sk-..." />
        </div>
      </div>
      <div style={{padding: "0 8px 8px"}}>
        <button className="btn primary" onClick={handleSave} style={{width: "100%", fontSize: 12}}>
          Save & Apply
        </button>
      </div>
    </div>
  );
}
