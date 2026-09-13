import React, { useState, useMemo } from "react";
import { API } from "../App";

export default function PromptManager({ prompts, selectedPrompt, editContent, preview, usage, onSelect, onPreview, onSave, onClose, onEditContent }) {
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

          {usage && (
            <div className="modal-section" style={{
              background: "var(--card)", border: "1px solid var(--border)",
              borderRadius: "var(--r)", padding: "var(--s3)", marginBottom: "var(--s3)"
            }}>
              <div style={{fontSize:11,fontWeight:600,marginBottom:6}}>📊 token usage</div>
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:4,fontSize:10}}>
                <div style={{color:"var(--muted)"}}>Total calls</div>
                <div style={{textAlign:"right"}}>{usage.llm_calls?.toLocaleString() || "—"}</div>
                <div style={{color:"var(--muted)"}}>Input tokens / call</div>
                <div style={{textAlign:"right"}}>
                  {usage.llm_calls
                    ? (usage.llm_input_tokens / usage.llm_calls).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")
                    : "—"}
                </div>
                <div style={{color:"var(--muted)"}}>Output tokens / call</div>
                <div style={{textAlign:"right"}}>
                  {usage.llm_calls
                    ? (usage.llm_output_tokens / usage.llm_calls).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")
                    : "—"}
                </div>
                <div style={{color:"var(--muted)"}}>~Tokens / call</div>
                <div style={{textAlign:"right",fontWeight:600,color:"var(--accent)"}}>
                  {usage.llm_calls
                    ? Math.round((usage.llm_input_tokens + usage.llm_output_tokens) / usage.llm_calls).toLocaleString()
                    : "—"}
                </div>
                {usage.total_cost_usd > 0 && (<>
                  <div style={{color:"var(--muted)"}}>Total cost</div>
                  <div style={{textAlign:"right"}}>${usage.total_cost_usd.toFixed(4)}</div></>
                )}
              </div>
            </div>
          )}

          <div className="modal-section">
            <h4>sections (ordered)</h4>
            <input
              className="modal-search"
              type="text"
              placeholder="filter sections…"
              value={search}
              onInput={e => setSearch(e.target.value)}
            />
            {!filtered || filtered.length === 0 ? (
              <div className="info-line">Loading…</div>
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
                  ? selectedPrompt.char_count.toLocaleString() + " chars · " + selectedPrompt.source
                  : selectedPrompt.source
                }
                {selectedPrompt.template_path && <span> · {selectedPrompt.template_path}</span>}
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
                <div className="info-line">{selectedPrompt.note || "(rendered at runtime — no saved content)"}</div>
              ) : null}
            </div>
          )}

          {preview !== null && (
            <div className="modal-section">
              <h4>preview — assembled system prompt</h4>
              <div className="prompt-preview">{preview}</div>
            </div>
          )}
          </>) : (<>
            <div className="map-view">

              {/* 1 · the ear: sound becomes a turn, outside the loop */}
              <div className="map-stage">
                <div className="map-stage-header">
                  <strong>1 · Вухо</strong>
                  <span style={{marginLeft:"auto",fontSize:9,color:"var(--muted)"}}>src/spine — поза петлею</span>
                </div>
                <div className="map-body">
                  <div className="map-row">
                    <span className="map-key">мікрофон</span>
                    <span className="map-source inline">audio</span>
                    <span className="map-file">audio_io → aec (не чути себе) → vad</span>
                  </div>
                  <div className="map-row">
                    <span className="map-key">розпізнавання</span>
                    <span className="map-source inline">stt</span>
                    <span className="map-file">Groq Whisper → фільтр галюцинацій</span>
                  </div>
                  <div className="map-row">
                    <span className="map-key">збирач ходу</span>
                    <span className="map-source inline">turn</span>
                    <span className="map-file">скінчились слова, не час → Turn(mic)</span>
                  </div>
                  <div className="map-row">
                    <span className="map-key">ворота</span>
                    <span className="map-source inline">wake</span>
                    <span className="map-file">до мене? — інакше в пам’ять, не в петлю</span>
                  </div>
                </div>
              </div>
              <div className="map-arrow">↓</div>

              {/* 2 · the system prompt, as build_system_prompt assembles it */}
              <div className="map-stage">
                <div className="map-stage-header">
                  <strong>2 · Системний промпт</strong>
                  <span style={{marginLeft:"auto",fontSize:9,color:"var(--muted)"}}>src/spine/prompt.py · build_system_prompt()</span>
                </div>
                <div className="map-body">
                  {Array.isArray(prompts) && prompts.length > 0 ? (
                    prompts.map(p => (
                      <div className="map-row" key={p.key}>
                        <span className="map-key">{p.key}</span>
                        <span className={"map-source " + (p.source || "inline")}>{p.source || "inline"}</span>
                        <span className="map-file">{p.template_path ? p.template_path.replace("prompts/", "") : (p.note || "")}</span>
                      </div>
                    ))
                  ) : (
                    <div className="info-line" style={{padding:8}}>Loading sections…</div>
                  )}
                  <div className="map-row" style={{borderTop:"1px solid var(--border)",marginTop:4,paddingTop:4}}>
                    <span className="map-key">статичне → динамічне</span>
                    <span className="map-source dynamic">кеш</span>
                    <span className="map-file">незмінне спереду, годинник останнім — щоб кеш префікса влучав</span>
                  </div>
                </div>
              </div>
              <div className="map-arrow">↓</div>

              {/* 3 · the core decides how, the model decides what */}
              <div className="map-stage">
                <div className="map-stage-header">
                  <strong>3 · Ядро й виходи</strong>
                  <span style={{marginLeft:"auto",fontSize:9,color:"var(--muted)"}}>loop.respond() → Рот</span>
                </div>
                <div className="map-body">
                  <div className="map-decider">
                    Модель вирішує, що сказати. Ядро — як донести: за роллю й вимикачами
                    <div className="map-decider-options">
                      <div className="map-decider-opt speak">🗣 голос — Рот</div>
                      <div className="map-decider-opt act">📝 текст — дашборд</div>
                      <div className="map-decider-opt nothing">🔇 лог · підказки — тихі ролі</div>
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
