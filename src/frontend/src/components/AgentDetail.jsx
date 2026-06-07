import React from "react";
import { API } from "../App";

export default function AgentDetail({ detailId, detailData, detailOutput, detailResult, loadingDetail, onClose, onRefresh }) {
  return (
    <div>
      {detailId && (
        <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
          <div className="modal-card agent-detail-modal">
            <div className="modal-header">
              <span className="modal-title">
                {"🤖"} agent {detailId.slice(0, 12)}
                {detailResult && (
                  <span className={"agent-badge " + detailResult.status} style={{marginLeft:8,verticalAlign:"middle"}}>
                    {detailResult.status}
                  </span>
                )}
              </span>
              <button className="modal-close" onClick={onClose}>{"\u00D7"}</button>
            </div>
            <div className="modal-body">
              {loadingDetail ? <div className="info-line">Loading\u2026</div> : detailData ? (
                <div>
                  <div className="info-line"><strong>prompt:</strong> {detailData.prompt}</div>
                  <div className="info-line"><strong>tools:</strong> {detailResult?.tool_calls || detailData.tool_calls || 0} {" \u00B7 "} <strong>cost:</strong> ${((detailResult?.cost || detailData.cost) || 0).toFixed(4)}</div>
                  <div className="info-line"><strong>session:</strong> {detailData.session_id}</div>
                  <div className="info-line"><strong>age:</strong> {detailData.age_seconds}s {" \u00B7 "} <strong>turn:</strong> {detailData.turn || 1}</div>
                  {detailData.current_step && <div className="info-line"><strong>step:</strong> {detailData.current_step}</div>}
                  {detailData.error_message && <div className="info-line" style={{color:"var(--accent-red)"}}><strong>error:</strong> {detailData.error_message}</div>}
                </div>
              ) : <div className="info-line">Agent not found</div>}
              {detailResult?.status !== "done" && detailOutput && (
                <div className="info-line" style={{color:"var(--accent-cyan)",marginTop:8}}>{"\u27F3"} still generating \u2014 auto-refreshing\u2026</div>
              )}
              {detailOutput ? (
                <div className="agent-output-box">{detailOutput}</div>
              ) : detailResult?.status === "running" ? (
                <div className="info-line" style={{marginTop:8}}>Waiting for output\u2026</div>
              ) : null}
              <div style={{marginTop:8,display:"flex",gap:8}}>
                <button className="btn" onClick={onClose}>close</button>
                <button className="btn" onClick={() => onRefresh(detailId)}>{"\u21BB"} refresh</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
