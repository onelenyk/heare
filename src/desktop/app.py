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
    --bg: #1a1a2e;
    --card: #1e1e35;
    --border: #333;
    --text: #c0c0c0;
    --muted: #666;
    --accent: #00ff88;
    --accent-red: #ff4444;
    --accent-cyan: #00d4ff;
    --accent-orange: #ff8c42;
    --accent-magenta: #e040fb;
    --accent-yellow: #ffd740;
    --mono: 'SF Mono','Cascadia Code','Fira Code','JetBrains Mono','Menlo','Consolas',monospace;
    --sans: -apple-system,'Segoe UI',system-ui,sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--sans);
    background: var(--bg);
    color: var(--text);
    padding: 10px;
    font-size: 14px;
    line-height: 1.4;
    min-width: 400px;
    overflow-x: hidden;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 10px;
    margin-bottom: 6px;
  }
  .card-header {
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--muted);
    margin-bottom: 5px;
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .card-header span.label { flex: 1; }
  .card-header span.extra { color: var(--muted); font-size: 11px; }

  #status-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 5px 10px;
    border-bottom: 2px solid var(--border);
    margin-bottom: 6px;
    flex-wrap: wrap;
  }
  #status-bar .dot {
    width: 8px; height: 8px; border-radius: 50%; display: inline-block;
    flex-shrink: 0;
  }
  #status-bar .dot.on { background: var(--accent); box-shadow: 0 0 6px var(--accent); }
  #status-bar .dot.off { background: var(--accent-red); box-shadow: 0 0 4px var(--accent-red); }
  #status-bar .identity { font-weight: 700; font-size: 20px; white-space: nowrap; }
  #status-bar .meta { color: var(--muted); font-size: 13px; white-space: nowrap; }
  #status-bar .meta em { font-style: normal; color: var(--accent-cyan); }

  .row { display: flex; gap: 6px; }
  .col { flex: 1; min-width: 0; }
  .col-wide { flex: 2; }

  #controls .btn-row { display: flex; flex-wrap: wrap; gap: 3px; margin-bottom: 4px; }
  #controls button {
    padding: 5px 14px;
    border: 1px solid var(--border);
    border-radius: 3px;
    background: var(--bg);
    color: var(--text);
    cursor: pointer;
    font-size: 12px;
    font-weight: 600;
    font-family: var(--mono);
    white-space: nowrap;
    transition: border-color 0.15s, background 0.15s;
  }
  #controls button:hover { border-color: var(--accent-cyan); background: #252540; }
  #controls button.active { border-color: var(--accent); color: var(--accent); }
  #controls button.on { border-color: var(--accent); color: var(--accent); background: #0a2a1a; }

  #ai-panel .info-line {
    font-family: var(--mono); font-size: 11px; margin-bottom: 3px; color: var(--muted);
  }
  #ai-panel .info-line strong { color: var(--accent-cyan); font-weight: 400; }
  #ai-panel .usage-line { font-family: var(--mono); font-size: 10px; color: var(--muted); margin-top: 4px; }
  #ai-panel .cost { color: var(--accent-yellow); }

  #response-panel .text {
    font-family: var(--mono); font-size: 15px; font-weight: 600; color: var(--accent-orange);
    max-height: 60px; overflow-y: auto; white-space: pre-wrap; word-break: break-word;
  }
  #response-panel { padding: 12px; }
  #response-panel .meta { font-size: 9px; color: var(--muted); margin-top: 3px; }

  #canvas {
    min-height: 160px; max-height: 220px; overflow: auto;
    padding: 6px; font-size: 14px; line-height: 1.5;
  }
  #canvas:empty::after {
    content: "canvas — LLM output appears here";
    color: var(--muted); font-style: italic; font-size: 10px;
  }
  #canvas img { max-width: 100%; height: auto; }
  #canvas pre {
    background: #0d0d1a; border: 1px solid var(--border);
    border-radius: 4px; padding: 6px; overflow-x: auto;
    font-family: var(--mono); font-size: 10px; color: var(--text);
  }

  .data-table {
    width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 10px;
    table-layout: auto;
  }
  .data-table th {
    text-align: left; color: var(--muted); font-weight: 700; padding: 3px 8px;
    border-bottom: 1px solid #2a2a44; font-size: 11px; text-transform: uppercase;
  }
  .data-table td {
    padding: 3px 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    font-size: 12px; line-height: 1.5;
    border-bottom: 1px solid #1f1f35;
  }
  .data-table .ts { width: 58px; color: var(--muted); }
  .data-table .who { width: 40px; }
  .data-table .type { width: 46px; }
  .data-table .content { overflow: hidden; text-overflow: ellipsis; }
  .data-table .who-bot { color: var(--accent-orange); }
  .data-table .who-you { color: var(--accent-cyan); }
  .data-table .log-error { color: var(--accent-red); }
  .data-table .log-warn { color: var(--accent-yellow); }

  .scroll-panel { max-height: 120px; overflow-y: auto; }
  .scroll-panel.short { max-height: 80px; }

  #voice-indicator {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 9px; font-family: var(--mono); margin-left: 6px;
  }
   #voice-indicator.listening { background: #0a2a1a; color: var(--accent); }
  #voice-indicator.stt { background: #2a1a0a; color: var(--accent-yellow); }
  #voice-indicator.idle { background: #1a1a2e; color: var(--muted); }

  @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.3 } }
  #voice-dot {
    width: 10px; height: 10px; border-radius: 50%; display: inline-block;
    flex-shrink: 0; background: var(--accent); box-shadow: 0 0 8px var(--accent);
    opacity: 0; transition: opacity 0.2s;
  }
  #voice-dot.speaking { opacity: 1; animation: pulse 0.8s ease-in-out infinite; }

  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }
