"""Heare desktop app — lightweight PyWebView window."""
import webview

HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 12px; }
  .header { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
  .name { font-size: 18px; font-weight: bold; }
  .status { font-size: 12px; padding: 2px 8px; border-radius: 10px; }
  .status.active { background: #0f0; color: #000; }
  .controls { display: flex; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
  button { padding: 4px 12px; border: 1px solid #444; border-radius: 4px; background: #2a2a4a; color: #ccc; cursor: pointer; font-size: 12px; }
  button:hover { background: #3a3a5a; }
  button.on { background: #4a4a; border-color: #0f0; color: #0f0; }
  .canvas { border: 1px dashed #444; border-radius: 6px; min-height: 120px; padding: 10px; margin-bottom: 10px; background: #0d0d1a; overflow: auto; }
  .canvas:empty::after { content: "canvas — LLM output appears here"; color: #555; font-style: italic; }
  .activity { font-size: 11px; color: #888; max-height: 80px; overflow-y: auto; }
  .activity .user { color: #69f; }
  .activity .bot { color: #f96; }
</style>
</head>
<body>
  <div class="header">
    <span class="name" id="name">—</span>
    <span class="status" id="status">•</span>
    <span id="mode" style="font-size:12px;color:#888"></span>
    <span id="provider" style="font-size:12px;color:#888"></span>
  </div>
  <div class="controls">
    <button id="mute-mic" onclick="toggleMute('mic')">🔇 mic</button>
    <button id="mute-bot" onclick="toggleMute('bot')">🔇 bot</button>
    <button onclick="setMode('silent')">silent</button>
    <button onclick="setMode('focus')">focus</button>
    <button onclick="setMode('ambient')">ambient</button>
    <button onclick="setMode('assistant')">assistant</button>
    <button onclick="cancel()">✕ cancel</button>
  </div>
  <div class="canvas" id="canvas"></div>
  <div class="activity" id="activity"></div>

<script>
const API = "http://127.0.0.1:9778";
let lastCanvasTs = 0;

async function poll() {
  try {
    const r = await fetch(API + "/state");
    const s = await r.json();
    const running = !!s.running || (s.mode && s.provider);
    document.getElementById("name").textContent = (s.agent||"heare") + " " + (s.emoji||"");
    document.getElementById("status").className = "status " + (running ? "active" : "");
    document.getElementById("status").textContent = running ? "active" : "offline";
    document.getElementById("mode").textContent = "mode: " + (s.mode||"?");
    document.getElementById("provider").textContent = " | " + (s.provider||"?");
    const micOn = s.mute_mic === "1" || s.mute_mic === true;
    const botOn = s.mute_bot === "1" || s.mute_bot === true;
    document.getElementById("mute-mic").textContent = micOn ? "🔇 mic" : "🔈 mic";
    document.getElementById("mute-mic").className = micOn ? "on" : "";
    document.getElementById("mute-bot").textContent = botOn ? "🔇 bot" : "🔈 bot";
    document.getElementById("mute-bot").className = botOn ? "on" : "";
  } catch(e) {}

  try {
    const r = await fetch(API + "/canvas");
    const c = await r.json();
    if (c.html && c.ts !== lastCanvasTs) {
      lastCanvasTs = c.ts;
      document.getElementById("canvas").innerHTML = c.html;
    }
  } catch(e) {}
}
setInterval(poll, 500); poll();

async function toggleMute(target) {
  await fetch(API + "/mute", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({target:target})});
  poll();
}
async function setMode(m) {
  await fetch(API + "/mode", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({mode:m})});
  poll();
}
async function cancel() {
  await fetch(API + "/cancel", {method:"POST"});
}
</script>
</body>
</html>
"""


def run():
    window = webview.create_window("heare", html=HTML, width=420, height=520, resizable=True)
    webview.start()
