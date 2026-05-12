// Overlay frontend — polls /api/state every 200ms and renders.
// Pauses polling when the window is hidden (visibilitychange).
const POLL_MS = 200;

const $ = (id) => document.getElementById(id);

const els = {
  statusPill: $("status-pill"),
  statusLabel: $("status-label"),
  transcript: $("transcript"),
  btnMic: $("btn-mic"),
  btnOut: $("btn-out"),
  btnCancel: $("btn-cancel"),
  dbgAudio: $("dbg-audio"),
  dbgState: $("dbg-state"),
  dbgPartial: $("dbg-partial"),
};

let pollTimer = null;
let lastFinal = null;

async function fetchState() {
  try {
    const r = await fetch("/api/state");
    if (!r.ok) return null;
    return await r.json();
  } catch (_) {
    return null;
  }
}

function setPill(state) {
  els.statusPill.className = "pill " + state;
  els.statusLabel.textContent = state;
}

function setActive(btn, active) {
  btn.classList.toggle("active", !!active);
}

function renderTranscript(voiceState) {
  if (!voiceState) {
    els.transcript.innerHTML = '<div class="hint">Waiting for daemon…</div>';
    return;
  }
  const finalText = voiceState.last_final || null;
  const partial = voiceState.last_partial || null;
  // Append a new final line only when it changes.
  if (finalText && finalText !== lastFinal) {
    lastFinal = finalText;
    const node = document.createElement("div");
    node.className = "final";
    node.textContent = finalText;
    els.transcript.appendChild(node);
    els.transcript.scrollTop = els.transcript.scrollHeight;
  }
  // Render the current partial as a single trailing line (replaced each tick).
  let partialNode = els.transcript.querySelector(".partial");
  if (partial) {
    if (!partialNode) {
      partialNode = document.createElement("div");
      partialNode.className = "partial";
      els.transcript.appendChild(partialNode);
    }
    partialNode.textContent = partial;
    els.transcript.scrollTop = els.transcript.scrollHeight;
  } else if (partialNode) {
    partialNode.remove();
  }
  // Remove the boot hint once anything real has arrived.
  const hint = els.transcript.querySelector(".hint");
  if (hint && (finalText || partial)) hint.remove();
}

function render(state) {
  if (!state) {
    setPill("offline");
    renderTranscript(null);
    setActive(els.btnMic, false);
    setActive(els.btnOut, false);
    els.dbgAudio.textContent = "—";
    els.dbgState.textContent = "—";
    els.dbgPartial.textContent = "—";
    return;
  }
  const online = state.daemon_online;
  const vs = state.voice_state || {};
  const pillState = online ? (vs.state || "idle") : "offline";
  setPill(pillState);
  setActive(els.btnMic, state.mute_input);
  setActive(els.btnOut, state.mute_output);
  renderTranscript(vs);
  const ae = state.audio_event;
  els.dbgAudio.textContent = ae ? `${ae.label} (${(ae.score ?? 0).toFixed(2)})` : "—";
  els.dbgState.textContent = vs.state || "—";
  els.dbgPartial.textContent = vs.last_partial || "—";
}

async function tick() {
  const state = await fetchState();
  render(state);
}

function startPolling() {
  if (pollTimer != null) return;
  tick();
  pollTimer = setInterval(tick, POLL_MS);
}

function stopPolling() {
  if (pollTimer == null) return;
  clearInterval(pollTimer);
  pollTimer = null;
}

async function postJson(path, body) {
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : "{}",
    });
    return r.ok ? await r.json() : null;
  } catch (_) {
    return null;
  }
}

els.btnMic.addEventListener("click", async () => {
  const next = !els.btnMic.classList.contains("active");
  setActive(els.btnMic, next); // optimistic
  await postJson("/api/mute/input", { on: next });
  tick();
});

els.btnOut.addEventListener("click", async () => {
  const next = !els.btnOut.classList.contains("active");
  setActive(els.btnOut, next);
  await postJson("/api/mute/output", { on: next });
  tick();
});

els.btnCancel.addEventListener("click", async () => {
  await postJson("/api/cancel");
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPolling();
  else startPolling();
});

startPolling();