</style>
</head>
<body>

<div id="status-bar">
  <span id="status-dot" class="dot off"></span>
  <span class="identity"><span id="agent-name">heare</span> <span id="agent-emoji">&#x1FA76;</span></span>
  <span class="meta" id="status-text">stopped</span>
  <span class="meta" id="pid-text"></span>
  <span class="meta" id="uptime-text"></span>
  <span style="flex:1"></span>
  <span class="meta">mode <em id="mode-text">?</em></span>
  <span class="meta">provider
    <select id="provider-select" onchange="switchProvider(this.value)"
      style="background:#1e1e35;color:#c0c0c0;border:1px solid #333;font-size:11px;padding:2px">
    </select>
  </span>
  <span class="meta" id="counts-text"></span>
  <span id="voice-dot"></span>
  <span id="voice-indicator" class="idle">idle</span>
</div>

<div class="row">
  <div class="col" id="controls">
    <div class="card">
      <div class="card-header"><span class="label">controls</span></div>
      <div class="btn-row">
        <button onclick="setMode('silent')" id="btn-silent">silent</button>
        <button onclick="setMode('focus')" id="btn-focus">focus</button>
        <button onclick="setMode('ambient')" id="btn-ambient">ambient</button>
      </div>
      <div class="btn-row">
        <button onclick="toggleMute('bot')" id="btn-mute-bot">mute bot</button>
        <button onclick="toggleMute('mic')" id="btn-mute-mic">mute mic</button>
        <button onclick="cancel()" id="btn-cancel">cancel</button>
      </div>
    </div>
  </div>

  <div class="col col-wide" id="ai-panel">
    <div class="card">
      <div class="card-header"><span class="label">AI</span></div>
      <div class="info-line">provider: <strong id="ai-provider">—</strong></div>
      <div class="info-line">model: <strong id="ai-model">—</strong></div>
      <div class="usage-line" id="usage-text">usage —</div>
    </div>
  </div>
</div>

<div class="card" id="response-panel">
  <div class="card-header">
    <span class="label">latest response</span>
    <span class="extra" id="response-meta"></span>
  </div>
  <div class="text" id="response-text">—</div>
</div>

<div class="card" id="canvas-panel">
   <div class="card-header">
    <span class="label">display / canvas</span>
    <span class="extra" id="canvas-meta"></span>
    <button onclick="copyCanvas()" style="font-size:10px;padding:1px 6px;border:1px solid var(--border);border-radius:3px;background:var(--bg);color:var(--text);cursor:pointer">copy</button>
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

<script>
const API = "http://127.0.0.1:9778";
let lastCanvasTs = 0;

function fmtTime(ts) {
  if (!ts) return "";
  var d = new Date(ts * 1000);
  return d.toTimeString().slice(0, 8);
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
        "llm " + u.llm_calls + " calls | $" + (u.llm_cost_usd || 0).toFixed(4) +
        " &nbsp; stt " + (u.stt_audio_seconds || 0).toFixed(1) + "s";
    }

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

    var micOn = s.mute_mic === "1" || s.mute_mic === true;
    var botOn = s.mute_bot === "1" || s.mute_bot === true;
    document.getElementById("btn-mute-mic").textContent = micOn ? "unmute mic" : "mute mic";
    document.getElementById("btn-mute-mic").className = micOn ? "on" : "";
    document.getElementById("btn-mute-bot").textContent = botOn ? "unmute bot" : "mute bot";
    document.getElementById("btn-mute-bot").className = botOn ? "on" : "";

    var mode = s.mode || "";
    ["silent", "focus", "ambient"].forEach(function(m) {
      var btn = document.getElementById("btn-" + m);
      if (btn) btn.className = (mode === m) ? "active" : "";
    });
  } catch(e) {}
}

async function pollCanvas() {
  try {
    var r = await fetch(API + "/canvas");
    var c = await r.json();
    if (c.html && c.ts !== lastCanvasTs) {
      lastCanvasTs = c.ts;
      document.getElementById("canvas").innerHTML = c.html;
      document.getElementById("canvas-meta").textContent = c.ts ? fmtTime(c.ts) : "";
    }
    if (!c.html && !document.getElementById("canvas").innerHTML) {
      document.getElementById("canvas").innerHTML =
        '<div style="color:var(--muted);font-style:italic;font-size:10px">LLM [canvas] output renders here</div>';
    }
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
  pollCanvas();
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
</script>
</body>
</html>
"""


def run():
    window = webview.create_window("heare", html=HTML, width=620, height=800, resizable=True)
    webview.start()
