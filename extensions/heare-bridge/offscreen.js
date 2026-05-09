'use strict';

const DEFAULT_PORT = 9333;
const BACKOFF_STEPS = [1000, 2000, 4000, 8000, 30000];
const PONG_TIMEOUT_MS = 35000;
const PING_INTERVAL_MS = 24000;
const RPC_TIMEOUT_MS = 30000;

let ws = null;
let backoffIdx = 0;
let reconnectTimer = null;
let authOk = false;
let lastPongTime = 0;
let pongCheckTimer = null;
let pingTimer = null;

let swPort = null;
const pendingRPCs = new Map();

// chrome.storage is not exposed to offscreen documents on every Chrome build,
// so all storage access is proxied through the service worker via the port.

let configReplyResolvers = [];

function loadConfig() {
  return new Promise((resolve) => {
    if (!swPort) {
      resolve({token: '', port: DEFAULT_PORT, pairCode: ''});
      return;
    }
    configReplyResolvers.push(resolve);
    try {
      swPort.postMessage({type: 'load_config'});
    } catch (err) {
      configReplyResolvers = configReplyResolvers.filter(r => r !== resolve);
      console.warn('[heare-bridge:offscreen] load_config post failed:', err);
      resolve({token: '', port: DEFAULT_PORT, pairCode: ''});
    }
  });
}

function storageRemove(key) {
  if (!swPort) return;
  try { swPort.postMessage({type: 'storage_remove', key}); } catch {}
}

function openOptionsPageViaSW() {
  if (!swPort) return;
  try { swPort.postMessage({type: 'open_options_page'}); } catch {}
}

// ---------- SW port ----------

function connectPort() {
  swPort = chrome.runtime.connect({name: 'heare-rpc'});

  swPort.onMessage.addListener((msg) => {
    if (msg.type === 'rpc_response') {
      const pending = pendingRPCs.get(msg.id);
      if (pending) {
        clearTimeout(pending.timer);
        pendingRPCs.delete(msg.id);
        pending.resolve({ok: msg.ok, result: msg.result, error: msg.error});
      }
      return;
    }
    if (msg.type === 'reconnect') {
      disconnect();
      connect();
      return;
    }
    if (msg.type === 'config') {
      const cfg = {
        token: msg.token || '',
        port: msg.port || DEFAULT_PORT,
        pairCode: msg.pairCode || '',
      };
      const pending = configReplyResolvers;
      configReplyResolvers = [];
      pending.forEach(r => r(cfg));
    }
  });

  swPort.onDisconnect.addListener(() => {
    swPort = null;
    setTimeout(connectPort, 500);
  });
}

function postToSW(msg) {
  if (!swPort) return;
  try {
    swPort.postMessage(msg);
  } catch (err) {
    console.warn('[heare-bridge:offscreen] postMessage failed:', err);
  }
}

function sendRPCToSW(id, method, params) {
  return new Promise((resolve, reject) => {
    if (!swPort) {
      reject(new Error('port disconnected'));
      return;
    }
    const timer = setTimeout(() => {
      pendingRPCs.delete(id);
      reject(new Error('RPC timeout'));
    }, RPC_TIMEOUT_MS);
    pendingRPCs.set(id, {resolve, reject, timer});
    swPort.postMessage({type: 'rpc_request', id, method, params});
  });
}

// ---------- pong watchdog ----------

function startPongCheck() {
  stopPongCheck();
  pongCheckTimer = setInterval(() => {
    if (!authOk || !ws || ws.readyState !== WebSocket.OPEN) return;
    if (Date.now() - lastPongTime > PONG_TIMEOUT_MS) {
      console.warn('[heare-bridge:offscreen] connection dead (no pong), opening options page');
      openOptionsPageViaSW();
      disconnect();
      scheduleReconnect();
    }
  }, 5000);
}

function stopPongCheck() {
  if (pongCheckTimer) {
    clearInterval(pongCheckTimer);
    pongCheckTimer = null;
  }
}

// in-offscreen ping setInterval keeps SW idle (no alarm-driven ping needed).
function startPing() {
  stopPing();
  pingTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN && authOk) {
      try { ws.send(JSON.stringify({v: 1, type: 'ping'})); } catch (err) {
        console.warn('[heare-bridge:offscreen] ping send failed:', err);
      }
    }
  }, PING_INTERVAL_MS);
}

function stopPing() {
  if (pingTimer) {
    clearInterval(pingTimer);
    pingTimer = null;
  }
}

// ---------- WebSocket ----------

async function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const {token, port, pairCode} = await loadConfig();
  if (!token && !pairCode) {
    postToSW({type: 'connection_state', state: 'disconnected'});
    return;
  }

  const url = 'ws://localhost:' + port;
  ws = new WebSocket(url);

  ws.onopen = () => {
    setTimeout(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        if (pairCode) {
          ws.send(JSON.stringify({v: 1, type: 'pair', code: pairCode}));
        } else {
          ws.send(JSON.stringify({v: 1, type: 'auth', token}));
        }
      }
    }, 50);
  };

  ws.onmessage = async (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch {
      console.warn('[heare-bridge:offscreen] non-JSON message ignored');
      return;
    }

    if (msg.type === 'auth_result') {
      if (msg.ok) {
        authOk = true;
        lastPongTime = Date.now();
        startPongCheck();
        startPing();
        backoffIdx = 0;
        postToSW({type: 'connection_state', state: 'connected'});
      } else {
        authOk = false;
        stopPongCheck();
        stopPing();
        postToSW({type: 'connection_state', state: 'auth_failed'});
        storageRemove('token');
        ws.close();
      }
      return;
    }

    if (msg.type === 'pair_result') {
      if (msg.ok && msg.token) {
        authOk = true;
        lastPongTime = Date.now();
        startPongCheck();
        startPing();
        backoffIdx = 0;
        postToSW({type: 'pair_success', token: msg.token});
        postToSW({type: 'connection_state', state: 'connected'});
      } else {
        authOk = false;
        stopPongCheck();
        stopPing();
        postToSW({type: 'connection_state', state: 'pair_failed'});
        storageRemove('pairCode');
        ws.close();
      }
      return;
    }

    if (msg.type === 'pong') {
      lastPongTime = Date.now();
      return;
    }

    if (msg.type === 'request') {
      const {id, method, params = {}} = msg;
      let response;
      try {
        const res = await sendRPCToSW(id, method, params);
        response = {v: 1, id, type: 'response', ok: res.ok, result: res.result, error: res.error};
      } catch (err) {
        response = {v: 1, id, type: 'response', ok: false, error: {code: 'RPC_PROXY_ERROR', message: String(err)}};
      }
      if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify(response)); } catch (err) {
          console.warn('[heare-bridge:offscreen] send failed:', err);
        }
      }
    }
  };

  ws.onclose = () => {
    authOk = false;
    stopPongCheck();
    stopPing();
    postToSW({type: 'connection_state', state: 'disconnected'});
    scheduleReconnect();
  };

  ws.onerror = () => {};
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  const delay = BACKOFF_STEPS[Math.min(backoffIdx, BACKOFF_STEPS.length - 1)];
  backoffIdx = Math.min(backoffIdx + 1, BACKOFF_STEPS.length - 1);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

function disconnect() {
  stopPongCheck();
  stopPing();
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    ws.onclose = null;
    try { ws.close(); } catch {}
    ws = null;
  }
  authOk = false;
}

// ---------- bootstrap ----------

connectPort();
connect();
