import React, { useState, useEffect } from 'react';
import { API } from '../App';

// ── AgentLauncher ──
export function AgentLauncher({ onLaunch, onToast }) {
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleStart() {
    if (!prompt.trim()) return;
    if (onLaunch) {
      setLoading(true);
      try {
        await onLaunch(prompt.trim());
        setPrompt("");
      } catch (e) {
        if (onToast) onToast("failed: " + e.message, "err");
      }
      setLoading(false);
    } else {
      // Fallback: direct API call
      setLoading(true);
      try {
        const r = await fetch(API + "/api/agents/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: prompt.trim() }),
        });
        const d = await r.json();
        if (d.session_id) {
          if (onToast) onToast("agent started", "ok");
          setPrompt("");
        } else {
          if (onToast) onToast("failed: " + (d.error || "unknown"), "err");
        }
      } catch (e) {
        if (onToast) onToast("failed: " + e.message, "err");
      }
      setLoading(false);
    }
  }

  return (
    <div className="agent-launch-row">
      <input
        type="text" value={prompt}
        onInput={e => setPrompt(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter") handleStart(); }}
        placeholder={"agent prompt\u2026"}
      />
      <button className="btn" onClick={handleStart} disabled={loading} style={{flexShrink: 0}}>
        {loading ? "\u2026" : "\u25b6 start"}
      </button>
    </div>
  );
}

// ── AgentList ──
export function AgentList({ agents, onCancel, onToast, onDetail }) {
  const [detailId, setDetailId] = useState(null);
  const [detailData, setDetailData] = useState(null);
  const [detailOutput, setDetailOutput] = useState(null);
  const [detailResult, setDetailResult] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Auto-refresh detail when agent is running
  useEffect(() => {
    if (!detailId) return;
    if (!detailResult) return;
    const active = detailResult.status === "running" || detailResult.status === "starting" || detailResult.status === "waiting_for_input";
    if (!active) return;
    const interval = setInterval(() => loadOutput(detailId), 2000);
    return () => clearInterval(interval);
  }, [detailId, detailResult]);

  async function loadDetail(sid) {
    setDetailId(sid);
    setLoadingDetail(true);
    setDetailOutput(null);
    setDetailResult(null);
    try {
      const [ar, rr] = await Promise.all([
        fetch(API + "/api/agents"),
        fetch(API + "/api/agents/" + sid + "/result"),
      ]);
      const ad = await ar.json();
      const rd = await rr.json();
      const agent = (ad.agents || []).find(a => a.session_id === sid);
      setDetailData(agent || null);
      setDetailResult(rd);
      if (rd && rd.output) {
        setDetailOutput(rd.output);
      }
    } catch (e) {}
    setLoadingDetail(false);

    if (onDetail) onDetail(sid);
  }

  async function loadOutput(sid) {
    try {
      const r = await fetch(API + "/api/agents/" + sid + "/result");
      const d = await r.json();
      setDetailResult(d);
      if (d && d.output && d.output !== detailOutput) {
        setDetailOutput(d.output);
      }
    } catch (e) {}
  }

  function handleCancel(sid) {
    if (onCancel) {
      onCancel(sid);
    }
  }

  if (!agents || agents.length === 0) {
    return <div className="info-line" style={{padding: "8px 0"}}>No agents. Launch one above.</div>;
  }

  const statusIcon = (s) => ({running: "\u2699", starting: "\u27f3", waiting_for_input: "\u23f8", done: "\u2713", error: "\u2717", cancelled: "\u2717"}[s] || "\u2022");
  const statusLabel = (s) => ({running: "running", starting: "booting", waiting_for_input: "waiting", done: "done", error: "failed", cancelled: "stopped"}[s] || s);

  return (
    <div>
      {agents.slice().reverse().map(a => {
        const active = a.status === "running" || a.status === "starting" || a.status === "waiting_for_input";
        const icon = statusIcon(a.status);
        const label = statusLabel(a.status);
        const prompt = (a.prompt || "").slice(0, 80);
        return (
          <div key={a.session_id} className="agent-row">
            <div className="agent-row-main">
              <span className={"agent-dot " + a.status}>{icon}</span>
              <div className="agent-row-body">
                <div className="agent-row-head">
                  <span className={"agent-badge " + a.status}>{label}</span>
                  {a.current_step && <span className="agent-step" title={a.current_step}>{a.current_step}</span>}
                  <span className="agent-sid" title={a.session_id}>{a.session_id.slice(0, 12)}</span>
                </div>
                <div className="agent-prompt-text" title={a.prompt}>{prompt || "(no prompt)"}</div>
                {(a.tool_calls > 0 || a.cost != null) && (
                  <div className="agent-metrics">
                    {a.tool_calls > 0 && <span className="agent-metric">{a.tool_calls} tools</span>}
                    {a.cost != null && a.cost > 0 && <span className="agent-metric">${a.cost.toFixed(4)}</span>}
                    <span className="agent-metric">turn {a.turn || 1}</span>
                    <span className="agent-metric">{a.age_seconds}s</span>
                  </div>
                )}
              </div>
              <div className="agent-actions">
                <button className="btn btn-tiny" onClick={() => loadDetail(a.session_id)} title="details">{'\ud83d\udccb'}</button>
                {active && (
                  <button className="btn btn-tiny danger" onClick={() => handleCancel(a.session_id)} title="cancel">{'\u2715'}</button>
                )}
              </div>
            </div>
            {a.partial_output && (
              <div className="agent-row-preview" onClick={() => loadDetail(a.session_id)} style={{cursor: "pointer"}} title="Click for full output">
                {a.partial_output}
              </div>
            )}
          </div>
        );
      })}

      {/* Detail modal */}
      {detailId && (
        <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) setDetailId(null); }}>
          <div className="modal-card agent-detail-modal">
            <div className="modal-header">
              <span className="modal-title">
                {'\ud83e\udd16'} agent {detailId.slice(0, 12)}
                {detailResult && (
                  <span className={"agent-badge " + detailResult.status} style={{marginLeft: 8, verticalAlign: "middle"}}>
                    {detailResult.status}
                  </span>
                )}
              </span>
              <button className="modal-close" onClick={() => setDetailId(null)}>{'\u00d7'}</button>
            </div>
            <div className="modal-body">
              {loadingDetail ? <div className="info-line">{'Loading\u2026'}</div> : detailData ? (
                <div>
                  <div className="info-line"><strong>prompt:</strong> {detailData.prompt}</div>
                  <div className="info-line">
                    <strong>tools:</strong> {detailResult?.tool_calls || detailData.tool_calls || 0} {'\u00b7'}{" "}
                    <strong>cost:</strong> ${((detailResult?.cost || detailData.cost) || 0).toFixed(4)}
                  </div>
                  <div className="info-line"><strong>session:</strong> {detailData.session_id}</div>
                  <div className="info-line"><strong>age:</strong> {detailData.age_seconds}s {'\u00b7'} <strong>turn:</strong> {detailData.turn || 1}</div>
                  {detailData.current_step && <div className="info-line"><strong>step:</strong> {detailData.current_step}</div>}
                  {detailData.error_message && <div className="info-line" style={{color: "var(--accent-red)"}}><strong>error:</strong> {detailData.error_message}</div>}
                </div>
              ) : <div className="info-line">Agent not found</div>}
              {detailResult?.status !== "done" && detailOutput && (
                <div className="info-line" style={{color: "var(--accent-cyan)", marginTop: 8}}>{'\u27f3 still generating \u2014 auto-refreshing\u2026'}</div>
              )}
              {detailOutput ? (
                <div className="agent-output-box">{detailOutput}</div>
              ) : detailResult?.status === "running" ? (
                <div className="info-line" style={{marginTop: 8}}>Waiting for output\u2026</div>
              ) : null}
              <div style={{marginTop: 8, display: "flex", gap: 8}}>
                <button className="btn" onClick={() => setDetailId(null)}>close</button>
                <button className="btn" onClick={() => loadOutput(detailId)}>{'\u21bb'} refresh</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── AgentsPanel ──
export default function AgentsPanel({ agents, onLaunch, onCancel, onDetail, onToast }) {
  return (
    <div>
      <AgentLauncher onLaunch={onLaunch} onToast={onToast} />
      <AgentList agents={agents} onCancel={onCancel} onDetail={onDetail} onToast={onToast} />
    </div>
  );
}
