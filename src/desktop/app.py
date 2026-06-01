"""Heare desktop app — lightweight PyWebView window."""
import webview

HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  :root {
    --bg: #0d1117;
    --card: #161b22;
    --card-hl: #1c2128;
    --border: #21262d;
    --border-hover: #30363d;
    --text: #c9d1d9;
    --muted: #8b949e;
    --accent: #7ee787;
    --accent-red: #ff7b72;
    --accent-cyan: #79c0ff;
    --accent-orange: #ffa657;
    --accent-magenta: #d2a8ff;
    --accent-yellow: #e3b341;
    --mono: 'SF Mono','Cascadia Code','Fira Code','JetBrains Mono','Menlo','Consolas',monospace;
    --sans: -apple-system,'Segoe UI',system-ui,sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--sans);
    background: var(--bg);
    color: var(--text);
    padding: 8px;
    font-size: 13px;
    line-height: 1.5;
    min-width: 520px;
    overflow-x: hidden;
  }

  /* ── CARD ── */
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 10px;
    margin-bottom: 6px;
  }
  .card-header {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--muted);
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .card-header span.label { flex: 1; }
  .card-header span.extra { color: var(--muted); font-size: 9px; }

  /* ── STATUS BAR ── */
  #status-bar {
    border-bottom: 2px solid var(--border);
    margin-bottom: 8px;
    padding: 0;
    display: flex;
    flex-direction: column;
  }
  .status-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 3px 8px;
    flex-wrap: wrap;
  }
  .status-row + .status-row { border-top: 1px solid var(--border); }
  #status-bar .dot {
    width: 7px; height: 7px; border-radius: 50%; display: inline-block;
    flex-shrink: 0;
  }
  #status-bar .dot.on { background: var(--accent); box-shadow: 0 0 6px var(--accent); }
  #status-bar .dot.off { background: var(--accent-red); box-shadow: 0 0 4px var(--accent-red); }
  #status-bar .identity { font-weight: 700; font-size: 18px; white-space: nowrap; color: var(--text); }
  #status-bar .meta { color: var(--muted); font-size: 11px; white-space: nowrap; }
  #status-bar .meta em { font-style: normal; color: var(--accent-cyan); }
  #status-bar select {
    background: var(--card); color: var(--text); border: 1px solid var(--border);
    font-size: 10px; padding: 1px 4px; font-family: var(--mono); border-radius: 3px;
  }
  .dot.amber { background: var(--accent-yellow); box-shadow: 0 0 4px var(--accent-yellow); }
  .dot.silent { background: var(--muted); box-shadow: none; }

  /* ── 3-COLUMN ROW ── */
  .row { display: flex; gap: 6px; }
  .col { flex: 1; min-width: 0; }

  /* ── CONTROLS ── */
  #controls .btn-row { display: flex; flex-wrap: wrap; gap: 3px; margin-bottom: 4px; }
  #controls button {
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--card);
    color: var(--text);
    cursor: pointer;
    font-size: 11px;
    font-weight: 600;
    font-family: var(--sans);
    white-space: nowrap;
    transition: border-color 0.15s, background 0.15s, color 0.15s;
  }
  #controls button:hover { border-color: var(--border-hover); background: #1c2128; }
  #controls button.active { border-color: var(--accent); color: var(--accent); }
  #controls button.on { border-color: var(--accent); color: var(--accent); background: #0d1f15; }
  #btn-stop { border-color: #3a1f1f !important; }
  #btn-stop:hover { background: #2a1515; }

  /* ── RESPONSE PANEL ── */
  .response-panel { background: var(--card-hl) !important; }
  .response-panel .text {
    font-family: var(--sans);
    font-size: 16px;
    font-weight: 600;
    color: var(--accent-orange);
    max-height: 80px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.4;
  }
  .response-panel .meta { font-size: 9px; color: var(--muted); margin-top: 4px; }

  /* ── STATS PANEL (was AI panel) ── */
  #ai-panel .info-line {
    font-family: var(--mono);
    font-size: 11px;
    margin-bottom: 2px;
    color: var(--muted);
    line-height: 1.5;
  }
  #ai-panel .info-line strong { color: var(--accent-cyan); font-weight: 400; }
  #ai-panel .usage-line {
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    margin-top: 5px;
    line-height: 1.6;
    word-break: break-word;
  }
  #ai-panel .cost { color: var(--accent-yellow); }

  /* ── DISPLAY / CANVAS ── */
  #canvas {
    min-height: 120px;
    max-height: 280px;
    overflow: auto;
    padding: 8px;
    font-size: 13px;
    line-height: 1.55;
    color: var(--text);
    font-family: var(--mono);
    white-space: pre-wrap;
  }
  #canvas:empty::after {
    content: "canvas — LLM output appears here";
    color: var(--muted);
    font-style: italic;
    font-size: 10px;
  }
  #canvas img { max-width: 100%; height: auto; }
  #canvas pre {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 8px;
    overflow-x: auto;
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text);
    white-space: pre-wrap;
    word-break: break-word;
  }

  /* ── DATA TABLE ── */
  .data-table {
    width: 100%;
    border-collapse: collapse;
    font-family: var(--mono);
    font-size: 11px;
    table-layout: auto;
  }
  .data-table th {
    text-align: left;
    color: var(--muted);
    font-weight: 700;
    padding: 2px 6px;
    border-bottom: 1px solid var(--border);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .data-table td {
    padding: 2px 6px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 11px;
    line-height: 1.5;
    border-bottom: 1px solid var(--border);
  }
  .data-table .ts { width: 56px; color: var(--muted); }
  .data-table .who { width: 36px; }
  .data-table .type { width: 42px; }
  .data-table .content { overflow: hidden; text-overflow: ellipsis; }
  .data-table .who-bot { color: var(--accent-orange); }
  .data-table .who-you { color: var(--accent-cyan); }
  .data-table .log-error { color: var(--accent-red); }
  .data-table .log-warn { color: var(--accent-yellow); }

  /* ── SCROLL PANELS ── */
  .scroll-panel { max-height: 240px; overflow-y: auto; }
  .scroll-panel.short { max-height: 150px; }

  /* ── CANVAS HEADER BUTTON ── */
  .canvas-copy-btn {
    font-size: 10px;
    padding: 1px 8px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--card);
    color: var(--muted);
    cursor: pointer;
    font-family: var(--sans);
    transition: border-color 0.15s, color 0.15s;
  }
  .canvas-copy-btn:hover { border-color: var(--border-hover); color: var(--text); }

  /* ── VOICE INDICATOR ── */
  #voice-indicator {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 8px;
    font-size: 9px;
    font-family: var(--mono);
    margin-left: 4px;
  }
  #voice-indicator.listening { background: #0d1f15; color: var(--accent); }
  #voice-indicator.stt { background: #1f150d; color: var(--accent-yellow); }
  #voice-indicator.idle { background: var(--bg); color: var(--muted); }

  @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.3 } }
  #voice-dot {
    width: 8px; height: 8px; border-radius: 50%; display: inline-block;
    flex-shrink: 0; background: var(--accent); box-shadow: 0 0 6px var(--accent);
    opacity: 0; transition: opacity 0.2s;
  }
  #voice-dot.speaking { opacity: 1; animation: pulse 0.8s ease-in-out infinite; }

  /* ── SCROLLBARS ── */
  ::-webkit-scrollbar { width: 5px; height: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--border-hover); }

  /* ── TEXT INJECTION ── */
  #inject-row { display: flex; gap: 4px; align-items: center; }
  #inject-row input {
    background: var(--card);
    color: var(--text);
    border: 1px solid var(--border);
    font-size: 11px;
    font-family: var(--mono);
    padding: 3px 8px;
    flex: 1;
    border-radius: 4px;
    transition: border-color 0.15s;
  }
  #inject-row input:focus { border-color: var(--border-hover); outline: none; }
  #inject-row button {
    padding: 3px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--card);
    color: var(--text);
    cursor: pointer;
    font-size: 11px;
    font-family: var(--sans);
    white-space: nowrap;
    transition: border-color 0.15s, background 0.15s;
  }
  #inject-row button:hover { border-color: var(--border-hover); background: var(--card-hl); }
