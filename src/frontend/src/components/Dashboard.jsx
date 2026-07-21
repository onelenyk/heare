import React, { useState, useEffect, useCallback, useRef } from 'react';
import { API } from '../App';
import StatusBar from './StatusBar';
import ControlsCard from './ControlsCard';
import UserVoiceBar from './UserVoiceBar';
import AgentStatusBar from './AgentStatusBar';
import DisplayCard from './DisplayCard';
import SettingsPanel from './SettingsPanel';
import UsageCard from './UsageCard';
import InjectPanel from './InjectPanel';
import AgentsPanel from './AgentsPanel';
import HistoryPanel from './HistoryPanel';
import AudioDevicePicker from './AudioDevicePicker';
import AudioPanel from './AudioPanel';
import BrainCard from './BrainCard';
import ToolsModal from './ToolsModal';
import BridgeModal from './BridgeModal';
import PromptManager from './PromptManager';
import Toast from './Toast';

const ACTIVITY_PAGE = 50;   // rows fetched on each poll
const ACTIVITY_OLDER = 100; // rows fetched per "load older" click

// Union by id, newest first. The poll only ever returns the newest page, so a
// plain replace would discard anything the user paged back to.
function mergeActivity(prev, incoming) {
  const byId = new Map();
  for (const row of [...incoming, ...prev]) {
    const key = row.id != null ? row.id : `${row.ts}:${row.content}`;
    if (!byId.has(key)) byId.set(key, row);
  }
  return [...byId.values()].sort((a, b) => b.ts - a.ts);
}

