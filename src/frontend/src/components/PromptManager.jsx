import React, { useState, useMemo } from "react";
import { API } from "../App";

export default function PromptManager({ prompts, selectedPrompt, editContent, preview, onSelect, onPreview, onSave, onClose, onEditContent }) {
  const [activeKey, setActiveKey] = useState(null);
  const [search, setSearch] = useState("");
  const [pmTab, setPmTab] = useState("sections");

  const filtered = useMemo(() => {
    if (!prompts) return [];
    if (!search) return prompts;
    const q = search.toLowerCase();
    return prompts.filter(p =>
      p.key.toLowerCase().includes(q) ||
      (p.template_path && p.template_path.toLowerCase().includes(q)) ||
      p.source.toLowerCase().includes(q)
    );
  }, [prompts, search]);

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card" style={{maxWidth: 640}}>
        <div className="modal-header">
          <span className="modal-title">📝 prompts</span>
          <span className="flex-row">
            <button className={"tab-btn" + (pmTab === "sections" ? " active" : "")} onClick={() => setPmTab("sections")}>📋 sections</button>
            <button className={"tab-btn" + (pmTab === "map" ? " active" : "")} onClick={() => setPmTab("map")}>🧭 map</button>
          </span>
          <button className="modal-close" onClick={onClose}>&times;</button>
        </div>
        <div className="modal-body">
          {pmTab === "sections" ? (<>
          <div className="btn-row" style={{marginBottom: 8}}>
            <button className="btn" onClick={onPreview}>preview</button>
            <button className="btn" onClick={onClose}>close</button>
          </div>

          <div className="modal-section">
            <h4>sections (ordered)</h4>
            <input
              className="modal-search"
              type="text"
              placeholder="filter sections\u2026"
              value={search}
              onInput={e => setSearch(e.target.value)}
            />
            {!filtered || filtered.length === 0 ? (
              <div className="info-line">Loading\u2026</div>
            ) : (
              filtered.map(p => (
                <div
                  key={p.key}
                  className={"prompt-section-row" + (activeKey === p.key ? " active" : "")}
                  onClick={() => { setActiveKey(p.key); onSelect(p.key); }}
                >
                  <span style={{width: 30, color: "var(--muted)", fontSize: 10}}>{p.order}</span>
                  <span style={{flex: 1, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis"}}>{p.key}</span>
                  <span className="prompt-source">{p.source}</span>
                  {p.template_path && <span className="prompt-file">{p.template_path.replace("prompts/", "")}</span>}
                  <span className="prompt-chars">{p.char_count.toLocaleString()}</span>
                </div>
              ))
            )}
          </div>

          {selectedPrompt && (
            <div className="modal-section">
              <h4>selected: {selectedPrompt.key}</h4>
              <div style={{fontSize: 10, color: "var(--muted)", marginBottom: 4}}>
                {selectedPrompt.char_count != null && selectedPrompt.char_count > 0
                  ? selectedPrompt.char_count.toLocaleString() + " chars \u00b7 " + selectedPrompt.source
                  : selectedPrompt.source
                }
                {selectedPrompt.template_path && <span> \u00b7 {selectedPrompt.template_path}</span>}
              </div>
              {selectedPrompt && selectedPrompt.source === "template" && selectedPrompt.content != null ? (
                <div>
                  <textarea
                    className="prompt-editor"
                    value={editContent}
                    onInput={e => onEditContent(e.target.value)}
                  />
                  <div className="btn-row" style={{marginTop: 6}}>
                    <button className="btn" onClick={onSave}>save</button>
                    <button className="btn" onClick={() => onEditContent(selectedPrompt.content)}>reset</button>
                  </div>
                </div>
              ) : selectedPrompt && selectedPrompt.content != null ? (
                <div className="prompt-preview" style={{maxHeight: 300, overflow: "auto"}}>
                  {selectedPrompt.content}
                </div>
              ) : selectedPrompt ? (
                <div className="info-line">{selectedPrompt.note || "(rendered at runtime \u2014 no saved content)"}</div>
              ) : null}
            </div>
          )}

          {preview !== null && (
            <div className="modal-section">
              <h4>preview \u2014 assembled system prompt</h4>
              <div className="prompt-preview">{preview}</div>
            </div>
          )}
          </>) : (<>
            <div className="map-view">

              {/* Stage 1: Input Pipeline */}
              <div className="map-stage">
                <div className="map-stage-header">
                  <strong>1 \u00b7 Input Pipeline</strong>
                </div>
                <div className="map-body">
                  <div className="map-row">
                    <span className="map-key">Capture</span>
                    <span className="map-source inline">audio</span>
                    <span className="map-file">Microphone \u2192 VAD</span>
                  </div>
                  <div className="map-row">
                    <span className="map-key">Transcribe</span>
                    <span className="map-source inline">stt</span>
                    <span className="map-file">GroqSTT (Whisper)</span>
                  </div>
                  <div className="map-row">
                    <span className="map-key">Gate</span>
                    <span className="map-source inline">filter</span>
                    <span className="map-file">transcription_gate</span>
                  </div>
                  <div className="map-row">
                    <span className="map-key">Injector</span>
                    <span className="map-source dynamic">rebuild</span>
                    <span className="map-file">context_injector.py</span>
                  </div>
                </div>
              </div>
              <div className="map-arrow">\u2193</div>

              {/* Stage 2: System Prompt Assembly */}
              <div className="map-stage">
                <div className="map-stage-header">
                  <strong>2 \u00b7 System Prompt</strong>
                  <span style={{marginLeft:"auto",fontSize:9,color:"var(--muted)"}}>render_native_system_prompt()</span>
                </div>
                <div className="map-body">
                  {Array.isArray(prompts) && prompts.length > 0 ? (
                    prompts.map(p => (
                      <div className="map-row" key={p.key}>
                        <span className="map-key">{p.key}</span>
                        <span className={"map-source " + (p.source || "inline")}>{p.source || "inline"}</span>
                        <span className="map-file">{p.template_path ? p.template_path.replace("prompts/", "") : (p.source === "dynamic" ? "per-turn" : "identity data")}</span>
                      </div>
                    ))
                  ) : (
                    <div className="info-line" style={{padding:8}}>Loading sections\u2026</div>
                  )}
                  <div className="map-row" style={{borderTop:"1px solid var(--border)",marginTop:4,paddingTop:4}}>
                    <span className="map-key">Assembled \u2192</span>
                    <span className="map-source dynamic">render</span>
                    <span className="map-file">LLM system prompt</span>
                  </div>
                </div>
              </div>
              <div className="map-arrow">\u2193</div>

              {/* Stage 3: Mode Gate */}
              <div className="map-stage">
                <div className="map-stage-header">
                  <strong>3 \u00b7 Mode Gate</strong>
                  <span style={{marginLeft:"auto",fontSize:9,color:"var(--muted)"}}>modes.py \u2192 output gating</span>
                </div>
                <div className="map-body">
                  <div className="map-decider">
                    Channel constraint: limits output, not personality
                    <div className="map-decider-options">
                      <div className="map-decider-opt nothing">🔇 silent</div>
                      <div className="map-decider-opt speak">🎯 focus</div>
                      <div className="map-decider-opt act">🌊 ambient</div>
                    </div>
                  </div>
                </div>
              </div>

            </div>
          </>)}
        </div>
      </div>
    </div>
  );
}