</style>
</head>
<body>

<div id="status-bar">
  <div class="status-row">
    <span id="status-dot" class="dot off"></span>
    <span class="identity"><span id="agent-name">heare</span> <span id="agent-emoji">&#x1FA76;</span></span>
    <span class="meta" id="status-text">stopped</span>
    <span class="meta" id="pid-text"></span>
    <span class="meta" id="uptime-text"></span>
  </div>
  <div class="status-row">
    <span class="meta">mode <em id="mode-text">?</em></span>
    <span class="meta">provider
      <select id="provider-select" onchange="switchProvider(this.value)">
      </select>
    </span>
    <span class="meta">model
      <select id="model-select" onchange="switchModel()">
      </select>
    </span>
    <span class="meta" id="counts-text"></span>
  </div>
  <div class="status-row">
    <span id="status-dot2" class="dot off"></span><span class="meta" id="status-label">agent</span>
    <span class="meta">mode <em id="mode-label">ambient</em></span>
    <span id="chrome-dot" class="dot silent" title="chrome bridge"></span><span class="meta" id="chrome-label">chrome</span>
  </div>
</div>

<div class="row">
  <!-- Controls column -->
  <div class="col" id="controls">
    <div class="card">
      <div class="card-header"><span class="label">controls</span></div>
      <div class="btn-row">
        <button onclick="setMode('silent')" id="btn-silent">silent</button>
        <button onclick="setMode('focus')" id="btn-focus">focus</button>
        <button onclick="setMode('ambient')" id="btn-ambient">ambient</button>
        <button onclick="setMode('assistant')" id="btn-assistant">assistant</button>
      </div>
      <div class="btn-row">
        <button onclick="toggleMute('bot')" id="btn-mute-bot">🔇 bot</button>
        <button onclick="toggleMute('mic')" id="btn-mute-mic">🔇 mic</button>
        <button onclick="cancel()" id="btn-cancel">cancel</button>
        <button onclick="daemonAction('stop')" id="btn-stop">stop</button>
      </div>
    </div>
  </div>

  <!-- Response column -->
  <div class="col">
    <div class="card response-panel">
      <div class="card-header">
        <span class="label">💬 response</span>
        <span class="extra" id="response-meta"></span>
      </div>
      <div class="text" id="response-text">—</div>
    </div>
  </div>

  <!-- Stats column (was AI panel) -->
  <div class="col" id="ai-panel">
    <div class="card">
      <div class="card-header"><span class="label">📊 stats</span></div>
      <div class="info-line">provider: <strong id="ai-provider">—</strong></div>
      <div class="info-line">model: <strong id="ai-model">—</strong></div>
      <div class="usage-line" id="usage-text">usage —</div>
    </div>
  </div>
