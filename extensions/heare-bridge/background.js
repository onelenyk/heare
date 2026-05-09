'use strict';

const BLOCKED_SCHEMES = ['chrome://', 'chrome-extension://', 'file://'];

let HAS_OFFSCREEN = typeof chrome.offscreen !== 'undefined';
let offscreenPort = null;

const NO_OFFSCREEN_WARNING = '[heare-bridge] chrome.offscreen unavailable; bridge will not connect on this Chrome version';

// ---------- badge ----------

function badgeGreen() {
  chrome.action.setBadgeText({text: '●'});
  chrome.action.setBadgeBackgroundColor({color: '#00aa00'});
  chrome.storage.local.set({connectionStatus: 'connected'});
}

function badgeRed() {
  chrome.action.setBadgeText({text: '●'});
  chrome.action.setBadgeBackgroundColor({color: '#cc0000'});
  chrome.storage.local.set({connectionStatus: 'auth_failed'});
}

function badgePairFailed() {
  chrome.action.setBadgeText({text: '●'});
  chrome.action.setBadgeBackgroundColor({color: '#cc0000'});
  chrome.storage.local.set({connectionStatus: 'pair_failed'});
}

function badgeGrey() {
  chrome.action.setBadgeText({text: '●'});
  chrome.action.setBadgeBackgroundColor({color: '#888888'});
  chrome.storage.local.set({connectionStatus: ''});
}

// ---------- URL blocklist ----------

function isBlocked(url) {
  return BLOCKED_SCHEMES.some(s => url.startsWith(s));
}

function blockedError(url) {
  return {ok: false, error: {code: 'BLOCKED_URL', message: 'Cannot access ' + url.split('://')[0] + ':// pages'}};
}

// ---------- tab helpers ----------

async function resolveTabId(tabId) {
  if (tabId != null) return tabId;
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  return tab ? tab.id : null;
}

async function getTab(tabId) {
  try {
    return await chrome.tabs.get(tabId);
  } catch {
    return null;
  }
}

// ---------- handlers ----------

async function handleListTabs() {
  const tabs = await chrome.tabs.query({});
  return {ok: true, result: {tabs: tabs.map(t => ({id: t.id, url: t.url, title: t.title, active: t.active}))}};
}

async function handleReadPage(params) {
  const tabId = await resolveTabId(params.tab_id ?? null);
  if (tabId == null) return {ok: false, error: {code: 'NO_TAB', message: 'No active tab found'}};
  const tab = await getTab(tabId);
  if (!tab) return {ok: false, error: {code: 'TAB_NOT_FOUND', message: 'Tab ' + tabId + ' not found'}};
  if (isBlocked(tab.url)) return blockedError(tab.url);
  const [{result}] = await chrome.scripting.executeScript({
    target: {tabId},
    func: () => ({url: location.href, title: document.title, text: document.body.innerText.slice(0, 50000)}),
  });
  return {ok: true, result};
}

async function handleClick(params) {
  const tabId = await resolveTabId(params.tab_id ?? null);
  if (tabId == null) return {ok: false, error: {code: 'NO_TAB', message: 'No active tab found'}};
  const tab = await getTab(tabId);
  if (!tab) return {ok: false, error: {code: 'TAB_NOT_FOUND', message: 'Tab ' + tabId + ' not found'}};
  if (isBlocked(tab.url)) return blockedError(tab.url);
  const selector = params.selector;
  const [{result}] = await chrome.scripting.executeScript({
    target: {tabId},
    func: (sel) => { const el = document.querySelector(sel); if (el) { el.click(); return true; } return false; },
    args: [selector],
  });
  return {ok: true, result: {clicked: !!result}};
}

