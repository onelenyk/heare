import React from 'react';

// The engine publishes `mcp_status` as a JSON string in /state:
// { servers: [...], tools: N, ok: bool, error: str }. An absent key means
// this engine never said anything (the pipecat path never writes it) —
// which must read as "unknown", not as an outage, so nothing is rendered.
function mcpChip(raw) {
  if (!raw || typeof raw !== 'string') return null;
  let s;
  try { s = JSON.parse(raw); } catch (e) { return null; }
  if (!s || typeof s !== 'object') return null;
  const servers = Array.isArray(s.servers) ? s.servers : [];
  const tools = Number(s.tools) || 0;
  const error = s.error || '';
  // "off" is the feature switch, not a failure — the engine writes exactly
  // that string when mcp is switched off, and the two must not look alike.
  if (!s.ok && error === 'off') return { label: 'off', cls: 'off', title: 'MCP feature switched off — no servers, no external tools' };
  if (!s.ok) return { label: 'failed', cls: 'err', title: error || 'MCP did not connect' };
  if (servers.length === 0) return { label: 'no servers', cls: 'off', title: 'connected, but .mcp.json configures no server' };
  return {
    label: servers.length + (servers.length === 1 ? ' server · ' : ' servers · ') + tools + ' tools',
    cls: 'ok',
    title: servers.join(', '),
  };
}

// Header: identity and vitals on top, then the controls reached for
// constantly — audio, daemon — so they stay put no matter how far
// the work column scrolls.
export default function StatusBar({
  state, interruptEnabled,
  onMute, onCancel, onInterrupt, onDaemon,
}) {
  const running = state.running === true;
  const micMuted = state.mute_mic === '1' || state.mute_mic === true;
  const botMuted = state.mute_bot === '1' || state.mute_bot === true;
  const mcp = mcpChip(state.mcp_status);
  // Three states, not two: the bridge can be switched off, switched on with
  // nothing attached, or actually carrying a browser. It said "connected"
  // permanently while it only meant "enabled in config".
  const chromeLabel = state.chrome
    ? 'connected'
    : state.chrome_enabled === false
      ? 'disabled'
      : 'not connected';

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
          <span className="meta">
            chrome <strong className={'vital-' + (state.chrome ? 'ok' : 'off')}>{chromeLabel}</strong>
          </span>
          {mcp && (
            <span className="meta" title={mcp.title}>
              mcp <strong className={'vital-' + mcp.cls}>{mcp.label}</strong>
            </span>
          )}
          {state.version && <span className="meta">{state.version}</span>}
        </div>
      </div>

      <div className="status-row header-controls">
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
          {!running && (
            <button className="btn primary" onClick={() => onDaemon('start')}>{'▶ start'}</button>
          )}
          {running && (
            <button className="btn danger" onClick={() => onDaemon('stop')}>{'■ stop'}</button>
          )}
          {running && (
            <button className="btn" onClick={() => onDaemon('restart')}>{'↻ restart'}</button>
          )}
        </div>
      </div>
    </div>
  );
}