</div>

<div class="card" id="canvas-panel">
  <div class="card-header">
    <span class="label">display</span>
    <span class="extra" id="canvas-meta"></span>
    <button onclick="copyCanvas()" class="canvas-copy-btn">copy</button>
  </div>
  <div id="canvas"></div>
</div>

<div class="card">
  <div class="card-header">
    <span class="label">activity (recent transcripts)</span>
    <span class="extra" id="activity-count"></span>
  </div>
  <div class="scroll-panel" id="activity-scroll">
    <table class="data-table">
      <thead><tr><th class="ts">time</th><th class="who">who</th><th class="type">type</th><th class="content">content</th></tr></thead>
      <tbody id="activity-body"></tbody>
    </table>
  </div>
</div>

<div class="card">
  <div class="card-header">
    <span class="label">daemon log</span>
    <span class="extra" id="log-count"></span>
  </div>
  <div class="scroll-panel short" id="log-scroll">
    <table class="data-table">
      <tbody id="log-body"></tbody>
    </table>
  </div>
</div>

<div class="card" id="inject-card">
  <div class="card-header"><span class="label">text injection</span></div>
  <div id="inject-row">
    <input id="inject-text" type="text" placeholder="type text to inject...">
    <button onclick="injectText()">send</button>
  </div>
</div>

<script>
const API = "http://127.0.0.1:9778";
let lastDisplayTs = 0;

async function pollDisplay() {
  try {
    var r = await fetch(API + "/display");
    var d = await r.json();
    if (d.content && d.ts !== lastDisplayTs) {
      lastDisplayTs = d.ts;
      var el = document.getElementById("canvas");
      var fmt = d.format || "text";
      if (fmt === "html") {
        el.innerHTML = d.content;
      } else {
        el.innerHTML = "<pre>" + d.content.replace(/</g, "&lt;").replace(/>/g, "&gt;") + "</pre>";
      }
      var meta = (d.format || "text") + (d.title ? " · " + d.title : "");
      document.getElementById("canvas-meta").textContent = meta;
    }
    if (!d.content) {
      document.getElementById("canvas").innerHTML =
        '<div style="color:var(--muted);font-style:italic;font-size:10px">LLM display output renders here</div>';
    }
  } catch(e) {}
}

