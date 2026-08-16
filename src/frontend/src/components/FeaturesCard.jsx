import React, { useState, useEffect } from 'react';
import { API } from '../App';

// The subsystem switches — and the one thing about them that must not be
// hidden: a subsystem is wired at build time, by the composition root. A
// toggle here changes what gets wired on the NEXT start; it cannot switch
// off something already running. So the card shows two values at once,
// what is running and what is configured, and offers the restart that
// makes them agree.

function safeParse(json, fallback) {
  if (!json) return fallback;
  try {
    const d = JSON.parse(json);
    return d == null ? fallback : d;
  } catch (e) {
    return fallback;
  }
}

export default function FeaturesCard({ state, post, onToast }) {
  // The live half rides along on the /state poll the dashboard already
  // does — no second request, and it refreshes once a second.
  const liveFromState = safeParse(state.spine_features, null);
  const [fetched, setFetched] = useState(null); // { live, config, defaults }
  const [busy, setBusy] = useState('');

  async function loadFeatures() {
    try {
      const r = await fetch(API + '/api/features');
      const d = await r.json();
      setFetched(d && d.ok ? d : { live: null, config: {}, defaults: {} });
    } catch (e) {
      setFetched({ live: null, config: {}, defaults: {} });
      if (onToast) onToast('не вдалося прочитати підсистеми: ' + e.message, 'err');
    }
  }

  useEffect(() => {
    loadFeatures();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const live = Array.isArray(liveFromState)
    ? liveFromState
    : (fetched && Array.isArray(fetched.live) ? fetched.live : null);
  const config = (fetched && fetched.config) || {};
  const defaults = (fetched && fetched.defaults) || {};

  // ── State 3: the running engine says nothing about subsystems ──
  // (the pipecat engine, or a daemon older than the state key). Guessing
  // from config.toml here would be a lie about what is running.
  if (!live) {
    return (
      <div className="card">
        <div className="card-header">{'🧩'} Підсистеми</div>
        <div className="info-line">
          поточний двигун не повідомляє про підсистеми
        </div>
      </div>
    );
  }

  const configuredFor = (name, liveOn) => {
    if (typeof config[name] === 'boolean') return config[name];
    if (typeof defaults[name] === 'boolean') return defaults[name];
    return liveOn;
  };

  const rows = live.map((f) => {
    const name = f && f.name ? String(f.name) : '';
    const on = f && f.on === true;
    const want = configuredFor(name, on);
    return { name, on, want, cost: (f && f.cost) || '', differs: want !== on };
  });
  const anyDiffers = rows.some((r) => r.differs);

  async function handleToggle(row) {
    if (busy) return;
    setBusy(row.name);
    try {
      const r = await fetch(API + '/api/features', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: row.name, enabled: !row.want }),
      });
      const d = await r.json();
      if (d.ok) {
        setFetched((prev) => ({ ...(prev || {}), config: d.config || {} }));
        if (onToast) {
          onToast(
            row.name + ': ' + (!row.want ? 'увімкнено' : 'вимкнено') +
              ' — з наступного запуску',
            'ok'
          );
        }
      } else if (onToast) {
        onToast('не вдалося: ' + (d.error || 'unknown'), 'err');
      }
    } catch (e) {
      if (onToast) onToast('не вдалося: ' + e.message, 'err');
    } finally {
      setBusy('');
    }
  }

  async function handleRestart() {
    try {
      await post('/daemon', { action: 'restart' });
      if (onToast) onToast('перезапускаю…', 'info');
    } catch (e) {
      if (onToast) onToast('рестарт не вдався: ' + e.message, 'err');
    }
  }

  return (
    <div className="card">
      <div className="card-header">
        {'🧩'} Підсистеми
        {anyDiffers && (
          <button
            className="btn primary feature-restart"
            onClick={handleRestart}
            style={{ marginLeft: 'auto' }}
          >
            {'↻'} Перезапустити
          </button>
        )}
      </div>

      <div className="info-line feature-note">
        вмикається при старті: перемикач діє з наступного запуску
      </div>

      <div className="feature-list">
        {rows.map((row) => (
          <div
            className={'feature-row' + (row.differs ? ' pending' : '')}
            key={row.name}
          >
            <button
              className="feature-toggle"
              role="switch"
              aria-checked={row.want}
              aria-label={row.name}
              disabled={busy === row.name}
              onClick={() => handleToggle(row)}
            >
              <span className={'bridge-toggle-track' + (row.want ? ' on' : '')}></span>
              <span className={'bridge-toggle-thumb' + (row.want ? ' on' : '')}></span>
            </button>
            <div className="feature-body">
              <div className="feature-title">
                <span className="feature-name">{row.name}</span>
                <span className={'status-badge ' + (row.on ? 'on' : 'off')}>
                  {row.on ? 'працює' : 'вимкнено'}
                </span>
                {row.differs && (
                  <span className="feature-pending">потрібен рестарт</span>
                )}
              </div>
              {/* What switching it off costs — the reason to think twice. */}
              <div className="feature-cost">{row.cost}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
