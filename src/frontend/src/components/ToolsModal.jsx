import React, { useState } from "react";
import { API } from "../App";

// ═══ Helper: infer built-in tool category from name ═══
function inferBuiltInCategory(name) {
  const n = name || "";
  // file & shell — direct file ops and web access
  if (/^(bash|read|write|web_search|web_fetch|create_archive|extract_archive|batch_operation)$/.test(n))
    return {key:"file", label:"file & shell", icon:"🗂"};
  // browser — any tool that touches the browser
  if (/browser/.test(n))
    return {key:"browser", label:"browser", icon:"🌐"};
  // ui-output — tools that push content to the display/canvas widget
  if (/^(show_text|show_canvas)$/.test(n))
    return {key:"ui-output", label:"ui output", icon:"📺"};
  // agent — mode, config, capabilities, favorites, views
  if (/^(set_(mode|provider|view_preference)|show_profile|discover_capability|install_(skill|mcp)_tool|list_capabilities|revoke_capability|add_favorite|list_favorites|cancel)$/.test(n))
    return {key:"agent", label:"agent", icon:"🤖"};
  // daemon — process lifecycle
  if (/_daemon$/.test(n))
    return {key:"daemon", label:"daemon", icon:"⚙"};
  // skill — agent skills lifecycle
  if (/_skill/.test(n))
    return {key:"skill", label:"skill", icon:"🧩"};
  // tooling — MCP + dynamic tool management
  if (/^(list_(mcp_servers|tools)|create_tool|update_tool|delete_tool|register_mcp_server)$/.test(n))
    return {key:"tooling", label:"tooling", icon:"🔧"};
  // anything else
  return {key:"other", label:"other", icon:"📦"};
}

// ═══ Helper: the MCP servers that actually connected ═══
//
// The "mcps" list below is what .mcp.json asks for. `mcp_status` (from the
// engine's State key, passed through /api/tools) is what came up — the two
// disagree exactly when something is wrong, and until now the modal only
// ever showed the wish list.
function mcpStatusLine(status) {
  if (!status) {
    // The pipecat engine never writes the key. Silence is not an outage.
    return { text: "connection status unknown — this engine does not report it", cls: "off" };
  }
  const servers = Array.isArray(status.servers) ? status.servers : [];
  const tools = Number(status.tools) || 0;
  const error = status.error || "";
  if (!status.ok && error === "off") {
    return { text: "MCP is off — no servers started, no external tools for the agent", cls: "off" };
  }
  if (!status.ok) {
    return { text: "MCP failed: " + (error || "did not connect"), cls: "err" };
  }
  if (servers.length === 0) {
    return { text: "connected, but no server is configured in .mcp.json", cls: "off" };
  }
  return {
    text: "connected: " + servers.join(", ") + " — " + tools + " tool" + (tools === 1 ? "" : "s"),
    cls: "ok",
  };
}

