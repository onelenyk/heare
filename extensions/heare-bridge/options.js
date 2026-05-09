'use strict';

const tokenInput = document.getElementById('token');
const portInput = document.getElementById('port');
const saveBtn = document.getElementById('save');
const pairBtn = document.getElementById('pair');
const pairCodeInput = document.getElementById('pair-code');
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');

function renderStatus(connectionStatus) {
  statusDot.className = '';
  if (connectionStatus === 'connected') {
    statusDot.classList.add('green');
    statusText.textContent = 'Connected';
  } else if (connectionStatus === 'auth_failed') {
    statusDot.classList.add('red');
    statusText.textContent = 'Auth failed — check token';
  } else if (connectionStatus === 'pair_failed') {
    statusDot.classList.add('red');
    statusText.textContent = 'Pair failed — check code';
  } else {
    statusText.textContent = 'Not connected';
  }
}

chrome.storage.local.get({token: '', port: 9333, connectionStatus: ''}, (data) => {
  if (data.token) tokenInput.value = data.token;
  portInput.value = data.port || 9333;
  renderStatus(data.connectionStatus || '');
});

saveBtn.addEventListener('click', () => {
  const token = tokenInput.value.trim();
  const port = parseInt(portInput.value, 10) || 9333;
  chrome.storage.local.set({token, port}, () => {
    chrome.runtime.sendMessage({type: 'reconnect'});
  });
});

pairBtn.addEventListener('click', () => {
  const code = pairCodeInput.value.trim();
  if (code.length !== 6 || !/^\d{6}$/.test(code)) {
    statusText.textContent = 'Invalid code — must be 6 digits';
    statusDot.className = '';
    statusDot.classList.add('red');
    return;
  }
  const port = parseInt(portInput.value, 10) || 9333;
  chrome.storage.local.set({pairCode: code, port}, () => {
    statusText.textContent = 'Pairing…';
    statusDot.className = '';
    chrome.runtime.sendMessage({type: 'reconnect'});
  });
});

chrome.storage.onChanged.addListener((changes) => {
  if (changes.connectionStatus) {
    renderStatus(changes.connectionStatus.newValue || '');
  }
});