function fmtTime(ts) {
  if (!ts) return "";
  var d = new Date(ts * 1000);
  return d.toTimeString().slice(0, 8);
}

function fmtTok(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}

async function pollState() {
  try {
    var r = await fetch(API + "/state");
    var s = await r.json();
    var running = s.running === true;
    var dot = document.getElementById("status-dot");
    dot.className = "dot " + (running ? "on" : "off");
    document.getElementById("status-text").textContent = running ? "running" : "stopped";
    document.getElementById("pid-text").textContent = s.pid ? "pid=" + s.pid : "";
    document.getElementById("uptime-text").textContent = s.uptime || "";
    document.getElementById("agent-name").textContent = s.agent || "heare";
    document.getElementById("agent-emoji").textContent = s.emoji || "";
    document.getElementById("mode-text").textContent = s.mode || "?";
    var tc = s.transcripts_count || 0;
    var ac = s.actions_count || 0;
    document.getElementById("counts-text").textContent = tc + " msgs | " + ac + " actions";

    document.getElementById("ai-provider").textContent = s.provider || "—";
    document.getElementById("ai-model").textContent = s.model || "—";

    if (s.usage) {
      var u = s.usage;
      document.getElementById("usage-text").innerHTML =
        "llm: " + u.llm_calls + " calls, " + fmtTok(u.llm_input_tokens || 0) + "/" + fmtTok(u.llm_output_tokens || 0) + " tok, $" + (u.llm_cost_usd || 0).toFixed(4) +
        " | stt: " + (u.stt_calls || 0) + " calls, " + (u.stt_audio_seconds || 0).toFixed(0) + "s, $" + (u.stt_cost_usd || 0).toFixed(4) +
        " | tts: " + (u.tts_calls || 0) + " calls, " + fmtTok(u.tts_char_count || 0) + " ch, $" + (u.tts_cost_usd || 0).toFixed(4);
    }

    var cd = document.getElementById("chrome-dot");
    cd.className = "dot " + (s.chrome ? "amber" : "silent");
    cd.title = s.chrome ? "chrome bridge connected" : "chrome bridge offline";
    var cl = document.getElementById("chrome-label");
    if (cl) cl.textContent = s.chrome ? "chrome" : "chrome ✗";

    var d2 = document.getElementById("status-dot2");
    if (d2) d2.className = "dot " + (running ? "on" : "off");
    var sl = document.getElementById("status-label");
    if (sl) sl.textContent = running ? "alive" : "dead";
    var ml = document.getElementById("mode-label");
    if (ml) ml.textContent = s.mode || "ambient";

    var vi = document.getElementById("voice-indicator");
    var vd = document.getElementById("voice-dot");
    if (s.voice_state) {
      vi.textContent = s.voice_state.state || "idle";
      vi.className = s.voice_state.state || "idle";
      if (s.voice_state.last_partial) {
        vi.textContent += ": " + s.voice_state.last_partial.slice(0, 24);
      }
      var vs = s.voice_state || {};
      var speaking = vs.state === "speaking" || vs.state === "stt" || vs.state === "listening";
      vd.className = speaking ? "speaking" : "";
    } else {
      vi.textContent = "idle";
      vi.className = "idle";
      vd.className = "";
    }

    if (s.last_response) {
      document.getElementById("response-text").textContent = s.last_response;
      document.getElementById("response-meta").textContent = (s.last_response_mode || "") + " · " + (s.last_response ? s.last_response.length + " chars" : "");
    }

    if (s.providers && s.providers.length) {
      var sel = document.getElementById("provider-select");
      var cur = s.provider || "";
      var opts = "";
      for (var i = 0; i < s.providers.length; i++) {
        var p = s.providers[i];
        opts += '<option value="' + p + '"' + (p === cur ? " selected" : "") + '>' + p + '</option>';
      }
      sel.innerHTML = opts;
    }

    if (s.models && s.models.length) {
      var msel = document.getElementById("model-select");
      var mcur = s.model || "";
      var mopts = "";
      for (var j = 0; j < s.models.length; j++) {
        var m = s.models[j];
        mopts += '<option value="' + m + '"' + (m === mcur ? " selected" : "") + '>' + m + '</option>';
      }
      msel.innerHTML = mopts;
    }

    var micOn = s.mute_mic === "1" || s.mute_mic === true;
    var botOn = s.mute_bot === "1" || s.mute_bot === true;
    document.getElementById("btn-mute-mic").textContent = micOn ? "🔊 mic" : "🔇 mic";
    document.getElementById("btn-mute-mic").className = micOn ? "on" : "";
    document.getElementById("btn-mute-bot").textContent = botOn ? "🔊 bot" : "🔇 bot";
    document.getElementById("btn-mute-bot").className = botOn ? "on" : "";

    var mode = s.mode || "";
    ["silent", "focus", "ambient", "assistant"].forEach(function(m) {
      var btn = document.getElementById("btn-" + m);
      if (btn) btn.className = (mode === m) ? "active" : "";
    });
  } catch(e) {}
}

