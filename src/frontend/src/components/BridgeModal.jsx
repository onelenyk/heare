import React, { useState } from "react";
import { API } from "../App";

export default function BridgeModal({ data, onClose, onRotate, onToggle, revealToken, onToggleReveal, onToast }) {
  const [tokenFull, setTokenFull] = useState(null);
  const [tokenLoading, setTokenLoading] = useState(false);

  if (!data) return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card bridge-modal-card">
        <div className="modal-header">
          <span className="modal-title">bridge setup</span>
          <button className="modal-close" onClick={onClose}>&times;</button>
        </div>
        <div className="modal-body">
          <div className="info-line">Loading\u2026</div>
        </div>
      </div>
    </div>
  );

  const pairRemaining = data.pair_remaining_s || 0;
  const hasPairCode = data.pair_code && pairRemaining > 0;
  const ttlText = hasPairCode ? Math.floor(pairRemaining) + "s" : "";
  const ttlUrgent = hasPairCode && pairRemaining <= 15;
  const ttlExpired = data.pair_code && pairRemaining <= 0;

  let statusClass = "bridge-status-idle";
  let dotClass = "gray";
  let statusText = "idle";
  if (data.connected) { statusClass = "bridge-status-connected"; dotClass = "green"; statusText = "connected"; }
  else if (hasPairCode) { statusClass = "bridge-status-pairing"; dotClass = "yellow"; statusText = "pair pending"; }
  else if (!data.enabled) { statusClass = "bridge-status-disabled"; dotClass = "red"; statusText = "disabled"; }

  async function fetchToken() {
    setTokenLoading(true);
    try {
      const r = await fetch(API + "/api/bridge/token");
      const d = await r.json();
      setTokenFull(d.token);
    } catch(e) {
      onToast("failed to fetch token", "err");
    } finally {
      setTokenLoading(false);
    }
  }

  async function handleShowToken() {
    if (!revealToken) {
      if (!tokenFull) await fetchToken();
    }
    onToggleReveal();
  }

  function copy(text, label) {
    navigator.clipboard.writeText(text).then(() => {
      onToast(label + " copied", "ok");
    }).catch(() => {
      onToast("copy failed", "err");
    });
  }

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card bridge-modal-card">
        <div className="modal-header">
          <span className="modal-title">bridge setup</span>
          <button className="modal-close" onClick={onClose}>&times;</button>
        </div>
        <div className="modal-body">
          <div className={"bridge-status-row " + statusClass}>
            <span className={"bridge-dot " + dotClass}></span>
            <span>{statusText}</span>
            {hasPairCode && <span style={{marginLeft:"auto",fontSize:11,color:"var(--muted)"}}>{ttlText}</span>}
          </div>

          {hasPairCode && (
            <div>
              <div className="bridge-pair-code">{data.pair_code}</div>
              <div className={"bridge-ttl" + (ttlExpired ? " expired" : ttlUrgent ? " urgent" : "")}>
                {ttlExpired ? "expired" : "expires in " + ttlText}
              </div>
              <div style={{textAlign:"center",marginBottom:8}}>
                <button className="bridge-btn-sm" onClick={() => copy(data.pair_code, "pair code")}>copy code</button>
              </div>
            </div>
          )}

          {!hasPairCode && (
            <div className="bridge-no-code">
              {data.enabled
                ? "no active pair code \u2014 open the Chrome extension to generate one"
                : "bridge is disabled \u2014 enable it above"}
            </div>
          )}

          <div className="modal-section">
            <h4>connection</h4>
            <div className="bridge-field">
              <span className="bridge-label">websocket url</span>
              <div className="flex-row-sm">
                <span className="bridge-value">{data.ws_url}</span>
                <button className="bridge-btn-sm" onClick={() => copy(data.ws_url, "URL")}>copy</button>
              </div>
            </div>
            <div className="bridge-field">
              <span className="bridge-label">port</span>
              <span className="bridge-value">{data.port}</span>
            </div>
            <div className="bridge-field">
              <span className="bridge-label">token</span>
              <div className="flex-row-sm">
                {revealToken && tokenFull
                  ? <span className="bridge-value" style={{maxWidth:180,overflow:"hidden",textOverflow:"ellipsis"}}>{tokenFull}</span>
                  : <span className="bridge-value masked">{data.token_hint || "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"}</span>
                }
                {data.has_token && (
                  <button className="bridge-btn-sm" onClick={handleShowToken} disabled={tokenLoading}>
                    {tokenLoading ? "..." : revealToken ? "hide" : "show"}
                  </button>
                )}
                {revealToken && tokenFull && (
                  <button className="bridge-btn-sm" onClick={() => copy(tokenFull, "token")}>copy</button>
                )}
                <button className="bridge-btn-sm" onClick={onRotate}>rotate</button>
              </div>
            </div>
          </div>

          <div className="modal-section">
            <h4>enable</h4>
            <div className="bridge-field">
              <span className="bridge-label">bridge enabled</span>
              <div className="bridge-toggle" onClick={() => onToggle(data.enabled)}>
                <div className={"bridge-toggle-track" + (data.enabled ? " on" : "")}></div>
                <div className={"bridge-toggle-thumb" + (data.enabled ? " on" : "")}></div>
              </div>
            </div>
          </div>

          {data.restart_notice && (
            <div className="bridge-footer">
              restart required for changes to take effect
            </div>
          )}

          <details className="bridge-instructions">
            <summary>how to install the extension</summary>
            <ol>
              <li>Open <code>chrome://extensions</code>, enable Developer mode</li>
              <li>Click "Load unpacked", select <code>extensions/heare-bridge/</code></li>
              <li>Enter the pair code shown above (or paste the token into the extension options)</li>
              <li>Click "Connect"</li>
            </ol>
          </details>
        </div>
      </div>
    </div>
  );
}
