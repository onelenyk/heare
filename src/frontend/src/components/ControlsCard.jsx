import React from 'react';

// Every control lives here, always on screen — nothing is behind a tab or a
// menu. The panel toggles below open cards in the centre column rather than
// navigating away, so you can have several open at once.
export default function ControlsCard({
  state,
  showCanvas, showHistory, showInject,
  showSettings, showBrain, showAudio, showAudioControls, showAgents, showUsage,
  showTools, showBridge, showPrompts,
  onOpenSetup,
  interruptEnabled,
  onModeChange, onMute, onCancel, onInterrupt, onDaemon,
  onToggle,
  onOpenModal,
  chromeProfiles, chromeProfile, onChromeLaunch, onChromeProfileChange, onChromeClose,
}) {
  const running = state.running === true;
  const micMuted = state.mute_mic === '1' || state.mute_mic === true;
  const botMuted = state.mute_bot === '1' || state.mute_bot === true;

  const panels = [
    ['canvas', '🖼', 'canvas', showCanvas],
    ['history', '📜', 'history', showHistory],
    ['brain', '🧠', 'brain', showBrain],
    ['audiocontrols', '🎛', 'audio', showAudioControls],
    ['audio', '🎧', 'audio devices', showAudio],
    ['agents', '🤖', 'agents', showAgents],
    ['usage', '📊', 'usage', showUsage],
    ['inject', '💬', 'inject', showInject],
    ['settings', '⚙', 'settings', showSettings],
  ];

  const dialogs = [
    ['chrome', '🌐', 'chrome', chromeProfiles !== null],
    ['tools', '🔧', 'tools', showTools],
    ['bridge', '🌉', 'bridge', showBridge],
    ['prompts', '📝', 'prompts', showPrompts],
  ];

  return (
    <div className="rail-card">
      <div className="rail-section">
        <div className="rail-label">mode</div>
        <div className="rail-group rail-group-2col">
          {['silent', 'focus', 'ambient', 'assistant'].map(m => (
            <button
              key={m}
              className={'btn rail-btn' + (state.mode === m ? ' active' : '')}
              onClick={() => onModeChange(m)}
            >{m}</button>
          ))}
        </div>
      </div>

      <div className="rail-section">
        <div className="rail-label">audio</div>
        <div className="rail-group">
          <button className="btn rail-btn" onClick={() => onMute('mic')}>
            {micMuted ? '🔇 mic muted' : '🎤 mic on'}
          </button>
          <button className="btn rail-btn" onClick={() => onMute('bot')}>
            {botMuted ? '🔇 bot muted' : '🔊 bot on'}
          </button>
          <button className="btn rail-btn" onClick={() => onCancel()}>{'⏹ cancel'}</button>
          <button className="btn rail-btn" onClick={() => onInterrupt(!interruptEnabled)}>
            {interruptEnabled ? '🗣 interrupt: on' : '🤐 interrupt: off'}
          </button>
        </div>
      </div>

      <div className="rail-section">
        <div className="rail-label">daemon</div>
        <div className="rail-group">
          {!running && !state.starting && (
            <button className="btn rail-btn primary" onClick={() => onDaemon('start')}>{'▶ start'}</button>
          )}
          {running && (
            <button className="btn rail-btn danger" onClick={() => onDaemon('stop')}>{'■ stop'}</button>
          )}
          {running && (
            <button className="btn rail-btn" onClick={() => onDaemon('restart')}>{'↻ restart'}</button>
          )}
          {!running && state.starting && (
            <span className="rail-note">starting…</span>
          )}
        </div>
      </div>

      <div className="rail-section">
        <div className="rail-label">panels</div>
        <div className="rail-group">
          {panels.map(([key, icon, label, on]) => (
            <button
              key={key}
              className={'btn rail-btn' + (on ? ' active' : '')}
              onClick={() => onToggle(key)}
            >
              <span className="rail-btn-icon">{icon}</span>
              <span className="rail-btn-label">{label}</span>
              <span className="rail-btn-state">{on ? '●' : ''}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="rail-section">
        <div className="rail-label">dialogs</div>
        <div className="rail-group rail-group-2col">
          {dialogs.map(([key, icon, label, on]) => (
            <button
              key={key}
              className={'btn rail-btn' + (on ? ' active' : '')}
              onClick={() => onOpenModal(key)}
            >
              <span className="rail-btn-icon">{icon}</span>
              <span className="rail-btn-label">{label}</span>
            </button>
          ))}
          <button className="btn rail-btn" onClick={onOpenSetup}>
            <span className="rail-btn-icon">{'🧩'}</span>
            <span className="rail-btn-label">setup</span>
          </button>
        </div>
      </div>

      {chromeProfiles !== null && (
        <div className="rail-section">
          <div className="rail-label">chrome profile</div>
          <div className="rail-group">
            {chromeProfiles.length > 0 && (
              <select
                value={chromeProfile}
                onChange={e => onChromeProfileChange && onChromeProfileChange(e.target.value)}
                className="rail-select"
              >
                {chromeProfiles.map(p => (
                  <option key={p.directory} value={p.directory}>
                    {p.display_name}{p.last_used ? ' (last)' : ''}
                  </option>
                ))}
              </select>
            )}
            <button className="btn rail-btn" onClick={onChromeLaunch}>launch chrome</button>
            <button className="btn rail-btn" onClick={onChromeClose}>{'✕ close'}</button>
          </div>
        </div>
      )}
    </div>
  );
}
