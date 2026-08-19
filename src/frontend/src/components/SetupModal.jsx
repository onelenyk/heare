import React, { useState, useEffect } from 'react';
import { API } from '../App';
import KeysCard from './KeysCard';

export default function SetupModal({ show, onClose, onComplete }) {
  const [tab, setTab] = useState('persona');
  const [identity, setIdentity] = useState(null);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');

  const [name, setName] = useState('');
  const [emoji, setEmoji] = useState('');
  const [vibe, setVibe] = useState('');
  const [tagline, setTagline] = useState('');
  const [creature, setCreature] = useState('');
  // 'uk' by default: this assistant is spoken to in Ukrainian, and an
  // 'en' default meant a fresh install transcribed Ukrainian as English.
  const [language, setLanguage] = useState('uk');

  useEffect(() => {
    if (show) loadState();
  }, [show]);

  const loadState = async () => {
    try {
      const r = await fetch(API + '/api/setup');
      const d = await r.json();
      if (d.identity) {
        setIdentity(d.identity);
        setName(d.identity.name || '');
        setEmoji(d.identity.emoji || '');
        setVibe(d.identity.vibe || '');
        setTagline(d.identity.tagline || '');
        setCreature(d.identity.creature || '');
      }
      if (d.config) setLanguage(d.config.language || 'uk');
    } catch (e) {
      setStatus('Failed to load setup state');
    }
  };

  const handleSaveIdentity = async () => {
    setLoading(true);
    setStatus('Saving...');
    try {
      const body = { name, emoji, vibe, tagline, creature, generated_at: identity?.generated_at || new Date().toISOString() };
      const r = await fetch(API + '/api/setup/identity', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      });
      const d = await r.json();
      if (d.ok) { setStatus('Persona saved! Restart daemon to apply.'); setIdentity(d.identity); }
      else setStatus(d.error || 'Save failed');
    } catch (e) { setStatus('Save failed: ' + e.message); }
    setLoading(false);
  };

  const handleRegenerate = async () => {
    setLoading(true);
    setStatus('Regenerating...');
    try {
      const r = await fetch(API + '/api/setup/identity/regenerate', { method: 'POST' });
      const d = await r.json();
      if (d.ok) {
        setIdentity(d.identity);
        setName(d.identity.name || '');
        setEmoji(d.identity.emoji || '');
        setVibe(d.identity.vibe || '');
        setTagline(d.identity.tagline || '');
        setCreature(d.identity.creature || '');
        setStatus('New persona generated! Save or edit then save.');
      } else setStatus(d.error || 'Regeneration failed');
    } catch (e) { setStatus('Regeneration failed: ' + e.message); }
    setLoading(false);
  };

  const handleSaveConfig = async () => {
    setLoading(true);
    setStatus('Saving config...');
    try {
      const r = await fetch(API + '/api/setup/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language })
      });
      const d = await r.json();
      if (d.ok) setStatus('Config saved!');
      else setStatus(d.error || 'Save failed');
    } catch (e) { setStatus('Save failed: ' + e.message); }
    setLoading(false);
  };

  const handleDone = async () => {
    await fetch(API + '/api/setup/complete', { method: 'POST' });
    if (onComplete) onComplete();
    if (onClose) onClose();
  };

  if (!show) return null;

  return (
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card" style={{maxWidth: 560}}>
        <div className="modal-header">
          <span className="modal-title">{'\ud83e\udde0'} Agent Setup</span>
          <button className="modal-close" onClick={onClose}>&times;</button>
        </div>
        <div className="modal-body" style={{maxHeight: '70vh', overflowY: 'auto'}}>

          <div style={{display: 'flex', gap: 4, marginBottom: 16, borderBottom: '1px solid var(--border)', paddingBottom: 8}}>
            {['persona', 'config', 'keys'].map(t => (
              <button key={t} className={"tab-btn" + (tab === t ? ' active' : '')} onClick={() => setTab(t)}
                style={{textTransform: 'capitalize'}}>{t}</button>
            ))}
          </div>

          {tab === 'persona' && (
            <div>
              <div style={{marginBottom: 12}}>
                <label style={{display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4}}>Name</label>
                <input value={name} onChange={e => setName(e.target.value)}
                  style={{width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--r-xs)', color: 'var(--text)', fontSize: 14}} />
              </div>
              <div style={{display: 'flex', gap: 8, marginBottom: 12}}>
                <div style={{flex: '0 0 60px'}}>
                  <label style={{display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4}}>Emoji</label>
                  <input value={emoji} onChange={e => setEmoji(e.target.value)}
                    style={{width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--r-xs)', color: 'var(--text)', fontSize: 18, textAlign: 'center'}} />
                </div>
                <div style={{flex: 1}}>
                  <label style={{display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4}}>Vibe</label>
                  <input value={vibe} onChange={e => setVibe(e.target.value)}
                    style={{width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--r-xs)', color: 'var(--text)', fontSize: 13}} />
                </div>
              </div>
              <div style={{marginBottom: 12}}>
                <label style={{display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4}}>Tagline</label>
                <input value={tagline} onChange={e => setTagline(e.target.value)}
                  style={{width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--r-xs)', color: 'var(--text)', fontSize: 13}} />
              </div>
              <div style={{marginBottom: 12}}>
                <label style={{display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4}}>Description</label>
                <textarea value={creature} onChange={e => setCreature(e.target.value)} rows={3}
                  style={{width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--r-xs)', color: 'var(--text)', fontSize: 13, resize: 'vertical', fontFamily: 'var(--sans)'}} />
              </div>
              <div style={{display: 'flex', gap: 8, marginBottom: 8}}>
                <button className="btn primary" onClick={handleSaveIdentity} disabled={loading} style={{flex: 1}}>
                  {loading ? 'Saving...' : '\ud83d\udcbe Save Persona'}
                </button>
                <button className="btn" onClick={handleRegenerate} disabled={loading}>
                  {'\ud83d\udd04'} Regenerate
                </button>
              </div>
              {status && <div style={{fontSize: 11, color: status.includes('fail') || status.includes('error') ? 'var(--accent-red)' : 'var(--accent)', marginTop: 4}}>{status}</div>}
              <div style={{fontSize: 10, color: 'var(--muted)', marginTop: 8}}>
                Regenerate asks the LLM to invent a new persona. Save keeps your edits. Restart daemon to apply changes.
              </div>
            </div>
          )}

          {tab === 'config' && (
            <div>
              <div style={{marginBottom: 12}}>
                <label style={{display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4}}>Language</label>
                <select value={language} onChange={e => setLanguage(e.target.value)}
                  style={{width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--r-xs)', color: 'var(--text)', fontSize: 13}}>
                  <option value="en">English</option>
                  <option value="uk">Ukrainian</option>
                  <option value="ru">Russian</option>
                </select>
              </div>
              {/* The TTS voice picker used to live here. It is gone, not
                  hidden: the voice engine picks a voice per reply from the
                  script the reply is written in (src/spine/voicing.py), so
                  a chosen voice was never read and a Ukrainian sentence on
                  an English voice is silence. */}
              <div style={{marginBottom: 12, fontSize: 11, color: 'var(--muted)', lineHeight: 1.5}}>
                Voice follows the language of each reply {'\u2014'} nothing to choose.
              </div>
              <button className="btn primary" onClick={handleSaveConfig} disabled={loading} style={{width: '100%'}}>
                {loading ? 'Saving...' : '\ud83d\udcbe Save Config'}
              </button>
              {status && <div style={{fontSize: 11, color: status.includes('fail') || status.includes('error') ? 'var(--accent-red)' : 'var(--accent)', marginTop: 8}}>{status}</div>}
            </div>
          )}

          {tab === 'keys' && <KeysCard bare />}

        </div>
        <div style={{display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 16px', borderTop: '1px solid var(--border)'}}>
          <button className="btn" onClick={onClose}>Close</button>
          <button className="btn primary" onClick={handleDone}>{'\u2713'} Done</button>
        </div>
      </div>
    </div>
  );
}
