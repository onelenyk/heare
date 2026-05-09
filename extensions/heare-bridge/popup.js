'use strict';

const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const portSpan = document.getElementById('port');
const optionsBtn = document.getElementById('options');

function renderStatus(connectionStatus) {
  statusDot.className = 'dot';
  statusText.className = 'status-text';

  if (connectionStatus === 'connected') {
    statusDot.classList.add('green');
    statusText.classList.add('connected');
    statusText.textContent = 'Connected';
  } else if (connectionStatus === 'auth_failed') {
    statusDot.classList.add('red');
    statusText.classList.add('failed');
    statusText.textContent = 'Auth failed — check token';
  } else if (connectionStatus === 'pair_failed') {
    statusDot.classList.add('red');
    statusText.classList.add('failed');
    statusText.textContent = 'Pair failed — check code';
  } else {
    statusDot.classList.add('grey');
    statusText.classList.add('disconnected');
    statusText.textContent = 'Not connected';
  }
}

chrome.storage.local.get({port: 9333, connectionStatus: ''}, (data) => {
  portSpan.textContent = data.port || 9333;
  renderStatus(data.connectionStatus || '');
});

optionsBtn.addEventListener('click', () => {
  chrome.runtime.openOptionsPage();
});