async function handleFill(params) {
  const tabId = await resolveTabId(params.tab_id ?? null);
  if (tabId == null) return {ok: false, error: {code: 'NO_TAB', message: 'No active tab found'}};
  const tab = await getTab(tabId);
  if (!tab) return {ok: false, error: {code: 'TAB_NOT_FOUND', message: 'Tab ' + tabId + ' not found'}};
  if (isBlocked(tab.url)) return blockedError(tab.url);
  const [{result}] = await chrome.scripting.executeScript({
    target: {tabId},
    func: (sel, val) => {
      const el = document.querySelector(sel);
      if (!el) return false;
      el.value = val;
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      return true;
    },
    args: [params.selector, params.value],
  });
  return {ok: true, result: {filled: !!result}};
}

async function handleExtract(params) {
  const tabId = await resolveTabId(params.tab_id ?? null);
  if (tabId == null) return {ok: false, error: {code: 'NO_TAB', message: 'No active tab found'}};
  const tab = await getTab(tabId);
  if (!tab) return {ok: false, error: {code: 'TAB_NOT_FOUND', message: 'Tab ' + tabId + ' not found'}};
  if (isBlocked(tab.url)) return blockedError(tab.url);
  const [{result}] = await chrome.scripting.executeScript({
    target: {tabId},
    func: (sel) => {
      return Array.from(document.querySelectorAll(sel)).map(el => ({
        tag: el.tagName.toLowerCase(),
        text: el.innerText,
        attrs: Object.fromEntries(Array.from(el.attributes).map(a => [a.name, a.value])),
      }));
    },
    args: [params.selector],
  });
  return {ok: true, result: {elements: result}};
}

async function handleNavigate(params) {
  if (isBlocked(params.url)) return blockedError(params.url);
  const tabId = await resolveTabId(params.tab_id ?? null);
  if (tabId == null) return {ok: false, error: {code: 'NO_TAB', message: 'No active tab found'}};
  const url = params.url;
  await chrome.tabs.update(tabId, {url});
  const nav = await new Promise((resolve) => {
    const timer = setTimeout(() => {
      chrome.webNavigation.onCompleted.removeListener(listener);
      resolve(null);
    }, 10000);
    function listener(details) {
      if (details.tabId === tabId && details.frameId === 0) {
        clearTimeout(timer);
        chrome.webNavigation.onCompleted.removeListener(listener);
        resolve(details);
      }
    }
    chrome.webNavigation.onCompleted.addListener(listener);
  });
  if (!nav) return {ok: false, error: {code: 'NAVIGATE_TIMEOUT', message: 'Navigation to ' + url + ' timed out'}};
  const tab = await getTab(tabId);
  return {ok: true, result: {url: tab ? tab.url : url, title: tab ? tab.title : ''}};
}

async function handleOpenTab(params) {
  if (isBlocked(params.url)) return blockedError(params.url);
  const tab = await chrome.tabs.create({url: params.url});
  return {ok: true, result: {tab_id: tab.id, url: tab.url, title: tab.title || ''}};
}

async function handleActivateTab(params) {
  const tabId = await resolveTabId(params.tab_id ?? null);
  if (tabId == null) return {ok: false, error: {code: 'NO_TAB', message: 'No active tab found'}};
  const tab = await getTab(tabId);
  if (!tab) return {ok: false, error: {code: 'TAB_NOT_FOUND', message: 'Tab ' + tabId + ' not found'}};
  await chrome.tabs.update(tabId, {active: true});
  if (tab.windowId != null) {
    try { await chrome.windows.update(tab.windowId, {focused: true}); } catch {}
  }
  return {ok: true, result: {tab_id: tabId, url: tab.url, title: tab.title || ''}};
}

const HANDLERS = {
  list_tabs: handleListTabs,
  read_page: handleReadPage,
  click: handleClick,
  fill: handleFill,
  extract: handleExtract,
  navigate: handleNavigate,
  open_tab: handleOpenTab,
  activate_tab: handleActivateTab,
};

// ---------- offscreen lifecycle ----------

