import React, { useRef, useState, useEffect } from 'react';
import { API } from '../App';

// Keys are not here. They live in one card (KeysCard), which is also what
// the first-run modal shows — the same key used to be typeable into three
// different boxes on this page alone.
export default function SettingsPanel({ state, onToast }) {
  const [resetArmed, setResetArmed] = useState(false);
  const [resetStatus, setResetStatus] = useState('');
  const armTimer = useRef(null);

  useEffect(() => () => clearTimeout(armTimer.current), []);

  async function handleResetSession() {
    if (!resetArmed) {
      setResetArmed(true);
      setResetStatus('');
      armTimer.current = setTimeout(() => setResetArmed(false), 6000);
      return;
    }
    clearTimeout(armTimer.current);
    setResetArmed(false);
    try {
      const r = await fetch(API + '/api/settings/reset-session', { method: 'POST' });
      const d = await r.json();
      if (d.ok) {
        setResetStatus('backed up — restart daemon to apply');
        if (onToast) onToast('session reset', 'ok');
      } else {
        setResetStatus(d.error || 'nothing to reset');
      }
    } catch (e) {
      setResetStatus('reset failed: ' + e.message);
    }
  }

  return (
    <div className="card">
      <div className="card-header">
        {'⚙'} settings
      </div>
      <div className="settings-panel">
        <div className="settings-panel-section">
          <div className="settings-panel-section-title">session</div>
          <label>Backs up the current Claude Code session and starts fresh</label>
          <div style={{display: "flex", gap: 8, alignItems: "center"}}>
            <button className={"btn" + (resetArmed ? " danger" : "")} onClick={handleResetSession}>
              {resetArmed ? 'confirm reset?' : 'Reset Session'}
            </button>
            {resetStatus && <span className="settings-panel-hint">{resetStatus}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
