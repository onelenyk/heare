import React, { useState, useEffect, useCallback, useRef } from 'react';
import { API } from '../App';

export default function BrainCard({ state, post, onClose }) {
  const [providers, setProviders] = useState([]);
  const [provider, setProvider] = useState(state.provider || 'deepseek');
  const [models, setModels] = useState([]);
  const [modelsSource, setModelsSource] = useState('fallback');
  const [modelsLoading, setModelsLoading] = useState(false);
  const [model, setModel] = useState('');
  const [status, setStatus] = useState('');
  const tokenRef = useRef(null);
  const stateRef = useRef(state);
  useEffect(() => { stateRef.current = state; }, [state]);

  const loadProviders = useCallback(async () => {
    try {
      const r = await fetch(API + '/api/providers');
      const d = await r.json();
      setProviders(d);
    } catch (e) {
      setStatus('Failed to load providers');
    }
  }, []);

  const loadModels = useCallback(async (providerKey) => {
    setModelsLoading(true);
    try {
      const r = await fetch(API + '/api/models?provider=' + encodeURIComponent(providerKey));
      const d = await r.json();
      if (d.ok) {
        setModels(d.models || []);
        setModelsSource(d.source || 'fallback');
        const stateModel = stateRef.current['model_' + providerKey];
        setModel(stateModel || (d.models && d.models[0]) || '');
      }
    } catch (e) {
      setStatus('Failed to load models');
    } finally {
      setModelsLoading(false);
    }
  }, []);

  useEffect(() => { loadProviders(); }, [loadProviders]);
  useEffect(() => { loadModels(provider); }, [provider, loadModels]);

  const cfg = providers.find(p => p.key === provider);

  const handleSaveToken = async () => {
    const token = tokenRef.current ? tokenRef.current.value.trim() : '';
    if (!token || !cfg) return;
    setStatus('Saving...');
    try {
      const r = await fetch(API + '/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [cfg.key + '_api_key']: token }),
      });
      const d = await r.json();
      if (d.ok) {
        setStatus('Token saved & applied');
        tokenRef.current.value = '';
        await loadProviders();
        await loadModels(provider);
      } else {
        setStatus((d.errors && d.errors[0]) || 'Save failed');
      }
    } catch (e) {
      setStatus('Save failed: ' + e.message);
    }
  };

  const handleActivate = () => {
    post('/provider', { provider });
    setStatus(`Activated ${cfg ? cfg.display_name : provider}`);
  };

  const handleModelChange = (e) => {
    const next = e.target.value;
    setModel(next);
    post('/model', { provider, model: next });
  };

  const selectStyle = {
    width: '100%',
    padding: '8px 10px',
    background: 'var(--bg)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--r-xs)',
    color: 'var(--text)',
    fontSize: 13,
  };

  return (
    <div className="card">
      <div className="card-header">
        {'🧠'} brain
        <button className="modal-close" onClick={onClose} style={{ marginLeft: 'auto' }}>
          {'×'}
        </button>
      </div>

      <div style={{ padding: 'var(--s2) var(--s3)' }}>
        <div style={{ marginBottom: 12 }}>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
            Provider {state.provider === provider && <span style={{ color: 'var(--accent)' }}>{'●'} active</span>}
          </label>
          <select value={provider} onChange={e => setProvider(e.target.value)} style={selectStyle}>
            {providers.map(p => (
              <option key={p.key} value={p.key}>
                {p.display_name} {p.configured ? '✓' : ''}
              </option>
            ))}
          </select>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
            API Token {cfg && cfg.configured && <span style={{ color: 'var(--accent)' }}>{'✓'} configured</span>}
          </label>
          <input
            ref={tokenRef}
            type="password"
            placeholder={cfg && cfg.configured ? '✓ configured — paste to replace' : 'paste token...'}
            style={selectStyle}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
            <button className="btn" onClick={handleSaveToken} style={{ flex: 1, fontSize: 12 }}>
              Save Token
            </button>
            <button
              className="btn primary"
              onClick={handleActivate}
              disabled={!cfg || !cfg.configured}
              style={{ flex: 1, fontSize: 12 }}
            >
              Activate
            </button>
          </div>
        </div>

        <div style={{ marginBottom: 4 }}>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
            Model {modelsLoading && <span style={{ color: 'var(--muted)' }}>loading...</span>}
          </label>
          <select value={model} onChange={handleModelChange} style={selectStyle} disabled={modelsLoading || models.length === 0}>
            {models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          {modelsSource === 'fallback' && !modelsLoading && (
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
              {'ℹ️'} using built-in list (no live token, or provider unreachable)
            </div>
          )}
        </div>

        {status && (
          <div style={{ fontSize: 11, color: status.includes('fail') || status.includes('Fail') ? 'var(--accent-red)' : 'var(--accent)', marginTop: 8 }}>
            {status}
          </div>
        )}
      </div>
    </div>
  );
}