async function pollActivity() {
  try {
    var r = await fetch(API + "/activity");
    var rows = await r.json();
    if (!rows || !rows.length) {
      document.getElementById("activity-body").innerHTML = '<tr><td colspan="4" style="color:var(--muted)">(no activity)</td></tr>';
      document.getElementById("activity-count").textContent = "";
      return;
    }
    document.getElementById("activity-count").textContent = rows.length + " rows";
    var html = "";
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var wc = row.who === "bot" ? "who-bot" : "who-you";
      var content = (row.content || "").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      html += "<tr>" +
        "<td class='ts'>" + fmtTime(row.ts) + "</td>" +
        "<td class='who " + wc + "'>" + row.who + "</td>" +
        "<td class='type'>" + row.type + "</td>" +
        "<td class='content'>" + content + "</td></tr>";
    }
    document.getElementById("activity-body").innerHTML = html;
  } catch(e) {}
}

async function pollLogs() {
  try {
    var r = await fetch(API + "/logs");
    var data = await r.json();
    var lines = data.lines || [];
    document.getElementById("log-count").textContent = lines.length + " lines";
    if (!lines.length) {
      document.getElementById("log-body").innerHTML = '<tr><td style="color:var(--muted)">(no log lines)</td></tr>';
      return;
    }
    var html = "";
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var cls = "";
      var u = line.toUpperCase();
      if (u.indexOf("ERROR") !== -1 || u.indexOf("CRITICAL") !== -1 || u.indexOf("ERR ") !== -1) cls = "log-error";
      else if (u.indexOf("WARN") !== -1) cls = "log-warn";
      html += "<tr><td class='" + cls + "' style='white-space:pre-wrap;word-break:break-all'>" +
        line.replace(/</g, "&lt;").replace(/>/g, "&gt;") + "</td></tr>";
    }
    document.getElementById("log-body").innerHTML = html;
  } catch(e) {}
}

function pollAll() {
  pollState();
  pollDisplay();
  pollActivity();
  pollLogs();
}
setInterval(pollAll, 800);
pollAll();

async function copyCanvas() {
  var el = document.getElementById("canvas");
  try {
    await navigator.clipboard.writeText(el.innerText || el.innerHTML);
  } catch(e) {
    var ta = document.createElement("textarea");
    ta.value = el.innerText || el.innerHTML;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  }
}

async function toggleMute(target) {
  await fetch(API + "/mute", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({target:target})});
  pollState();
}
async function setMode(m) {
  await fetch(API + "/mode", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({mode:m})});
  pollState();
}
async function cancel() {
  await fetch(API + "/cancel", {method:"POST"});
}
async function switchProvider(p) {
  await fetch(API + "/provider", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({provider:p})});
  pollState();
}
async function switchModel() {
  var m = document.getElementById("model-select").value;
  await fetch(API + "/model", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({model:m})});
  pollState();
}
async function daemonAction(action) {
  var r = await fetch(API + "/daemon", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action:action})});
  var d = await r.json();
  if (d.ok) document.getElementById("status-text").textContent = d.action;
}
async function injectText() {
  var el = document.getElementById("inject-text");
  var text = el.value.trim();
  if (!text) return;
  await fetch(API + "/inject", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({text:text})});
  el.value = "";
}
</script>
</body>
</html>
"""


def run():
    window = webview.create_window("heare", html=HTML, width=620, height=800, resizable=True)
    webview.start()
