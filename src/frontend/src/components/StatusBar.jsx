import React from 'react';

const MODES = ['silent', 'focus', 'ambient', 'assistant'];

// Header: identity and vitals on top, then the controls reached for
// constantly — mode, audio, daemon — so they stay put no matter how far
// the work column scrolls.
export default function StatusBar({
  state, interruptEnabled,
  onModeChange, onMute, onCancel, onInterrupt, onDaemon,
}) {
  const running = state.running === true;
  const micMuted = state.mute_mic === '1' || state.mute_mic === true;
  const botMuted = state.mute_bot === '1' || state.mute_bot === true;

  return (
    <div className="status-bar">
      <div className="status-row">
        <div className="status-group">
          <span className={'dot ' + (running ? 'on' : 'off')}></span>
          <span className="identity">{state.agent || 'heare'} {state.emoji || ''}</span>
          <span className={'status-badge ' + (running ? 'on' : 'off')}>
            {running ? 'running' : 'stopped'}
          </span>
          {state.uptime != null && state.uptime !== '' && (
            <span className="meta">{state.uptime}</span>
          )}
        </div>
        <div className="status-group">
          <span className="meta">provider <strong>{state.provider || '?'}</strong></span>
          <span className="meta">{state.transcripts_count || 0} msgs</span>
          {state.pid != null && <span className="meta">pid <strong>{state.pid}</strong></span>}
          <span className="meta">chrome <strong>{state.chrome ? 'connected' : 'off'}</strong></span>
          {state.version && <span className="meta">{state.version}</span>}
        </div>
      </div>

      <div className="status-row header-controls">
        <div className="hc-group">
          <span className="hc-label">mode</span>
          {MODES.map(m => (
            <button
              key={m}
              className={'btn' + (state.mode === m ? ' active' : '')}
              onClick={() => onModeChange(m)}
            >{m}</button>
          ))}
        </div>

        <div className="hc-group">
          <span className="hc-label">audio</span>
          <button className="btn" onClick={() => onMute('mic')}>
            {micMuted ? '🔇 mic muted' : '🎤 mic on'}
          </button>
          <button className="btn" onClick={() => onMute('bot')}>
            {botMuted ? '🔇 bot muted' : '🔊 bot on'}
          </button>
          <button className="btn" onClick={() => onCancel()}>{'⏹ cancel'}</button>
          <button className="btn" onClick={() => onInterrupt(!interruptEnabled)}>
            {interruptEnabled ? '🗣 interrupt: on' : '🤐 interrupt: off'}
          </button>
        </div>

        <div className="hc-group hc-right">
          <span className="hc-label">daemon</span>
          {!running && !state.starting && (
            <button className="btn primary" onClick={() => onDaemon('start')}>{'▶ start'}</button>
          )}
          {running && (
            <button className="btn danger" onClick={() => onDaemon('stop')}>{'■ stop'}</button>
          )}
          {running && (
            <button className="btn" onClick={() => onDaemon('restart')}>{'↻ restart'}</button>
          )}
          {!running && state.starting && (
            <span className="meta" style={{ color: 'var(--accent)' }}>starting…</span>
          )}
        </div>
      </div>
    </div>
  );
}
