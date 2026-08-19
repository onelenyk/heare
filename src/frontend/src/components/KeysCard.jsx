import React, { useState, useEffect, useCallback } from 'react';
import { API } from '../App';

// The one place a key is typed.
//
// There were five: a settings panel with Groq and DeepSeek, a brain card
// with a token box per provider, a setup modal with a third copy, plus two
// components no route ever rendered. The same DeepSeek key could be pasted
// into three different boxes, and each posted a slightly different body.
//
// One card, driven by the provider registry, rendered both in the dashboard
// and inside the first-run modal. Adding a provider adds a row here and
// nothing else.

const field = {
  width: '100%',
  padding: '8px 10px',
  background: 'var(--bg)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--r-xs)',
  color: 'var(--text)',
  fontSize: 13,
};

const label = {
  display: 'block',
  fontSize: 11,
  color: 'var(--muted)',
  marginBottom: 4,
};

export default function KeysCard({ state = {}, post, onClose, bare = false }) {
  const [providers, setProviders] = useState([]);
  const [groqConfigured, setGroqConfigured] = useState(false);
  const [typed, setTyped] = useState({});
  const [active, setActive] = useState(state.provider || 'deepseek');
  const [models, setModels] = useState([]);
  const [model, setModel] = useState('');
  const [status, setStatus] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(API + '/api/providers');
      setProviders(await r.json());
    } catch (e) {
      setStatus('could not read the provider list');
    }
    try {
      const r = await fetch(API + '/api/setup');
      const d = await r.json();
      setGroqConfigured(Boolean(d.config && d.config.groq_key_configured));
    } catch (e) {
      /* the key state is a nicety; the fields still work without it */
    }
  }, []);

  const loadModels = useCallback(async (providerKey) => {
    try {
      const r = await fetch(API + '/api/models?provider=' + encodeURIComponent(providerKey));
      const d = await r.json();
      if (d.ok) {
        setModels(d.models || []);
        setModel(state['model_' + providerKey] || (d.models && d.models[0]) || '');
      }
    } catch (e) {
      setModels([]);
    }
  }, [state]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadModels(active); }, [active, loadModels]);

  // Keys are collected and sent together: one save, one round trip, one
  // place that can report what went wrong.
  const save = async () => {
    const body = {};
    for (const [name, value] of Object.entries(typed)) {
      if (value && value.trim()) body[name] = value.trim();
    }
    if (!Object.keys(body).length) { setStatus('nothing typed'); return; }
    setSaving(true);
    setStatus('saving…');
    try {
      const r = await fetch(API + '/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (d.ok) {
        setStatus('saved — in effect from the next thing you say');
        setTyped({});
        await load();
      } else {
        setStatus((d.errors && d.errors[0]) || d.error || 'save failed');
      }
    } catch (e) {
      setStatus('save failed: ' + e.message);
    }
    setSaving(false);
  };

  const choose = (providerKey) => {
    setActive(providerKey);
    if (post) post('/provider', { provider: providerKey });
  };

  const chooseModel = (e) => {
    setModel(e.target.value);
    if (post) post('/model', { provider: active, model: e.target.value });
  };

  const set = (name, value) => setTyped(prev => ({ ...prev, [name]: value }));

  const body = (
    <div style={{ padding: bare ? 0 : 'var(--s2) var(--s3)' }}>

      <div style={{ marginBottom: 14 }}>
        <label style={label}>
          Groq {'—'} hearing {groqConfigured && <span style={{ color: 'var(--accent)' }}>{'✓'}</span>}
        </label>
        <input
          type="password"
          value={typed.groq_api_key || ''}
          onChange={e => set('groq_api_key', e.target.value)}
          placeholder={groqConfigured ? '✓ set — paste to replace' : 'gsk_...'}
          style={field}
        />
      </div>

      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
        Speaking {'—'} pick which one answers
      </div>

      {providers.map(p => (
        <div key={p.key} style={{ marginBottom: 12 }}>
          <label style={{ ...label, display: 'flex', alignItems: 'center', gap: 6 }}>
            <input
              type="radio"
              name="active-provider"
              checked={active === p.key}
              onChange={() => choose(p.key)}
              disabled={!p.configured}
              style={{ margin: 0 }}
            />
            {p.display_name}
            {p.configured
              ? <span style={{ color: 'var(--accent)' }}>{'✓'}</span>
              : <span style={{ color: 'var(--muted)' }}>{'—'} no key</span>}
          </label>
          <input
            type="password"
            value={typed[p.key + '_api_key'] || ''}
            onChange={e => set(p.key + '_api_key', e.target.value)}
            placeholder={p.configured ? '✓ set — paste to replace' : 'sk-...'}
            style={field}
          />
          {active === p.key && models.length > 0 && (
            <select value={model} onChange={chooseModel} style={{ ...field, marginTop: 6 }}>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          )}
        </div>
      ))}

      <button className="btn primary" onClick={save} disabled={saving} style={{ width: '100%' }}>
        {saving ? 'saving…' : 'Save keys'}
      </button>

      {status && (
        <div style={{
          fontSize: 11,
          marginTop: 8,
          color: status.includes('fail') || status.includes('could not') ? 'var(--accent-red)' : 'var(--accent)',
        }}>{status}</div>
      )}
    </div>
  );

  if (bare) return body;

  return (
    <div className="card">
      <div className="card-header">
        {'🔑'} keys
        {onClose && (
          <button className="modal-close" onClick={onClose} style={{ marginLeft: 'auto' }}>
            {'×'}
          </button>
        )}
      </div>
      {body}
    </div>
  );
}
