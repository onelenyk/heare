import React, { useState, useEffect, useRef } from 'react';
import { API } from '../App';

const CHANNEL_ICON = { voice: '🎙', log: '📝', hints: '💡' };

function safeParse(json, fallback) {
  if (!json) return fallback;
  try {
    const d = JSON.parse(json);
    return d == null ? fallback : d;
  } catch (e) {
    return fallback;
  }
}

function fmtElapsed(sinceStr) {
  const since = Number(sinceStr);
  if (!since || Number.isNaN(since)) return '';
  const mins = Math.max(0, Math.floor((Date.now() / 1000 - since) / 60));
  return mins + ' min';
}

function fmtDate(mtime) {
  if (!mtime) return '';
  const d = new Date(mtime * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function RolesCard({ state, post, onToast }) {
  const roleActive = state.role_active || '';
  const roleChannel = state.role_channel || '';
  const roleSince = state.role_since || '';
  const rolesAvailable = safeParse(state.roles_available, []);
  const roleHint = safeParse(state.role_hint, null);

  const [artifacts, setArtifacts] = useState([]);
  const [openArtifact, setOpenArtifact] = useState(null); // name of expanded artifact
  const [artifactContent, setArtifactContent] = useState('');
  const [artifactLoading, setArtifactLoading] = useState(false);
  const prevRoleActive = useRef(roleActive);

  const fetchArtifacts = async () => {
    try {
      const r = await fetch(API + '/api/artifacts');
      const d = await r.json();
      setArtifacts(Array.isArray(d.artifacts) ? d.artifacts.slice(0, 8) : []);
    } catch (e) {
      setArtifacts([]);
      if (onToast) onToast('failed to load artifacts: ' + e.message, 'err');
    }
  };

  // Fetch once on mount, and again whenever a role ends (active -> "").
  useEffect(() => {
    fetchArtifacts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (prevRoleActive.current && !roleActive) fetchArtifacts();
    prevRoleActive.current = roleActive;
  }, [roleActive]);

  async function handleStartRole(trigger) {
    if (!trigger) return;
    try {
      await post('/inject', { text: trigger });
    } catch (e) {
      if (onToast) onToast('failed to start role: ' + e.message, 'err');
    }
  }

  async function handleEndRole() {
    try {
      await post('/inject', { text: 'закінчили' });
    } catch (e) {
      if (onToast) onToast('failed to end role: ' + e.message, 'err');
    }
  }

  async function handleArtifactClick(name) {
    if (openArtifact === name) {
      setOpenArtifact(null);
      setArtifactContent('');
      return;
    }
    setOpenArtifact(name);
    setArtifactContent('');
    setArtifactLoading(true);
    try {
      const r = await fetch(API + '/api/artifacts/' + encodeURIComponent(name));
      const text = await r.text();
      setArtifactContent(text);
    } catch (e) {
      setArtifactContent('(failed to load: ' + e.message + ')');
    } finally {
      setArtifactLoading(false);
    }
  }

  return (
    <div className="card">
      <div className="card-header">{'🎞'} Ролі</div>

      {!roleActive && (
        <div className="btn-row">
          {rolesAvailable.length === 0 && (
            <div className="info-line">no roles available</div>
          )}
          {rolesAvailable.map((r, i) => (
            <button
              key={r.name || i}
              className="btn"
              title={r.trigger}
              onClick={() => handleStartRole(r.trigger)}
            >
              {r.name}
            </button>
          ))}
        </div>
      )}

      {roleActive && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s2)', marginBottom: 'var(--s3)' }}>
            <span className="status-badge on">
              {CHANNEL_ICON[roleChannel] || ''} {roleActive}
            </span>
            <span className="meta">{fmtElapsed(roleSince)}</span>
            <button
              className="btn danger"
              style={{ marginLeft: 'auto' }}
              onClick={handleEndRole}
            >
              {'Закінчити'}
            </button>
          </div>

          {roleChannel === 'hints' && roleHint && roleHint.text && (
            <div
              className="card"
              style={{
                background: 'var(--card-2)',
                borderColor: 'var(--accent)',
                fontSize: 'var(--fs-lg)',
                lineHeight: 1.5,
                padding: 'var(--s4)',
              }}
            >
              {roleHint.text}
            </div>
          )}
        </div>
      )}

      {/* Artifacts */}
      <div style={{ marginTop: 'var(--s3)' }}>
        <div className="bar-section-label">artifacts</div>
        {artifacts.length === 0 && <div className="info-line">no artifacts yet</div>}
        {artifacts.map((a) => (
          <div key={a.name}>
            <div
              className="memory-row"
              style={{ cursor: 'pointer' }}
              onClick={() => handleArtifactClick(a.name)}
            >
              <span className="memory-content">{a.name}</span>
              <span className="memory-ts">{fmtDate(a.mtime)}</span>
            </div>
            {openArtifact === a.name && (
              <pre
                style={{
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  background: 'var(--card-2)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--r-xs)',
                  padding: 'var(--s3)',
                  marginBottom: 'var(--s2)',
                  maxHeight: 320,
                  overflow: 'auto',
                }}
              >
                {artifactLoading ? '…' : artifactContent}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