async function ensureOffscreen() {
  if (!HAS_OFFSCREEN) return false;
  try {
    const has = await chrome.offscreen.hasDocument();
    if (!has) {
      await chrome.offscreen.createDocument({
        url: 'offscreen.html',
        reasons: [chrome.offscreen.Reason.WORKERS],
        justification: 'Persistent WebSocket to local Heare daemon',
      });
    }
    return true;
  } catch (err) {
    console.warn('[heare-bridge] offscreen createDocument failed, falling back to SW WS:', err);
    HAS_OFFSCREEN = false;
    return false;
  }
}

// ---------- offscreen RPC port ----------

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'heare-rpc') return;
  offscreenPort = port;

  port.onMessage.addListener(async (msg) => {
    if (msg.type === 'rpc_request') {
      const handler = HANDLERS[msg.method];
      let result;
      if (!handler) {
        result = {ok: false, error: {code: 'UNKNOWN_METHOD', message: 'Unknown method: ' + msg.method}};
      } else {
        try {
          result = await handler(msg.params || {});
        } catch (err) {
          result = {ok: false, error: {code: 'HANDLER_ERROR', message: String(err)}};
        }
      }
      try {
        port.postMessage({type: 'rpc_response', id: msg.id, ok: result.ok, result: result.result, error: result.error});
      } catch (err) {
        console.warn('[heare-bridge] rpc_response post failed:', err);
      }
      return;
    }

    if (msg.type === 'connection_state') {
      if (msg.state === 'connected') badgeGreen();
      else if (msg.state === 'auth_failed') badgeRed();
      else if (msg.state === 'pair_failed') badgePairFailed();
      else badgeGrey();
      return;
    }

    if (msg.type === 'pair_success') {
      chrome.storage.local.set({token: msg.token});
      chrome.storage.local.remove('pairCode');
      return;
    }

    if (msg.type === 'load_config') {
      chrome.storage.local.get({token: '', port: 9333, pairCode: ''}, (data) => {
        try {
          port.postMessage({type: 'config', token: data.token, port: data.port, pairCode: data.pairCode});
        } catch (err) {
          console.warn('[heare-bridge] config reply failed:', err);
        }
      });
      return;
    }

    if (msg.type === 'storage_remove') {
      if (typeof msg.key === 'string') chrome.storage.local.remove(msg.key);
      return;
    }

    if (msg.type === 'open_options_page') {
      try { chrome.runtime.openOptionsPage(); } catch (err) {
        console.warn('[heare-bridge] openOptionsPage failed:', err);
      }
    }
  });

  port.onDisconnect.addListener(() => {
    offscreenPort = null;
  });
});

// ---------- popup/options reconnect (forwarded via port; never re-broadcast) ----------

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === 'reconnect') {
    if (offscreenPort) {
      try { offscreenPort.postMessage({type: 'reconnect'}); } catch (err) {
        console.warn('[heare-bridge] forward reconnect failed:', err);
      }
      sendResponse({ok: true});
    } else if (HAS_OFFSCREEN) {
      ensureOffscreen().then(() => sendResponse({ok: true, note: 'offscreen re-created, will auto-connect'}));
    } else {
      // No offscreen support and no fallback WS in this build; surface to caller.
      sendResponse({ok: false, error: 'offscreen unavailable on this Chrome version'});
    }
    return true;
  }
});

// ---------- keepalive alarm ----------

chrome.alarms.create('keepalive', {periodInMinutes: 0.4});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== 'keepalive') return;
  if (HAS_OFFSCREEN) {
    ensureOffscreen();
  }
});

// ---------- lifecycle ----------

chrome.runtime.onInstalled.addListener(async () => {
  chrome.storage.local.set({connectionStatus: ''});
  if (HAS_OFFSCREEN) {
    await ensureOffscreen();
  } else {
    console.warn(NO_OFFSCREEN_WARNING);
  }
});

chrome.runtime.onStartup.addListener(async () => {
  chrome.storage.local.set({connectionStatus: ''});
  if (HAS_OFFSCREEN) {
    await ensureOffscreen();
  } else {
    console.warn(NO_OFFSCREEN_WARNING);
  }
});