export default function Dashboard({ onOpenSetup }) {
  const [state, setState] = useState({});
  const [activity, setActivity] = useState([]);
  const [logs, setLogs] = useState([]);
  const [showSettings, setShowSettings] = useState(false);
  const [showBrain, setShowBrain] = useState(false);
  const [toast, setToast] = useState(null);
  const [injectText, setInjectText] = useState('');
  const [showAudio, setShowAudio] = useState(false);
  const [showAudioControls, setShowAudioControls] = useState(false);
  const [audioDevices, setAudioDevices] = useState(null);
  const [showTools, setShowTools] = useState(false);
  const [toolsData, setToolsData] = useState(null);
  // Bridge state
  const [showBridge, setShowBridge] = useState(false);
  const [bridge, setBridge] = useState(null);
  const [bridgeRevealing, setBridgeRevealing] = useState(false);
  const [bridgeRestartNotice, setBridgeRestartNotice] = useState(false);
  // Prompt Manager state
  const [showPrompts, setShowPrompts] = useState(false);
  const [prompts, setPrompts] = useState(null);
  const [selectedPrompt, setSelectedPrompt] = useState(null);
  const [preview, setPreview] = useState(null);
  const [editContent, setEditContent] = useState('');
  const bridgeIntervalRef = useRef(null);
  const [interruptEnabled, setInterruptEnabled] = useState(true);
  // Display widget state
  const [display, setDisplay] = useState(null);
  const [displayVisible, setDisplayVisible] = useState(true);
  const [displayFlash, setDisplayFlash] = useState(false);
  const [pollFailed, setPollFailed] = useState(false);
  const [historyTab, setHistoryTab] = useState('activity');
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [noMoreActivity, setNoMoreActivity] = useState(false);
  const [agents, setAgents] = useState([]);
  const [showAgents, setShowAgents] = useState(false);
  const [showUsage, setShowUsage] = useState(false);
  const [showCanvas, setShowCanvas] = useState(true);
  const [showHistory, setShowHistory] = useState(true);
  const [showInject, setShowInject] = useState(false);

  // ═══ Polling ═══
  useEffect(() => {
    poll();
    const t = setInterval(poll, 1000);
    return () => clearInterval(t);
  }, []);

  // Logs are polled separately, and only while that tab is on screen — the
  // endpoint tails a file that grows to 10MB, so fetching it once a second
  // regardless of whether anyone is looking was pure waste.
  useEffect(() => {
    if (!showHistory || historyTab !== 'logs') return;
    let cancelled = false;
    async function pollLogs() {
      try {
        const r = await fetch(API + '/logs?limit=200');
        const d = await r.json();
        if (!cancelled) setLogs(d.lines || []);
      } catch (e) { /* transient — the next tick retries */ }
    }
    pollLogs();
    const t = setInterval(pollLogs, 2000);
    return () => { cancelled = true; clearInterval(t); };
  }, [showHistory, historyTab]);

  async function poll() {
    try {
      const [sr, ar, dr] = await Promise.all([
        fetch(API + '/state'),
        fetch(API + '/activity?limit=' + ACTIVITY_PAGE),
        fetch(API + '/display'),
      ]);
      const [sData, aData, dData] = await Promise.all([sr.json(), ar.json(), dr.json()]);
      setState(sData);
      setInterruptEnabled(sData.interrupt_enabled !== false);
      // Merge rather than replace so rows pulled in by "load older" survive
      // the next tick.
      setActivity(prev => mergeActivity(prev, aData));
      if (dData && dData.content) {
        setDisplay(prev => {
          if (!prev || prev.ts !== dData.ts) {
            setDisplayFlash(true);
            setTimeout(() => setDisplayFlash(false), 1200);
            setDisplayVisible(true);
          }
          return dData;
        });
      }
      // Agents poll
      fetch(API + '/api/agents').then(r => r.json()).then(d => {
        setAgents(d.agents || []);
      }).catch(() => {});
      setPollFailed(false);
    } catch(e) {
      setPollFailed(true);
    }
  }

  // Page backwards from the oldest row currently held.
  async function loadOlderActivity() {
    const oldest = activity[activity.length - 1];
    if (!oldest || oldest.id == null || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const r = await fetch(
        API + '/activity?limit=' + ACTIVITY_OLDER + '&before_id=' + oldest.id
      );
      const older = await r.json();
      if (Array.isArray(older)) {
        if (older.length === 0) setNoMoreActivity(true);
        setActivity(prev => mergeActivity(prev, older));
      }
    } catch (e) {
      showToastMsg('could not load older activity: ' + e.message, 'err');
    } finally {
      setLoadingOlder(false);
    }
  }

  // ═══ HTTP helpers ═══
  async function post(url, body) {
    await fetch(API + url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  }

  async function saveSettings() {
    const g = document.getElementById('set-groq').value;
    const d = document.getElementById('set-deepseek').value;
    if (g || d) {
      await fetch(API + '/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ groq_api_key: g, deepseek_api_key: d }),
      });
    }
    setShowSettings(false);
  }

  function showToastMsg(msg, type) {
    setToast({ msg, type: type || 'ok' });
  }

  // ═══ 4c: Text injection ═══
  async function handleInject() {
    if (!injectText.trim()) return;
    try {
      const r = await fetch(API + '/inject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: injectText.trim() }),
      });
      const d = await r.json();
      if (d.ok) { showToastMsg('injected!', 'ok'); setInjectText(''); }
      else showToastMsg('inject failed: ' + (d.error || 'unknown'), 'err');
    } catch(e) { showToastMsg('inject failed: ' + e.message, 'err'); }
  }

  // ═══ 4d: Daemon lifecycle ═══
  async function handleDaemonAction(action) {
    try {
      if (action === 'start' || action === 'restart') { showToastMsg('starting...', 'info'); }
      await fetch(API + '/daemon', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      poll();
    } catch(e) { showToastMsg(action + ' failed: ' + e.message, 'err'); }
  }

  // ═══ 4e: Audio devices ═══
  async function fetchAudioDevices() {
    try {
      const r = await fetch(API + '/api/audio-devices');
      const d = await r.json();
      setAudioDevices(d);
      // Deliberately does NOT open the panel — the caller owns visibility.
      // Forcing it open here made the toggle impossible to switch off.
    } catch(e) { showToastMsg('audio fetch failed: ' + e.message, 'err'); }
  }

  async function handleDeviceSelect(sel) {
    try {
      const r = await fetch(API + '/api/audio-devices/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sel),
      });
      const d = await r.json();
      if (d.ok) { showToastMsg(d.kind + ': ' + d.device, 'ok'); poll(); }
      else showToastMsg('device select failed: ' + (d.error || ''), 'err');
    } catch(e) { showToastMsg('device select failed: ' + e.message, 'err'); }
  }

  async function handleAgentCancel(sid) {
    try {
      await fetch(API + '/api/agents/cancel', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({session_id: sid})
      });
      showToastMsg('agent cancelled', 'ok');
    } catch(e) { showToastMsg('cancel failed: ' + e.message, 'err'); }
  }

  // ═══ 4g: Tools ═══
  async function handleOpenTools() {
    try {
      const r = await fetch(API + '/api/tools');
      const d = await r.json();
      setToolsData(d);
    } catch(e) { setToolsData({ error: e.message, built_in: [], skills: [], mcps: [] }); }
    setShowTools(true);
  }

  // ═══ Prompt Manager ═══
  async function handleOpenPrompts() {
    setShowPrompts(true);
    const r = await fetch(API + '/api/prompts');
    setPrompts(await r.json());
  }
  async function handlePromptEdit(key) {
    const r = await fetch(API + '/api/prompts/' + key);
    const d = await r.json();
    setSelectedPrompt(d);
    setEditContent(d.content || '');
  }
  async function handlePromptPreview() {
    const r = await fetch(API + '/api/prompts/preview');
    setPreview(await r.text());
  }
  async function handlePromptSave() {
    if (!selectedPrompt) return;
    const r = await fetch(API + '/api/prompts/' + selectedPrompt.key, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: editContent}),
    });
    const d = await r.json();
    if (d.ok) showToastMsg('saved (' + d.char_count + ' chars)', 'ok');
    else showToastMsg('save failed: ' + (d.error || 'unknown'), 'err');
  }
  async function handlePromptClose() {
    setShowPrompts(false);
    setSelectedPrompt(null);
    setPreview(null);
  }

  // ═══ Bridge setup ═══
  async function fetchBridgeStatus() {
    try {
      const r = await fetch(API + '/api/bridge/status');
      const d = await r.json();
      // Compute token_hint from has_token (masked display)
      if (d.has_token) {
        // The full token isn't returned from /status for security;
        // we'll just store a hint for the masked display.
        d.token_hint = d.has_token ? '••••••••' : null;
      }
      setBridge(d);
    } catch(e) {
      setBridge({ enabled: false, connected: false, has_token: false, error: e.message });
    }
  }

  async function openBridgeSetup() {
    setBridgeRevealing(false);
    setBridgeRestartNotice(false);
    await fetchBridgeStatus();
    setShowBridge(true);
  }

  function closeBridgeSetup() {
    if (bridgeIntervalRef.current) { clearInterval(bridgeIntervalRef.current); bridgeIntervalRef.current = null; }
    setShowBridge(false);
    setBridge(null);
  }

  useEffect(() => {
    if (!showBridge) {
      if (bridgeIntervalRef.current) { clearInterval(bridgeIntervalRef.current); bridgeIntervalRef.current = null; }
      return;
    }
    fetchBridgeStatus();
    bridgeIntervalRef.current = setInterval(fetchBridgeStatus, 3000);
    return () => {
      if (bridgeIntervalRef.current) { clearInterval(bridgeIntervalRef.current); bridgeIntervalRef.current = null; }
    };
  }, [showBridge]);

  async function handleBridgeRotate() {
    if (!confirm('This will invalidate the current token. Continue?')) return;
    try {
      const r = await fetch(API + '/api/bridge/rotate-token', { method: 'POST' });
      const d = await r.json();
      if (d.ok) {
        setBridgeRestartNotice(true);
        showToastMsg('new token generated (restart required): ' + d.token, 'ok');
        fetchBridgeStatus();
      } else {
        showToastMsg('rotate failed: ' + (d.error || ''), 'err');
      }
    } catch(e) { showToastMsg('rotate failed: ' + e.message, 'err'); }
  }

  async function handleBridgeToggle(currentEnabled) {
    const newEnabled = !currentEnabled;
    try {
      const r = await fetch(API + '/api/bridge/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: newEnabled }),
      });
      const d = await r.json();
      if (d.ok) {
        setBridgeRestartNotice(true);
        showToastMsg('bridge ' + (newEnabled ? 'enabled' : 'disabled') + ' (restart required)', 'ok');
        fetchBridgeStatus();
      } else {
        showToastMsg('toggle failed: ' + (d.error || ''), 'err');
      }
    } catch(e) { showToastMsg('toggle failed: ' + e.message, 'err'); }
  }

  const running = state.running === true;

  return (
    <div className="dash">
      {/* Header — identity, vitals, and the always-reachable controls */}
      <StatusBar
        state={state}
        interruptEnabled={interruptEnabled}
        onModeChange={(mode) => post('/mode', { mode })}
        onMute={(target) => post('/mute', { target })}
        onCancel={() => post('/cancel', {})}
        onInterrupt={(enabled) => { setInterruptEnabled(enabled); post('/interrupt', { enabled }) }}
        onDaemon={(action) => handleDaemonAction(action)}
      />

      {pollFailed && <div className="warn-banner">⚠ connection lost — data may be stale</div>}

      <div className={"dash-body" + (showHistory ? "" : " no-side")}>
        {/* Left column — every control, always on screen */}
        <aside className="dash-rail">
      <ControlsCard
        showCanvas={showCanvas}
        showHistory={showHistory}
        showInject={showInject}
        showSettings={showSettings}
        showBrain={showBrain}
        showAudio={showAudio}
        showAudioControls={showAudioControls}
        showAgents={showAgents}
        showUsage={showUsage}
        showTools={showTools}
        showBridge={showBridge}
        showPrompts={showPrompts}
        onToggle={(w) => {
          const setters = { settings: setShowSettings, brain: setShowBrain, audio: setShowAudio, audiocontrols: setShowAudioControls, agents: setShowAgents, usage: setShowUsage, canvas: setShowCanvas, history: setShowHistory, inject: setShowInject };
          if (setters[w]) setters[w](prev => !prev);
          // Refresh the device list only when opening the panel.
          if (w === 'audio' && !showAudio) fetchAudioDevices();
        }}
        onOpenModal={(d) => {
          if (d === 'tools') handleOpenTools();
          if (d === 'bridge') openBridgeSetup();
          if (d === 'prompts') handleOpenPrompts();
        }}
        onOpenSetup={onOpenSetup}
      />
        </aside>

        {/* Centre column — live meters, canvas, and any panels you opened */}
        <div className="dash-main">

      {state.last_response && (
        <div className="response-line">{state.last_response}</div>
      )}

      {/* Voice & Agent status bars */}
      <div className="viz-row">
        <div className="viz-col">
          <div className="bar-section-label">🎤 you</div>
          <UserVoiceBar voiceState={state.voice_state} />
        </div>
        <div className="viz-col">
          <div className="bar-section-label">🤖 agent</div>
          <AgentStatusBar agentState={state.agent_state} />
        </div>
      </div>

      {/* Display widget */}
      {showCanvas && (
        <DisplayCard
          display={display}
          flash={displayFlash}
          visible={true}
          onDismiss={() => setShowCanvas(false)}
        />
      )}

      {/* Settings Panel */}
      {showSettings && (
        <SettingsPanel
          state={state}
          onSave={saveSettings}
          onToast={showToastMsg}
        />
      )}

      {/* Brain (LLM provider/token/model) */}
      {showBrain && (
        <BrainCard
          state={state}
          post={post}
          onClose={() => setShowBrain(false)}
        />
      )}

      {/* Usage stats */}
      {showUsage && (
        <div className="card">
          <div className="card-header">
            {'📊'} usage
            <button className="modal-close" onClick={() => setShowUsage(false)} style={{marginLeft: 'auto'}}>{'×'}</button>
          </div>
          <UsageCard usage={state.usage} />
        </div>
      )}

      {/* Text injection */}
      {showInject && (
        <InjectPanel
          value={injectText}
          onChange={setInjectText}
          onSend={handleInject}
        />
      )}

      {/* Agent management */}
      {showAgents && (
        <AgentsPanel
          agents={agents}
          onLaunch={poll}
          onCancel={handleAgentCancel}
          onToast={showToastMsg}
        />
      )}

      {/* Audio device picker */}
      {showAudio && (
        <AudioDevicePicker
          devices={audioDevices ? audioDevices.devices : null}
          activeInput={audioDevices ? audioDevices.active_input : null}
          activeOutput={audioDevices ? audioDevices.active_output : null}
          onSelect={handleDeviceSelect}
          onClose={() => setShowAudio(false)}
        />
      )}

      {/* Audio controls panel */}
      {showAudioControls && (
        <AudioPanel
          state={state}
          post={post}
          onClose={() => setShowAudioControls(false)}
        />
      )}

        </div>

        {/* Sidebar — history lives here, pinned and independently scrolling */}
        {showHistory && (
          <aside className="dash-side">
            <HistoryPanel
              tab={historyTab}
              onTabChange={setHistoryTab}
              activity={activity}
              logs={logs}
              onLoadOlder={loadOlderActivity}
              loadingOlder={loadingOlder}
              noMoreActivity={noMoreActivity}
              onClose={() => setShowHistory(false)}
            />
          </aside>
        )}
      </div>

      {/* Tools modal */}
      {showTools && <ToolsModal data={toolsData} onClose={() => setShowTools(false)} />}

      {/* Bridge setup modal */}
      {showBridge && (
        <BridgeModal
          data={bridge ? {...bridge, restart_notice: bridgeRestartNotice} : null}
          onClose={closeBridgeSetup}
          onRotate={handleBridgeRotate}
          onToggle={handleBridgeToggle}
          revealToken={bridgeRevealing}
          onToggleReveal={() => setBridgeRevealing(!bridgeRevealing)}
          onToast={showToastMsg}
        />
      )}

      {/* Prompt Manager modal */}
      {showPrompts && (
        <PromptManager
          prompts={prompts}
          selectedPrompt={selectedPrompt}
          editContent={editContent}
          preview={preview}
          usage={state.usage}
          onSelect={handlePromptEdit}
          onPreview={handlePromptPreview}
          onSave={handlePromptSave}
          onClose={handlePromptClose}
          onEditContent={setEditContent}
        />
      )}

      {/* Toast */}
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
    </div>
  );
}