export default function ToolsModal({ data, onClose }) {
  const [searchQuery, setSearchQuery] = useState("");
  const [collapsedSections, setCollapsedSections] = useState(new Set());
  const [collapsedSubgroups, setCollapsedSubgroups] = useState(new Set());

  if (!data) {
    return (
      <div className="modal-overlay" onClick={function(e) { if (e.target === e.currentTarget) onClose(); }}>
        <div className="modal-card">
          <div className="modal-header">
            <span className="modal-title">tools</span>
            <button className="modal-close" onClick={onClose}>&times;</button>
          </div>
          <div className="modal-body">
            <div className="info-line">Loading…</div>
          </div>
        </div>
      </div>
    );
  }

  const searchActive = searchQuery.trim().length > 0;
  function itemMatches(item) {
    if (!searchActive) return true;
    const q = searchQuery.toLowerCase();
    return (item.name || "").toLowerCase().indexOf(q) !== -1 ||
           (item.description || "").toLowerCase().indexOf(q) !== -1;
  }

  const builtInAll = data.built_in || [];
  const skillsAll  = data.skills || [];
  const mcpsAll    = data.mcps || [];

  const filteredBI = searchActive ? builtInAll.filter(itemMatches) : builtInAll;
  const filteredSK = searchActive ? skillsAll.filter(itemMatches) : skillsAll;
  const filteredMC = searchActive ? mcpsAll.filter(itemMatches) : mcpsAll;
  const totalMatches = filteredBI.length + filteredSK.length + filteredMC.length;

  const BI_CATEGORY_ORDER = ["file", "browser", "ui-output", "agent", "daemon", "skill", "tooling", "other"];
  const biGroups = {};
  filteredBI.forEach(function(t) {
    const cat = inferBuiltInCategory(t.name);
    if (!biGroups[cat.key]) biGroups[cat.key] = {icon:cat.icon, label:cat.label, items:[]};
    biGroups[cat.key].items.push(t);
  });

  const bundledSkills   = filteredSK.filter(function(s) { return !s.installed_via_discovery; });
  const discoverySkills = filteredSK.filter(function(s) { return s.installed_via_discovery; });

  const activeMcps   = filteredMC.filter(function(m) { return !m.disabled; });
  const disabledMcps = filteredMC.filter(function(m) { return m.disabled; });

  function secExpanded(name) {
    return searchActive ? true : !collapsedSections.has(name);
  }
  function subExpanded(key) {
    return searchActive ? true : !collapsedSubgroups.has(key);
  }
  function toggleSection(name) {
    if (searchActive) return;
    setCollapsedSections(function(prev) {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  }
  function toggleSubgroup(key) {
    if (searchActive) return;
    setCollapsedSubgroups(function(prev) {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  const anyCollapsed = collapsedSections.size > 0 || collapsedSubgroups.size > 0;
  function toggleAll() {
    if (searchActive) return;
    if (anyCollapsed) {
      setCollapsedSections(new Set());
      setCollapsedSubgroups(new Set());
    } else {
      const newSec = new Set(["built-in","skills","mcps"]);
      setCollapsedSections(newSec);
      const allSubKeys = new Set();
      BI_CATEGORY_ORDER.forEach(function(k) { if (biGroups[k]) allSubKeys.add("bi:"+k); });
      if (bundledSkills.length > 0) allSubKeys.add("sk:bundled");
      if (discoverySkills.length > 0) allSubKeys.add("sk:discovery");
      if (activeMcps.length > 0) allSubKeys.add("mc:active");
      if (disabledMcps.length > 0) allSubKeys.add("mc:disabled");
      setCollapsedSubgroups(allSubKeys);
    }
  }

  function hasAnySubgroupMatches(section) {
    if (section === "built-in") {
      return Object.keys(biGroups).length > 0;
    }
    if (section === "skills") {
      return bundledSkills.length > 0 || discoverySkills.length > 0;
    }
    if (section === "mcps") {
      return activeMcps.length > 0 || disabledMcps.length > 0;
    }
    return false;
  }

  function renderItem(t, i) {
    return (
      <div key={i} className="modal-item">
        <span className="tool-name">{t.name || t}</span>
        {t.description && <div className="tool-desc">{t.description}</div>}
      </div>
    );
  }

  function renderSubgroup(key, icon, label, items) {
    if (items.length === 0) return null;
    const expanded = subExpanded(key);
    return (
      <div className="modal-subgroup" key={key}>
        <div
          className={"modal-subgroup-header" + (expanded ? " expanded" : "")}
          onClick={function() { toggleSubgroup(key); }}
        >
          <span className="sg-chevron">{expanded ? "▾" : "▸"}</span>
          <span className="sg-icon">{icon}</span>
          <span className="sg-label">{label}</span>
          <span className="sg-badge">{items.length}</span>
        </div>
        {expanded && (
          <div className="modal-subgroup-content">
            {items.map(renderItem)}
          </div>
        )}
      </div>
    );
  }

  function renderSection(name, title, count, renderSubgroupsFn) {
    // When searching, hide empty sections entirely
    if (searchActive && !hasAnySubgroupMatches(name)) return null;
    const expanded = secExpanded(name);
    return (
      <div className="modal-section" key={name}>
        <div
          className={"modal-section-hdr" + (expanded ? " expanded" : "")}
          onClick={function() { toggleSection(name); }}
        >
          <span className="ms-chevron">{expanded ? "▾" : "▸"}</span>
          <h4>
            {title}
            <span className="modal-section-count">{count}</span>
          </h4>
        </div>
        {expanded && (
          <div className="modal-section-body">
            {renderSubgroupsFn()}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="modal-overlay" onClick={function(e) { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card modal-card-wide">
        <div className="modal-header">
          <span className="modal-title">tools ({data.built_in ? data.built_in.length : 0} built-in, {data.skills ? data.skills.length : 0} skills, {data.mcps ? data.mcps.length : 0} mcps)</span>
          <div className="flex-row">
            {!searchActive && (
              <button className="btn" style={{fontSize:10,padding:"2px 6px"}} onClick={toggleAll}>
                {anyCollapsed ? "expand all" : "collapse all"}
              </button>
            )}
            <button className="modal-close" onClick={onClose}>&times;</button>
          </div>
        </div>
        <div className="modal-body">
          {data && data.error && (
            <div className="info-line" style={{color:"var(--accent-yellow)",marginBottom:6}}>{data.error}</div>
          )}
          {(function() {
            const line = mcpStatusLine(data.mcp_status);
            return (
              <div className={"mcp-status-line mcp-" + line.cls}>
                <span className="mcp-status-dot" aria-hidden="true"></span>
                <span>{line.text}</span>
              </div>
            );
          })()}
          <input
            type="text"
            className="modal-search"
            placeholder="search by name or description\u2026"
            value={searchQuery}
            onChange={function(e) { setSearchQuery(e.target.value); }}
            autoFocus
          />
          {searchActive && (
            <div className="modal-match-count">
              {totalMatches} item{totalMatches !== 1 ? "s" : ""} across {[filteredBI.length > 0, filteredSK.length > 0, filteredMC.length > 0].filter(Boolean).length} section{filteredBI.length + filteredSK.length + filteredMC.length !== 0 && [filteredBI.length > 0, filteredSK.length > 0, filteredMC.length > 0].filter(Boolean).length !== 1 ? "s" : ""}
            </div>
          )}

          {/* built-in */}
          {(!searchActive || filteredBI.length > 0) && renderSection("built-in", "built-in", builtInAll.length, function() {
            return BI_CATEGORY_ORDER.map(function(catKey) {
              const g = biGroups[catKey];
              if (!g || g.items.length === 0) return null;
              return renderSubgroup("bi:" + catKey, g.icon, g.label, g.items);
            });
          })}

          {/* skills */}
          {(!searchActive || filteredSK.length > 0) && renderSection("skills", "skills", skillsAll.length, function() {
            return (
              <div>
                {renderSubgroup("sk:bundled", "📦", "bundled", bundledSkills)}
                {renderSubgroup("sk:discovery", "\u2B07", "discovery-installed", discoverySkills)}
              </div>
            );
          })}

          {/* mcps */}
          {(!searchActive || filteredMC.length > 0) && renderSection("mcps", "mcps", mcpsAll.length, function() {
            return (
              <div>
                {renderSubgroup("mc:active", "🟢", "active", activeMcps)}
                {renderSubgroup("mc:disabled", "🔴", "disabled", disabledMcps)}
              </div>
            );
          })}

          {totalMatches === 0 && (
            <div className="info-line" style={{textAlign:"center",marginTop:12}}>no tools match "{searchQuery}"</div>
          )}
        </div>
      </div>
    </div>
  );
}
