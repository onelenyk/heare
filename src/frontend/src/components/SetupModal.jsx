import React, { useState, useEffect } from 'react';
import { API } from '../App';

export default function SetupModal({ show, onClose, onComplete }) {
  const [tab, setTab] = useState('persona');
  const [identity, setIdentity] = useState(null);
  const [config, setConfig] = useState({});
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');

  const [name, setName] = useState('');
  const [emoji, setEmoji] = useState('');
  const [vibe, setVibe] = useState('');
  const [tagline, setTagline] = useState('');
  const [creature, setCreature] = useState('');
  const [language, setLanguage] = useState('en');
  const [voice, setVoice] = useState('en-US-AriaNeural');
  const [groqKey, setGroqKey] = useState('');
  const [llmKey, setLlmKey] = useState('');
  const [provider, setProvider] = useState('deepseek');
  const [providers, setProviders] = useState([]);

  useEffect(() => {
    if (show) loadState();
  }, [show]);

  const loadState = async () => {
    // Provider list comes from the registry, not a hardcoded list here, so
    // this stays in step with the brain card and with the backend.
    try {
      const pr = await fetch(API + '/api/providers');
      setProviders(await pr.json());
    } catch (e) {
      setProviders([]);
    }
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
      if (d.config) {
        setConfig(d.config);
        setLanguage(d.config.language || 'en');
        setVoice(d.config.tts_voice || 'en-US-AriaNeural');
      }
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
        body: JSON.stringify({ language, tts_voice: voice })
      });
      const d = await r.json();
      if (d.ok) setStatus('Config saved!');
      else setStatus(d.error || 'Save failed');
    } catch (e) { setStatus('Save failed: ' + e.message); }
    setLoading(false);
  };

  const handleSaveKeys = async () => {
    setLoading(true);
    setStatus('Saving keys...');
    try {
      const body = {};
      if (groqKey) body.groq_api_key = groqKey;
      if (llmKey) body[provider + '_api_key'] = llmKey;
      const r = await fetch(API + '/api/setup/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      });
      const d = await r.json();
      if (d.ok) {
        setStatus('Keys saved & applied');
        setGroqKey('');
        setLlmKey('');
        await loadState();
      } else setStatus((d.errors && d.errors[0]) || d.error || 'Save failed');
    } catch (e) { setStatus('Save failed: ' + e.message); }
    setLoading(false);
  };

  const handleDone = async () => {
    await fetch(API + '/api/setup/complete', { method: 'POST' });
    if (onComplete) onComplete();
    if (onClose) onClose();
  };

  if (!show) return null;

  const voices = [
    { id: 'en-US-AriaNeural', label: 'English (Aria)' },
    { id: 'uk-UA-OstapNeural', label: 'Ukrainian (Ostap)' },
    { id: 'uk-UA-PolinaNeural', label: 'Ukrainian (Polina)' },
    { id: 'ru-RU-SvetlanaNeural', label: 'Russian (Svetlana)' },
  ];

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
              <div style={{marginBottom: 12}}>
                <label style={{display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4}}>TTS Voice</label>
                <select value={voice} onChange={e => setVoice(e.target.value)}
                  style={{width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--r-xs)', color: 'var(--text)', fontSize: 13}}>
                  {voices.map(v => <option key={v.id} value={v.id}>{v.label}</option>)}
                </select>
              </div>
              <button className="btn primary" onClick={handleSaveConfig} disabled={loading} style={{width: '100%'}}>
                {loading ? 'Saving...' : '\ud83d\udcbe Save Config'}
              </button>
              {status && <div style={{fontSize: 11, color: status.includes('fail') || status.includes('error') ? 'var(--accent-red)' : 'var(--accent)', marginTop: 8}}>{status}</div>}
            </div>
          )}

          {tab === 'keys' && (
            <div>
              <div style={{marginBottom: 12}}>
                <label style={{display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4}}>Groq API Key (STT)</label>
                <input type="password" value={groqKey} onChange={e => setGroqKey(e.target.value)}
                  placeholder={config?.groq_key_configured ? '\u2713 configured' : 'gsk_...'}
                  style={{width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--r-xs)', color: 'var(--text)', fontSize: 13}} />
              </div>
              <div style={{marginBottom: 12}}>
                <label style={{display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4}}>Provider</label>
                <select value={provider} onChange={e => setProvider(e.target.value)}
                  style={{width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--r-xs)', color: 'var(--text)', fontSize: 13, marginBottom: 8}}>
                  {providers.map(p => (
                    <option key={p.key} value={p.key}>
                      {p.display_name}{p.configured ? ' \u2713' : ''}
                    </option>
                  ))}
                </select>
                <label style={{display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4}}>LLM API Key</label>
                <input type="password" value={llmKey} onChange={e => setLlmKey(e.target.value)}
                  placeholder={providers.find(p => p.key === provider)?.configured ? '\u2713 configured \u2014 paste to replace' : 'sk-...'}
                  style={{width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--r-xs)', color: 'var(--text)', fontSize: 13}} />
              </div>
              <button className="btn primary" onClick={handleSaveKeys} disabled={loading} style={{width: '100%'}}>
                {loading ? 'Saving...' : '\ud83d\udcbe Save Keys'}
              </button>
              {status && <div style={{fontSize: 11, color: status.includes('fail') || status.includes('error') ? 'var(--accent-red)' : 'var(--accent)', marginTop: 8}}>{status}</div>}
            </div>
          )}

        </div>
        <div style={{display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 16px', borderTop: '1px solid var(--border)'}}>
          <button className="btn" onClick={onClose}>Close</button>
          <button className="btn primary" onClick={handleDone}>{'\u2713'} Done</button>
        </div>
      </div>
    </div>
  );
}
