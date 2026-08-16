import React, { useState, useEffect, useRef } from 'react';
import { API } from '../App';

const CHANNEL_ICON = { voice: '🎙', log: '📝', hints: '💡' };
const FINISHED_DISPLAY_MS = 30000;

function safeParse(json, fallback) {
  if (!json) return fallback;
  try {
    const d = JSON.parse(json);
    return d == null ? fallback : d;
  } catch (e) {
    return fallback;
  }
}

function elapsedSeconds(sinceStr) {
  const since = Number(sinceStr);
  if (!since || Number.isNaN(since)) return 0;
  return Math.max(0, Math.floor(Date.now() / 1000 - since));
}
function elapsedMinutes(sinceStr) {
  return Math.floor(elapsedSeconds(sinceStr) / 60);
}

function agoSeconds(ts) {
  const t = Number(ts);
  if (!t || Number.isNaN(t)) return null;
  return Math.max(0, Math.floor(Date.now() / 1000 - t));
}

function truncate(str, n) {
  if (!str) return '';
  return str.length > n ? str.slice(0, n).trimEnd() + '…' : str;
}

function fmtDate(mtime) {
  if (!mtime) return '';
  const d = new Date(mtime * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Splits a hint blob into clean bullet lines: drop leading "-", "•", "▸"
// markers and blank lines, since the source text mixes conventions.
function hintLines(text) {
  if (!text) return [];
  return text
    .split('\n')
    .map((l) => l.replace(/^\s*[-•▸]\s*/, '').trim())
    .filter(Boolean);
}

export default function RolesCard({ state, post, onToast }) {
  const roleActive = state.role_active || '';
  const roleFinishing = state.role_finishing === '1' || state.role_finishing === 1;
  const roleChannel = state.role_channel || '';
  const roleSince = state.role_since || '';
  const roleTurns = state.role_turns || '0';
  const roleLastHeard = state.role_last_heard || '';
  const rolesAvailable = safeParse(state.roles_available, []);
  const roleHint = safeParse(state.role_hint, null);

  // A meeting the daemon's restart cut off. The engine closes the session
  // row as 'interrupted' and writes this key at boot; until now nothing
  // read it, so the meeting vanished in silence and the strip showed
  // "idle" as if the last hour had not happened.
  const interrupted = safeParse(state.role_interrupted, null);

  const [finished, setFinished] = useState(null); // { name, minutes }
  // Dismissal is keyed on the event's ts so a *later* interruption comes
  // back even though this one was dismissed.
  const [dismissedTs, setDismissedTs] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [artifacts, setArtifacts] = useState([]);
  const [openArtifact, setOpenArtifact] = useState(null); // name of expanded artifact
  const [artifactContent, setArtifactContent] = useState('');
  const [artifactLoading, setArtifactLoading] = useState(false);

  const prevRoleRef = useRef({ name: roleActive, since: roleSince });
  const finishedTimerRef = useRef(null);

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

  useEffect(() => {
    fetchArtifacts();
    return () => {
      if (finishedTimerRef.current) clearTimeout(finishedTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Detect an active -> idle transition and surface the "just finished"
  // result card for a short window.
  useEffect(() => {
    const wasActive = prevRoleRef.current.name;
    if (wasActive && !roleActive) {
      const minutes = elapsedMinutes(prevRoleRef.current.since);
      setFinished({ name: wasActive, minutes });
      setHistoryOpen(false);
      setOpenArtifact(null);
      fetchArtifacts();
      if (finishedTimerRef.current) clearTimeout(finishedTimerRef.current);
      finishedTimerRef.current = setTimeout(() => setFinished(null), FINISHED_DISPLAY_MS);
    }
    if (roleActive) {
      if (finishedTimerRef.current) { clearTimeout(finishedTimerRef.current); finishedTimerRef.current = null; }
      setFinished(null);
    }
    prevRoleRef.current = { name: roleActive, since: roleSince };
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  async function handleDismissInterrupted() {
    // Hide first: the key is cleared server-side, but the next poll is up
    // to a second away and the click must feel like it did something.
    setDismissedTs(Number(interrupted && interrupted.ts) || 0);
    try {
      // No dedicated endpoint — the generic state write already exists and
      // clearing the key is all "dismiss" means.
      await post('/state', { key: 'role_interrupted', value: '' });
    } catch (e) {
      if (onToast) onToast('не вдалося прибрати банер: ' + e.message, 'err');
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

  // Shown above whatever the strip is doing now (idle, live role, or the
  // just-finished card) — a restart-killed meeting is news in every one of
  // those states, and the strip's own state says nothing about it.
  const showInterrupted =
    !!interrupted && (Number(interrupted.ts) || 0) !== dismissedTs;
  const interruptedBanner = showInterrupted ? (
    <div className="roles-interrupted" role="status">
      <span aria-hidden="true">{'⚠'}</span>
      <span className="roles-interrupted-msg">
        {interrupted.role ? '«' + interrupted.role + '» · ' : ''}
        {'мітинг перервано перезапуском'}
        {' · '}{Number(interrupted.turns) || 0} реплік
        {' · '}протокол не зібрано
      </span>
      <button
        className="btn-ghost roles-interrupted-dismiss"
        aria-label="Прибрати повідомлення про перерваний мітинг"
        title="Репліки збережені в базі; протокол з них поки не збирається"
        onClick={handleDismissInterrupted}
      >
        {'✕'}
      </button>
    </div>
  ) : null;

  // ── State 4: just finished ──
  if (finished) {
    const newest = artifacts[0];
    const rest = artifacts.slice(1);
    return (
      <div className="roles-strip">
        {interruptedBanner}
        <div className="roles-strip-top roles-finished">
          <span className="roles-finished-msg">
            {'✅'} {finished.name} завершено {'·'} {finished.minutes} хв
          </span>
          <div className="roles-finished-actions">
            {newest ? (
              <button
                className="btn primary"
                aria-label={'Відкрити протокол ' + newest.name}
                onClick={() => handleArtifactClick(newest.name)}
              >
                {'📄'} Відкрити протокол
              </button>
            ) : (
              <span className="info-line">протокол ще не готовий</span>
            )}
          </div>
        </div>

        {newest && openArtifact === newest.name && (
          <pre className="roles-artifact-view">
            {artifactLoading ? '…' : artifactContent}
          </pre>
        )}

        <div className="roles-history">
          <button
            className="btn-ghost"
            aria-expanded={historyOpen}
            onClick={() => setHistoryOpen((o) => !o)}
          >
            {historyOpen ? '▾' : '▸'} Історія артефактів ({rest.length})
          </button>
          {historyOpen && (
            <div className="roles-history-list">
              {rest.length === 0 && <div className="info-line">немає інших артефактів</div>}
              {rest.map((a) => (
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
                    <pre className="roles-artifact-view">
                      {artifactLoading ? '…' : artifactContent}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── State 1: idle ──
  if (!roleActive) {
    return (
      <div className="roles-strip">
        {interruptedBanner}
        <div className="roles-strip-top roles-idle">
          <span className="roles-label">РОЛІ</span>
          <div className="roles-idle-buttons">
            {rolesAvailable.length === 0 && (
              <span className="info-line">немає доступних ролей</span>
            )}
            {rolesAvailable.map((r, i) => (
              <button
                key={r.name || i}
                className="btn roles-role-btn"
                title={r.trigger}
                aria-label={'Почати роль ' + r.name}
                onClick={() => handleStartRole(r.trigger)}
              >
                {CHANNEL_ICON[r.channel] || ''} {r.name}
              </button>
            ))}
          </div>
        </div>
        <div className="roles-idle-hint">
          {'натисни або скажи: «повчи мене…», «почни мітинг»'}
        </div>
      </div>
    );
  }

  // ── State 2 / 3: active role ──
  const hintText = roleHint && roleHint.text ? roleHint.text : '';
  const isHints = roleChannel === 'hints';
  const lines = isHints ? hintLines(hintText) : [];
  const hintAgo = isHints && roleHint ? agoSeconds(roleHint.ts) : null;

  return (
    <div className="roles-strip">
      {interruptedBanner}
      <div className="roles-strip-top roles-active">
        {roleChannel === 'log' && (
          <>
            <span className="roles-rec-dot" aria-hidden="true"></span>
            <span className="roles-rec-label">ЗАПИС</span>
          </>
        )}
        <span className="status-badge on roles-active-badge">
          {CHANNEL_ICON[roleChannel] || ''} {roleActive}
        </span>
        <span className="meta">{elapsedMinutes(roleSince)} хв</span>
        <span className="meta">{'·'} {roleTurns} реплік</span>
        <button
          className="btn danger roles-end-btn"
          aria-label={'Закінчити роль ' + roleActive}
          onClick={handleEndRole}
          disabled={roleFinishing}
        >
          {roleFinishing ? '⏳ збираю підсумок…' : '■ Закінчити'}
        </button>
      </div>

      {!!roleLastHeard && (
        <div className="roles-last-heard">
          останнє почуте: «{truncate(roleLastHeard, 90)}»
        </div>
      )}

      {isHints && (
        <div className="roles-hint-panel">
          {lines.length === 0 ? (
            <div className="roles-hint-empty">
              {'слухаю… підказка з’явиться після першого питання'}
            </div>
          ) : (
            <>
              {roleHint.question && (
                <div className="roles-hint-question">«{roleHint.question}»</div>
              )}
              <div className="roles-hint-list">
                {lines.map((l, i) => (
                  <div className="roles-hint-item" key={i}>
                    <span className="roles-hint-marker" aria-hidden="true">▸</span>
                    <span>{l}</span>
                  </div>
                ))}
              </div>
              {hintAgo != null && (
                <div className="roles-hint-footer">оновлено {hintAgo} с тому</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
