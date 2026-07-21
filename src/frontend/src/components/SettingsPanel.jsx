import React, { useRef, useState, useEffect } from 'react';
import { API } from '../App';

export default function SettingsPanel({ state, onSave, onToast }) {
  const groqRef = useRef(null);
  const deepseekRef = useRef(null);
  const passphraseRef = useRef(null);

  const [passStatus, setPassStatus] = useState('');
  const [resetArmed, setResetArmed] = useState(false);
  const [resetStatus, setResetStatus] = useState('');
  const armTimer = useRef(null);

  useEffect(() => () => clearTimeout(armTimer.current), []);

  function handleSave() {
    const g = groqRef.current ? groqRef.current.value : '';
    const d = deepseekRef.current ? deepseekRef.current.value : '';
    onSave(g, d);
  }

  async function handleSavePassphrase() {
    const word = passphraseRef.current ? passphraseRef.current.value.trim() : '';
    if (!word) return;
    setPassStatus('saving…');
    try {
      const r = await fetch(API + '/api/settings/passphrase', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passphrase: word }),
      });
      const d = await r.json();
      if (d.ok) {
        setPassStatus('saved — restart daemon to apply');
        passphraseRef.current.value = '';
        if (onToast) onToast('passphrase saved', 'ok');
      } else {
        setPassStatus(d.error || 'save failed');
      }
    } catch (e) {
      setPassStatus('save failed: ' + e.message);
    }
  }

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
        <div>
          <label>
            Groq API Key {state.key_groq_api_key && <span style={{color: "var(--accent)"}}>{'✓'}</span>}
          </label>
          <input ref={groqRef} id="set-groq" type="password" placeholder="gsk_..." />
        </div>
        <div>
          <label>
            DeepSeek API Key {state.key_deepseek_api_key && <span style={{color: "var(--accent)"}}>{'✓'}</span>}
          </label>
          <input ref={deepseekRef} id="set-deepseek" type="password" placeholder="sk-..." />
        </div>

        <div className="settings-panel-section">
          <div className="settings-panel-section-title">confirmation passphrase</div>
          <label>Say this word instead of "yes" to skip the confirm prompt</label>
          <input ref={passphraseRef} type="text" placeholder="e.g. авторизую" />
          <div style={{display: "flex", gap: 8, marginTop: 8, alignItems: "center"}}>
            <button className="btn" onClick={handleSavePassphrase}>Save Passphrase</button>
            {passStatus && <span className="settings-panel-hint">{passStatus}</span>}
          </div>
        </div>

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
      <div style={{padding: "12px 0 0"}}>
        <button className="btn primary" onClick={handleSave} style={{width: "100%", fontSize: 12}}>
          Save & Apply
        </button>
      </div>
    </div>
  );
}
