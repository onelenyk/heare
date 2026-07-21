import React from 'react';

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

  return (
    <div className="card">
      {state.last_response && (
        <div className="response-line">{state.last_response}</div>
      )}
      <div className="card-header">controls</div>

      {/* Row 1: Mode */}
      <div className="controls-label">mode</div>
      <div className="controls-group">
        {["silent", "focus", "ambient", "assistant"].map(m =>
          <button
            key={m}
            className={"btn" + (state.mode === m ? " active" : "")}
            onClick={() => onModeChange(m)}
          >{m}</button>
        )}
      </div>

      <div className="controls-sep"></div>

      {/* Row 2: Audio + Daemon */}
      <div style={{display: "flex", gap: "var(--s2)", flexWrap: "wrap"}}>
        <div style={{flex: 1, minWidth: 180}}>
          <div className="controls-label">audio</div>
          <div className="controls-group">
            <button className="btn" onClick={() => onMute("mic")}>
              {state.mute_mic === "1" || state.mute_mic === true ? '\ud83d\udd07 mic muted' : '\ud83c\udfa4 mic on'}
            </button>
            <button className="btn" onClick={() => onMute("bot")}>
              {state.mute_bot === "1" || state.mute_bot === true ? '\ud83d\udd07 bot muted' : '\ud83d\udd0a bot on'}
            </button>
            <button className="btn" onClick={() => onCancel()}>{'\u23f9 cancel'}</button>
            <button className="btn" onClick={() => onInterrupt(!interruptEnabled)}>
              {interruptEnabled ? '\ud83d\udde3 interrupt: on' : '\ud83e\udd10 interrupt: off'}
            </button>
          </div>
        </div>
        <div style={{flex: 1, minWidth: 140}}>
          <div className="controls-label">daemon</div>
          <div className="controls-group" style={{justifyContent: "flex-end"}}>
            {!running && !state.starting && (
              <button className="btn primary" onClick={() => onDaemon("start")}>{'\u25b6 start'}</button>
            )}
            {running && (
              <button className="btn danger" onClick={() => onDaemon("stop")}>{'\u25a0 stop'}</button>
            )}
            {running && (
              <button className="btn" onClick={() => onDaemon("restart")}>{'\u21bb restart'}</button>
            )}
            {!running && state.starting && (
              <span className="info-line" style={{color: "var(--accent)"}}>starting{'\u2026'}</span>
            )}
          </div>
        </div>
      </div>

      <div className="controls-sep"></div>

      {/* Row 3: Panels — widgets */}
      <div className="controls-label">widgets</div>
      <div className="controls-group">
        <button className={"btn" + (showSettings ? " active" : "")} onClick={() => onToggle("settings")}>
          {'\u2699'} settings
        </button>
        <button className={"btn" + (showBrain ? " active" : "")} onClick={() => onToggle("brain")}>
          {'\ud83e\udde0'} brain
        </button>
        <button className={"btn" + (showAudio ? " active" : "")} onClick={() => onToggle("audio")}>
          {'\ud83c\udfa7'} audio devices
        </button>
        <button className={"btn" + (showAudioControls ? " active" : "")} onClick={() => onToggle("audiocontrols")}>
          {'\ud83c\udf9b'} audio
        </button>
        <button className={"btn" + (showAgents ? " active" : "")} onClick={() => onToggle("agents")}>
          {'\ud83e\udd16'} agents
        </button>
        <button className={"btn" + (showUsage ? " active" : "")} onClick={() => onToggle("usage")}>
          {'\ud83d\udcca'} usage
        </button>
        <button className={"btn" + (showCanvas ? " active" : "")} onClick={() => onToggle("canvas")}>
          {'\ud83d\udcba'} canvas
        </button>
        <button className={"btn" + (showHistory ? " active" : "")} onClick={() => onToggle("history")}>
          {'\ud83d\udcdc'} history
        </button>
        <button className={"btn" + (showInject ? " active" : "")} onClick={() => onToggle("inject")}>
          {'\ud83d\udcac'} inject
        </button>
      </div>

      <div className="controls-sep"></div>

      <div className="controls-label">dialogs</div>
      <div className="controls-group">
        <button className={"btn" + (chromeProfiles !== null ? " active" : "")} onClick={() => onOpenModal("chrome")}>
          {'\ud83c\udf10'} chrome
        </button>
        <button className={"btn" + (showTools ? " active" : "")} onClick={() => onOpenModal("tools")}>
          {'\ud83d\udd27'} tools
        </button>
        <button className={"btn" + (showBridge ? " active" : "")} onClick={() => onOpenModal("bridge")}>
          {'\ud83c\udf09'} bridge
        </button>
        <button className={"btn" + (showPrompts ? " active" : "")} onClick={() => onOpenModal("prompts")}>
          {'\ud83d\udcdd'} prompts
        </button>
        <button className="btn" onClick={onOpenSetup}>{'\ud83e\udde0'} Setup</button>
      </div>

      <div className="controls-sep"></div>

      {/* Chrome profile picker */}
      {chromeProfiles !== null && (
        <div className="controls-group" style={{marginTop: 4}}>
          {chromeProfiles.length > 0 && (
            <select
              value={chromeProfile}
              onChange={e => onChromeProfileChange ? onChromeProfileChange(e.target.value) : null}
              style={{
                flex: 1, minWidth: 0, background: "var(--bg)", color: "var(--text)",
                border: "1px solid var(--border)", padding: "3px 6px", fontSize: 11, borderRadius: 4
              }}
            >
              {chromeProfiles.map(p => (
                <option key={p.directory} value={p.directory}>
                  {p.display_name}{p.last_used ? " (last)" : ""}
                </option>
              ))}
            </select>
          )}
          <button className="btn" onClick={onChromeLaunch}>launch chrome</button>
          <button className="btn" onClick={onChromeClose || (() => onOpenModal("chrome"))}>{'\u2715'}</button>
        </div>
      )}
    </div>
  );
}
