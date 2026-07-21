import React from 'react';

// The rail owns what to *show*; the header owns what to *do* (mode, audio,
// daemon). Panel toggles open cards in the centre column rather than
// navigating, so several can be open at once.
export default function ControlsCard({
  showCanvas, showHistory, showInject,
  showSettings, showBrain, showAudio, showAudioControls, showAgents, showUsage,
  showTools, showBridge, showPrompts,
  onOpenSetup,
  onToggle,
  onOpenModal,
}) {
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
    ['tools', '🔧', 'tools', showTools],
    ['bridge', '🌉', 'bridge', showBridge],
    ['prompts', '📝', 'prompts', showPrompts],
  ];

  return (
    <div className="rail-card">
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

    </div>
  );
}
