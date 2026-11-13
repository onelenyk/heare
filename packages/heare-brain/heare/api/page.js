const LOCALE = document.documentElement.lang === "uk" ? "uk-UA" : "en-US";
const $ = id => document.getElementById(id);
const narrow = matchMedia("(max-width:900px)");
const DEMO = new URLSearchParams(location.search).get("demo") === "1";
const api = (path, opts = {}) => fetch("/api/" + path, {
  ...opts, credentials: "same-origin",
  headers: {"Content-Type": "application/json"},
  body: opts.body ? JSON.stringify(opts.body) : undefined,
}).then(r => {
  if (r.status === 401) { showPairing(); return Promise.reject("pair"); }
  return r.ok ? r.json() : r.text().then(t => Promise.reject(t));
});

function showPairing() {
  if (DEMO) return;
  const p = $("pairing");
  if (p) { p.hidden = false; setTimeout(() => $("paircode").focus(), 30); }
}
const webName = () => {
  const ua = navigator.userAgent;
  return /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome"
    : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : tr("browser");
};
$("pairform").addEventListener("submit", async ev => {
  ev.preventDefault();
  const code = $("paircode").value.trim().toUpperCase();
  $("pairerror").textContent = "";
  try {
    const answer = await fetch("/api/pair", {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({code, kind: "window", name: webName()}),
    });
    if (answer.ok) { location.reload(); return; }
    $("pairerror").textContent = answer.status === 429
      ? tr("too many tries — wait a minute")
      : tr("the code is wrong or expired — take a new one: heare-brain code");
  } catch {
    $("pairerror").textContent = tr("the brain does not answer");
  }
});
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

const NOW = {idle: tr("waiting"), hearing: tr("listening to you"), thinking: tr("thinking"), speaking: tr("speaking")};
const VIA = {voice: tr("by voice"), text: tr("by text"), screen: ""};
const WHY = {busy: tr("was busy"), "chose silence": tr("chose not to answer"), playing: tr("something is playing, and no one called it")};
let voice = null, phase = "idle", state = null, writing = null, askedAt = null;
const activity = {what: "", at: 0};
function noteActivity(what) {
  if (!what) return;
  activity.what = String(what);
  activity.at = Date.now();
  drawLast();
}
function drawLast() {
  const el = $("lasttext");
  if (!el) return;
  if (!activity.what) {
    el.textContent = tr("last") + " · " + ($("up") ? $("up").textContent : "");
    return;
  }
  const mins = Math.max(0, Math.round((Date.now() - activity.at) / 60000));
  const when = mins < 1 ? tr("now") : tr("{m} min", {m: mins});
  el.textContent = tr("last") + " · " + activity.what + " · " + when;
}
let aloud = localStorage.getItem("heare.aloud") === "1", webPid = null;
const speeds = [];
const stepLog = new Map();   // task id -> {request, steps: [{executor, step}, …]}

/* ── the conversation ── */
function feedAppend(node) {
  const empty = $("flow").querySelector(".feed-empty");
  if (empty) empty.remove();
  const hold = $("hold");
  if (hold && hold.parentNode === $("flow")) $("flow").insertBefore(node, hold);
  else $("flow").append(node);
  feedToEnd();
  return node;
}
function feedToEnd() { const f = $("feed"); if (f) f.scrollTop = f.scrollHeight; }
function feedClear() {
  const hold = $("hold");
  $("flow").replaceChildren();
  if (hold) $("flow").append(hold);
}
function feedEmpty() {
  const box = el("div", "feed-empty");
  const face = el("div", "emptyface");
  if ($("face")) face.innerHTML = $("face").outerHTML.replace(' id="face"', "").replace('class="face', 'class="face');
  const svg = face.querySelector("svg");
  if (svg) svg.setAttribute("class", "face");
  box.append(face, el("b", "", tr("Nothing here yet")), el("span", "", tr("Say something, or ask for a screen.")));
  feedAppend(box);
}
let pinState = { trail: [], shelf: [], all: [] };
const THUMB = {text: "¶", html: "▤", table: "▤", weather: "☀", image: "▣",
  audio: "♫", video: "▶", link: "↗", ask: "?", step: "·", game: "◉", pin: "⌖"};
let previewTimer = 0;
let previewCube = null;
let toastTimer = 0;
function toast(text) {
  const t = $("toast");
  t.textContent = text; t.hidden = false;
  t.classList.remove("on");
  requestAnimationFrame(() => t.classList.add("on"));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    t.classList.remove("on");
    setTimeout(() => { t.hidden = true; }, 200);
  }, 2400);
}
function dockMeasure() {
  const chat = $("chat"), dock = $("pin-dock"), composer = $("composer");
  if (!chat || !dock || !composer) return;
  chat.style.setProperty("--dock-h", dock.offsetHeight + "px");
  chat.style.setProperty("--comp-h", composer.offsetHeight + "px");
}
const PIN_TICO = {
  weather: `<svg class="tico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>`,
  table: `<svg class="tico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11M15 9v11"/></svg>`,
  audio: `<svg class="tico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/></svg>`,
  video: `<svg class="tico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="14" height="14" rx="2"/><path d="M17 10l4-2v8l-4-2"/></svg>`,
  image: `<svg class="tico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-6 6"/></svg>`,
  link: `<svg class="tico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/></svg>`,
  game: `<svg class="tico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M12 3 21 8v8l-9 5-9-5V8Z"/><path d="M3 8l9 5 9-5M12 13v8"/></svg>`,
  text: `<svg class="tico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 6h14M5 10h14M5 14h9M5 18h6"/></svg>`,
};
function pinImage(p) {
  if (p.kind !== "image" && !(p.artifact && /^image\//.test(String(p.artifact.mime || "")))) return "";
  if (p.src) return p.src;
  const mid = p.message || p.mid;
  const node = mid ? $("flow").querySelector(`[data-mid="${mid}"]`) : null;
  const img = node && node.querySelector(".ph img, img.msg-media");
  if (img) return img.src;
  const a = p.artifact || {};
  return a.file && p.session
    ? `/api/sessions/${encodeURIComponent(p.session)}/files/${encodeURIComponent(a.file)}`
    : "";
}
function pinChip(p) {
  const mid = p.message || p.mid;
  const chip = el("button", "pinchip" + (p.shelf ? " shelf" : "") + (p.missing ? " missing" : ""));
  chip.type = "button"; chip.dataset.mid = mid;
  const thumb = el("span", "pinchip-thumb");
  const picture = pinImage(p);
  if (picture) {
    const img = el("img"); img.src = picture; img.alt = ""; img.loading = "lazy";
    thumb.append(img);
  } else if (p.thumbHtml) thumb.innerHTML = p.thumbHtml;
  else thumb.innerHTML = PIN_TICO[p.kind] || PIN_TICO[p.kind === "html" ? "table" : "text"];
  const text = el("span", "pinchip-text");
  text.append(el("b", "", p.missing ? tr("Message unavailable") : p.title),
              el("small", "", p.line || ""));
  const x = el("span", "pinchip-x", "×");
  x.setAttribute("role", "button"); x.setAttribute("tabindex", "0");
  x.setAttribute("aria-label", tr("Unpin"));
  x.onclick = async e => { e.stopPropagation(); await togglePin(mid); };
  chip.append(thumb, text, x);
  chip.onclick = () => goToPin(p);
  chip.onmouseenter = () => previewShow(p, chip);
  chip.onmouseleave = previewHide;
  chip.onfocus = () => previewShow(p, chip);
  chip.onblur = previewHide;
  return chip;
}
function messageFor(p) {
  const mid = p.message || p.mid;
  const node = mid ? $("flow").querySelector(`[data-mid="${mid}"]`) : null;
  if (node) { const body = node.querySelector(".content"); return body ? {html: body.innerHTML} : null; }
  const demo = DEMO_FEED.find(x => x.id === mid);
  return demo && demo.html ? demo : null;
}
async function goToPin(p) {
  const mid = p.message || p.mid;
  if (p.session && p.session !== session) {
    try {
      await api(`sessions/${p.session}/open`, {method: "POST"});
      await loadSessions();
      loadPins();
      requestAnimationFrame(() => {
        const m = $("flow").querySelector(`[data-mid="${mid}"]`);
        if (m) m.scrollIntoView({behavior: "smooth", block: "center"});
      });
    } catch { toast(tr("Wait for the reply to end.")); }
    return;
  }
  const m = $("flow").querySelector(`[data-mid="${mid}"]`);
  if (m) { m.scrollIntoView({behavior: "smooth", block: "center"}); m.classList.add("flash"); setTimeout(() => m.classList.remove("flash"), 1200); }
  else toast(tr("another conversation"));
}
async function togglePin(mid) {
  if (DEMO) {
    const all = [...(pinState.trail || []), ...(pinState.shelf || [])];
    const found = all.find(p => (p.message || p.mid) === mid);
    if (found) {
      pinState.trail = (pinState.trail || []).filter(p => p !== found);
      pinState.shelf = (pinState.shelf || []).filter(p => p !== found);
      toast(tr("unpinned"));
    } else {
      const m = DEMO_FEED.find(x => x.id === mid) || {};
      const kind = m.kind === "html" ? (m.artifact || {}).label || "html" : (m.kind || "text");
      const title = m.title || (m.artifact || {}).title || String(m.text || "").slice(0, 46) || tr("Message");
      pinState.trail = [...(pinState.trail || []), {mid, kind, title, line: m.time || ""}];
      toast(tr("pinned"));
    }
    const node = $("flow").querySelector(`[data-mid="${mid}"]`);
    if (node) {
      const on = !found;
      node.classList.toggle("pinned", on);
      const b = node.querySelector(".pinbtn");
      if (b) { b.classList.toggle("on", on); b.title = on ? tr("Unpin") : tr("Pin"); }
    }
    dockRender();
    return;
  }
  const found = (pinState.all || []).find(p => (p.message || p.mid) === mid);
  try {
    if (found) { await api(`pins/${found.id}`, {method: "DELETE"}); toast(tr("unpinned")); }
    else { await api("pins", {method: "POST", body: {message: mid}}); toast(tr("pinned")); }
  } catch { toast(tr("could not pin")); return; }
  const node = $("flow").querySelector(`[data-mid="${mid}"]`);
  if (node) {
    node.classList.toggle("pinned", !found);
    const b = node.querySelector(".pinbtn");
    if (b) b.classList.toggle("on", !found);
  }
  await loadPins();
}
function passedDock(mid) {
  const feed = $("feed"), dock = $("pin-dock");
  const node = mid ? $("flow").querySelector(`[data-mid="${mid}"]`) : null;
  if (!node || !feed || !dock) return false;
  const dockH = dock.offsetHeight || 0;
  return node.getBoundingClientRect().bottom <= feed.getBoundingClientRect().top + dockH + 8;
}
function arrangePins() {
  if (DEMO) return;
  const all = pinState.all || [];
  pinState.trail = all.filter(p => p.session === session && !p.missing && passedDock(p.message));
  pinState.shelf = all.filter(p => p.session !== session || p.missing);
  dockRender();
}
async function loadPins() {
  if (DEMO) { pinState = DEMO_PINS; dockRender(); return; }
  try {
    const got = await api("pins");
    pinState = {trail: [], shelf: [], all: got.pins || []};
    arrangePins();
  } catch { }
}
const KIND_LABEL = {
  weather: () => tr("Weather"), table: () => tr("Table"), game: () => tr("3D"),
  image: () => tr("Photo"), audio: () => tr("Audio"), video: () => tr("Video"),
  link: () => tr("Link"), html: () => tr("Card"), card: () => tr("Card"),
};
const kindLabel = kind => (KIND_LABEL[kind] ? KIND_LABEL[kind]() : tr("Message"));
function previewShow(p, chip) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => {
    const pv = $("pinpreview");
    const m = messageFor(p);
    const head = el("div", "pv-head");
    head.append(el("span", "", kindLabel(p.kind) || tr("Pinned")),
                el("b", "", p.missing ? tr("Message unavailable") : p.title));
    const body = el("div", "pv-body");
    const picture = !m && pinImage(p);
    body.innerHTML = m ? m.html
      : picture
        ? `<figure class="artifact photo"><div class="ph"><img src="${escapeHtml(picture)}" alt=""></div>` +
          `${p.line ? `<figcaption class="cap">${escapeHtml(p.line)}</figcaption>` : ""}</figure>`
        : `<p>${tr("This message is not loaded.")}</p>`;
    const hint = el("div", "pv-hint", tr("Go to the message"));
    pv.replaceChildren(head, body, hint); pv.hidden = false;
    const card = body.querySelector(".game");
    if (card) {
      card.querySelector(".gameover")?.remove();
      const canvas = card.querySelector("canvas");
      if (canvas) {
        previewCube = {card, canvas, ctx: canvas.getContext("2d"), playing: true, angle: .7};
        cubeGames.push(previewCube);
        requestAnimationFrame(() => drawCube(previewCube));
      }
    }
    hint.onclick = () => { previewHide(); goToPin(p); };
    const r = chip.getBoundingClientRect(), w = pv.offsetWidth, h = pv.offsetHeight;
    pv.style.left = Math.min(Math.max(12, r.left), innerWidth - w - 12) + "px";
    pv.style.top = Math.min(r.bottom + 8, innerHeight - h - 12) + "px";
  }, 120);
}
function previewHide() {
  clearTimeout(previewTimer);
  if (previewCube) {
    cubeGames = cubeGames.filter(g => g !== previewCube);
    previewCube = null;
  }
  $("pinpreview").hidden = true;
}
function applyPins() {
  $("flow").querySelectorAll(".msg").forEach(node => {
    node.classList.toggle("pinned", isPinned(node.dataset.mid));
  });
}
function dockRender() {
  const dock = $("pin-dock");
  if (!dock) return;
  applyPins();
  const pins = [...pinState.trail, ...pinState.shelf];
  dock.replaceChildren(...(pins.length ? pins.map(pinChip) : [el("div", "dock-empty", tr("Pin a message — it appears here when you scroll past it."))]));
  dockMeasure();
}
dockRender();
let dockFrame = 0;
$("feed")?.addEventListener("scroll", () => {
  if (DEMO || dockFrame) return;
  dockFrame = requestAnimationFrame(() => { dockFrame = 0; arrangePins(); });
}, {passive: true});

const clock = at => {
  if (!at) return "";
  try { return new Date(at * 1000).toLocaleTimeString(LOCALE, {hour: "2-digit", minute: "2-digit"}); }
  catch { return ""; }
};
const escapeHtml = text => String(text ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
function onMessage(e) {
  if (e.session_id !== session) return;
  if (e.op === "add" && e.message) {
    if (writing) { writing.remove(); writing = null; }
    feedAppend(chipped(turnMsg(e.message), e.message.text, e.session_id, e.message.files || []));
    return;
  }
  const node = e.id ? $("flow").querySelector(`[data-mid="${e.id}"]`) : null;
  if (!node) return;
  if (e.op === "withdraw" || (e.patch && e.patch.status === "withdrawn")) { node.remove(); return; }
  if (e.patch && "width" in e.patch) node.classList.toggle("wide", e.patch.width === "w");
}
function turnMsg(t) {
  return msgEl({
    id: t.id, session_id: t.session_id, who: t.role === "user" ? "me" : "it",
    time: clock(t.at), kind: t.kind, artifact: t.artifact, text: t.text,
    html: t.kind === "html" && t.html ? t.html : `<p>${escapeHtml(t.text)}</p>`,
    wide: t.width === "w",
  });
}

/* What a message is, drawn by its kind: a card, a link, a file — the
   sketch's shapes, so a reply reads the same in the demo and in life. */
const PIN_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 3h6l-.8 5.1 3.3 3.2V13H6.5v-1.7l3.3-3.2L9 3Z"/><path d="M12 13v8"/></svg>`;
const LABELS = {
  weather: () => tr("Weather"), table: () => tr("Table"), game: () => tr("3D"),
  card: () => tr("Card"), canvas: () => tr("Card"), app: () => tr("App"),
};
const labelOf = a => (LABELS[a.label] ? LABELS[a.label]() : (a.label ? String(a.label) : tr("Card")));
function artHead(label, title) {
  return `<div class="art-head"><span>${escapeHtml(label)}</span>${title ? `<b>${escapeHtml(title)}</b>` : ""}</div>`;
}
function fileUrl(m) {
  const a = m.artifact || {};
  if (m.src) return m.src;
  return m.session_id && a.file
    ? `/api/sessions/${encodeURIComponent(m.session_id)}/files/${encodeURIComponent(a.file)}`
    : "";
}
function waveBars() {
  return Array.from({length: 64}, (_, i) => {
    const h = 5 + Math.round(Math.abs(Math.sin(i * .71)) * 17 + Math.abs(Math.cos(i * .29)) * 6);
    return `<i style="--h:${h}px;--d:${(i % 9) * .05}s"></i>`;
  }).join("");
}
function audioCard(m, file) {
  const a = m.artifact || {};
  return `<div class="artifact audio">${artHead(tr("Audio"), m.title || a.title || a.name || "")}` +
    `<div class="art-body"><div class="aud"><button type="button" class="pcircle" data-audio aria-label="${tr("play")}">▶</button>` +
    `<div class="audmain"><div class="audtop"><b>${escapeHtml(m.title || a.title || a.name || tr("Audio"))}</b><small data-dur>—</small></div>` +
    `<div class="wave">${waveBars()}</div><div class="prog"><i></i></div></div>` +
    `<audio class="afile" preload="metadata" src="${escapeHtml(file)}"></audio></div></div></div>`;
}
function videoCard(m, file) {
  const a = m.artifact || {};
  return `<div class="artifact vid">${artHead(tr("Video"), m.title || a.title || a.name || "")}` +
    `<div class="poster">${m.poster ? `<img class="vfilm" src="${escapeHtml(m.poster)}" alt="">` : `<video preload="metadata" playsinline src="${escapeHtml(file)}"></video>`}` +
    `<button type="button" class="pcircle" data-video aria-label="${tr("play")}">▶</button>` +
    `<span class="vlabel">${escapeHtml(m.vlabel || a.name || m.title || "")}</span><span class="vtime" data-dur>${escapeHtml(m.vtime || "—")}</span>` +
    `<div class="vprog"><i></i></div></div></div>`;
}
function linkCard(a) {
  const title = a.title || a.url || "";
  const host = a.host || (() => { try { return new URL(a.url).hostname; } catch { return ""; } })();
  return `<div class="artifact">${artHead(tr("Link"))}` +
    `<a class="linkcard" href="${escapeHtml(a.url || "#")}" target="_blank" rel="noopener noreferrer">` +
    `<span class="licon">${escapeHtml(((/[0-9]/.test((host || "").trim()[0] || "") ? (title || "?").trim()[0] : (host || "").trim()[0]) || "?").toLowerCase())}</span>` +
    `<span class="lmain"><span class="ltitle">${escapeHtml(title)}</span><span class="lurl">${escapeHtml(host)}</span></span>` +
    `<span class="larr">↗</span></a></div>`;
}
function imageCard(m, file) {
  const a = m.artifact || {};
  return `<figure class="artifact photo">${artHead(tr("Photo"), m.title || a.title || a.name || "")}` +
    `<div class="ph"><img src="${escapeHtml(file)}" alt="${escapeHtml(a.name || "")}" loading="lazy"></div>` +
    `${a.line || m.caption ? `<figcaption class="cap">${escapeHtml(a.line || m.caption || "")}</figcaption>` : ""}</figure>`;
}
function gameCard(m) {
  return `<div class="artifact game"><div class="art-head"><span>${tr("3D")}</span><b>${escapeHtml(m.title || tr("3D"))}</b>` +
    `<span class="spacer"></span><button type="button" class="widebtn" data-wide title="${tr("full width")}" aria-label="${tr("full width")}">⤢</button></div>` +
    `<div class="stage"><canvas></canvas><div class="gameover"><span class="glabel">${tr("a little game")}</span>` +
    `<button type="button" class="pcircle" data-play aria-label="${tr("play")}">▶</button>` +
    `<span class="glabel">${tr("mouse control")}</span></div></div>` +
    `<div class="gfoot"><i class="gdot"></i><span>WebGL · ${tr("pause off screen")}</span><span class="gright">${tr("state kept")}</span></div></div>`;
}
function renderContent(m) {
  const a = m.artifact || {};
  const file = fileUrl(m);
  const lead = m.lead ? `<div class="say">${m.lead}</div>` : "";
  if (m.kind === "step") return `<div class="step"><i class="sdot"></i>${escapeHtml(m.text)}</div>`;
  if (m.kind === "ask") {
    return `<div class="ask"><div class="atitle"><i></i>${escapeHtml(m.title || tr("HANDS ASK FOR PERMISSION"))}</div>` +
      `<p>${m.text}</p><div class="acts"><button type="button" class="btn primary" data-ask="yes">${tr("Allow")}</button>` +
      `<button type="button" class="btn" data-ask="no">${tr("Not now")}</button></div></div>`;
  }
  if (m.kind === "link" && a.url) return lead + linkCard(a);
  if (m.kind === "image" && file) return lead + imageCard(m, file);
  if (m.kind === "audio" && file) return lead + audioCard(m, file);
  if (m.kind === "video" && file) return lead + videoCard(m, file);
  if (m.kind === "game") return lead + gameCard(m);
  if (m.kind === "html") {
    let html = m.html || "";
    let title = m.title || a.title || "";
    const found = html.match(/^\s*<h([23])[^>]*>([\s\S]*?)<\/h\1>\s*/i);
    if (found) {
      if (!title) title = found[2].replace(/<[^>]+>/g, "").trim();
      html = html.slice(found[0].length);
    }
    const live = /<script[\s>]|<style[\s>]/i.test(html);
    const inner = live
      ? `<div class="art-body livebody"><iframe class="liveframe" sandbox="allow-scripts" title="${escapeHtml(title)}" ` +
        `srcdoc="${escapeHtml(framed(html, "m-" + String(m.id || "live").replace(/[^a-z0-9_-]+/gi, "-")))}"></iframe></div>`
      : `<div class="art-body">${html}</div>`;
    const head = live
      ? `<div class="art-head"><span>${escapeHtml(labelOf(a))}</span><b>${escapeHtml(title)}</b><span class="spacer"></span>` +
        `<button type="button" class="widebtn" data-live-full title="${tr("full screen")}" aria-label="${tr("full screen")}">⤢</button></div>`
      : artHead(labelOf(a), title);
    return lead + `<div class="artifact">${head}${inner}</div>`;
  }
  if (m.who === "me") return `<div class="bubble">${escapeHtml(m.text || "")}</div>`;
  return `<div class="say">${m.html || `<p>${escapeHtml(m.text || "")}</p>`}</div>`;
}
function isPinned(mid) {
  const all = pinState.all || [...(pinState.trail || []), ...(pinState.shelf || [])];
  return all.some(p => (p.message || p.mid) === mid);
}
function pinButton(m) {
  const b = el("button", "pinbtn" + (isPinned(m.id) ? " on" : ""));
  b.type = "button"; b.title = tr("Pin"); b.setAttribute("aria-label", tr("Pin"));
  b.innerHTML = PIN_SVG;
  b.onclick = () => togglePin(m.id);
  return b;
}
function msgEl(m) {
  const wrap = el("div", "msg " + (m.who === "me" ? "me" : "it") + (m.wide ? " wide" : "") + (isPinned(m.id) ? " pinned" : ""));
  wrap.dataset.mid = m.id;
  if (m.kind === "step") { wrap.innerHTML = renderContent(m); return wrap; }
  const meta = el("div", "meta");
  if (m.who !== "me") {
    const avatar = el("span", "avatar");
    avatar.innerHTML = $("face").outerHTML.replace(' id="face"', "").replace('class="face', 'class="face');
    const svg = avatar.querySelector("svg");
    if (svg) svg.setAttribute("class", "face");
    meta.append(avatar);
  }
  meta.append(el("b", "", m.who === "me" ? tr("you") : tr("Doka")), el("time", "", m.time || ""));
  meta.append(el("span", "pinstate", tr("pinned")));
  if (m.id && m.kind !== "ask") meta.append(pinButton(m));
  if (m.wide) {
    const w = el("button", "widebtn", "⤢"); w.type = "button"; w.title = tr("full width");
    w.onclick = () => { wrap.classList.toggle("wide"); dockMeasure(); };
    meta.append(w);
  }
  const content = el("div", "content"); content.innerHTML = renderContent(m);
  wrap.append(meta, content);
  return wrap;
}


/* The demo's little cube: drawn here, so the card works with no network. */
const VERTS = [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]];
const EDGES = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
let cubeGames = [];
function drawCube(g) {
  const canvas = g.canvas, ctx = g.ctx;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const w = canvas.clientWidth || 600, h = canvas.clientHeight || 300;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const a = g.angle, b = g.angle * .62;
  const cy = Math.cos(a), sy = Math.sin(a), cx = Math.cos(b), sx = Math.sin(b);
  const s = Math.min(w, h) * .26;
  const pts = VERTS.map(([x, y, z]) => {
    const x1 = x * cy - z * sy, z1 = x * sy + z * cy;
    const y1 = y * cx - z1 * sx, z2 = y * sx + z1 * cx;
    const k = 1 / (1 + z2 * .16);
    return [w / 2 + x1 * s * k, h / 2 + y1 * s * k, z2];
  });
  ctx.lineWidth = 1.4;
  ctx.strokeStyle = "rgba(157,124,255,.85)";
  ctx.shadowColor = "rgba(157,124,255,.55)";
  ctx.shadowBlur = 12;
  for (const [i, j] of EDGES) {
    ctx.beginPath(); ctx.moveTo(pts[i][0], pts[i][1]); ctx.lineTo(pts[j][0], pts[j][1]); ctx.stroke();
  }
  ctx.shadowBlur = 0;
  ctx.fillStyle = "rgba(236,232,226,.75)";
  for (const p of pts) { ctx.beginPath(); ctx.arc(p[0], p[1], 1.7, 0, Math.PI * 2); ctx.fill(); }
}
function initGames() {
  document.querySelectorAll("#flow .game").forEach(card => {
    const canvas = card.querySelector("canvas");
    if (!canvas || canvas.dataset.on) return;
    canvas.dataset.on = "1";
    const g = {card, canvas, ctx: canvas.getContext("2d"), playing: false, angle: .7};
    cubeGames.push(g);
    const overlay = card.querySelector(".gameover");
    const play = card.querySelector("[data-play]");
    if (play) play.addEventListener("click", ev => {
      ev.stopPropagation();
      g.playing = true; card.classList.add("playing");
      if (overlay) overlay.hidden = true;
    });
    canvas.addEventListener("click", () => { g.playing = !g.playing; });
    drawCube(g);
  });
}
function cubeLoop() {
  for (const g of cubeGames) if (g.playing) { g.angle += .012; drawCube(g); }
  requestAnimationFrame(cubeLoop);
}
requestAnimationFrame(cubeLoop);

/* The players are the sketch's; the media behind them is the real one. */
const mmss = sec => {
  if (!isFinite(sec)) return "—";
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return m + ":" + String(s).padStart(2, "0");
};
function mediaBox(node) { return node.closest(".aud, .vid"); }
document.addEventListener("loadedmetadata", ev => {
  const el = ev.target;
  if (!el || !el.tagName || !/^(AUDIO|VIDEO)$/.test(el.tagName)) return;
  const box = mediaBox(el), out = box && box.querySelector("[data-dur]");
  if (out) out.textContent = mmss(el.duration);
}, true);
for (const type of ["play", "pause", "ended"]) {
  document.addEventListener(type, ev => {
    const el = ev.target;
    if (!el || !el.tagName || !/^(AUDIO|VIDEO)$/.test(el.tagName)) return;
    const box = mediaBox(el);
    if (!box) return;
    box.classList.toggle("playing", type === "play" && !el.paused);
    const btn = box.querySelector("[data-audio], [data-video]");
    if (btn) btn.textContent = (type === "play" && !el.paused) ? "❚❚" : "▶";
  }, true);
}
function liveFullClose() {
  $("livefull").hidden = true;
  $("livefull-frame").srcdoc = "";
  document.body.classList.remove("livefull-open");
}
$("livefull-close").onclick = liveFullClose;
document.addEventListener("keydown", ev => {
  if (ev.key === "Escape" && !$("livefull").hidden) liveFullClose();
});
$("flow").addEventListener("click", ev => {
  const wide = ev.target.closest("[data-wide]");
  if (wide) {
    const msg = wide.closest(".msg");
    const on = msg.classList.toggle("wide");
    wide.title = on ? tr("narrow") : tr("full width");
    dockMeasure();
    return;
  }
  const full = ev.target.closest("[data-live-full]");
  if (full) {
    const card = full.closest(".artifact"), frame = card && card.querySelector(".liveframe");
    if (frame) {
      const title = card.querySelector(".art-head b");
      $("livefull-title").textContent = title ? title.textContent : "";
      $("livefull-frame").srcdoc = frame.srcdoc;
      $("livefull").hidden = false;
      document.body.classList.add("livefull-open");
    }
    return;
  }
  const aud = ev.target.closest("[data-audio]");
  if (aud) {
    const media = aud.closest(".aud").querySelector("audio");
    if (!media) return;
    if (media.paused) media.play().catch(() => {}); else media.pause();
    return;
  }
  const vid = ev.target.closest("[data-video]");
  if (vid) {
    const media = vid.closest(".vid").querySelector("video");
    if (!media) return;
    if (media.paused) media.play().catch(() => {}); else media.pause();
    return;
  }
  const ask = ev.target.closest("[data-ask]");
  if (ask) {
    const box = ask.closest(".ask");
    const yes = ask.dataset.ask === "yes";
    box.querySelector(".acts").outerHTML = `<div class="askdone">${yes ? "✓ " + tr("Allowed") : "✕ " + tr("Not now")}</div>`;
    toast(tr("This is a mock"));
  }
});

/* The generated screens the mock has always had: a chat screen and a
   landscape, drawn, so the feed shows what a card looks like. */
function screenArt(uid) {
  return `<svg viewBox="0 0 640 360" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
    <defs>
      <linearGradient id="g${uid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1d1a25"/><stop offset="1" stop-color="#0f0e14"/></linearGradient>
      <linearGradient id="a${uid}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#9d7cff"/><stop offset="1" stop-color="#6fb8d9"/></linearGradient>
    </defs>
    <rect width="640" height="360" fill="url(#g${uid})"/>
    <rect x="18" y="18" width="148" height="324" rx="14" fill="#1c1a21" stroke="#302c37"/>
    <rect x="38" y="38" width="42" height="42" rx="12" fill="none" stroke="#9d7cff" stroke-width="2"/>
    <rect x="49" y="54" width="7" height="12" rx="3.5" fill="#9d7cff"/><rect x="62" y="54" width="7" height="12" rx="3.5" fill="#9d7cff"/>
    <rect x="38" y="96" width="96" height="7" rx="3.5" fill="#ece8e2" opacity=".72"/>
    <rect x="38" y="112" width="62" height="6" rx="3" fill="#302c37"/>
    <rect x="38" y="140" width="108" height="34" rx="8" fill="#242029"/>
    <rect x="38" y="182" width="108" height="34" rx="8" fill="#242029"/>
    <rect x="38" y="224" width="108" height="34" rx="8" fill="#242029"/>
    <rect x="48" y="152" width="54" height="6" rx="3" fill="#96909e"/>
    <rect x="48" y="194" width="42" height="6" rx="3" fill="#96909e"/>
    <rect x="48" y="236" width="60" height="6" rx="3" fill="#96909e"/>
    <rect x="180" y="18" width="442" height="324" rx="14" fill="#16141b" stroke="#302c37"/>
    <rect x="180" y="18" width="442" height="46" rx="14" fill="#1c1a21"/>
    <circle cx="204" cy="41" r="8" fill="none" stroke="#9d7cff" stroke-width="1.6"/>
    <rect x="222" y="36" width="46" height="9" rx="4.5" fill="#ece8e2" opacity=".8"/>
    <rect x="280" y="36" width="68" height="9" rx="4.5" fill="#302c37"/>
    <rect x="544" y="33" width="60" height="16" rx="8" fill="#242029" stroke="#302c37"/>
    <rect x="198" y="80" width="74" height="22" rx="11" fill="#242029" stroke="#9d7cff" stroke-opacity=".5"/>
    <rect x="282" y="80" width="62" height="22" rx="11" fill="#242029" stroke="#302c37"/>
    <rect x="354" y="80" width="86" height="22" rx="11" fill="transparent" stroke="#302c37" stroke-dasharray="3 3"/>
    <rect x="432" y="86" width="168" height="10" rx="5" fill="#302c37"/>
    <rect x="198" y="124" width="250" height="9" rx="4.5" fill="#ece8e2" opacity=".66"/>
    <rect x="198" y="142" width="300" height="9" rx="4.5" fill="#ece8e2" opacity=".4"/>
    <rect x="198" y="160" width="214" height="9" rx="4.5" fill="#ece8e2" opacity=".2"/>
    <rect x="198" y="192" width="250" height="98" rx="12" fill="#1c1a21" stroke="#302c37"/>
    <rect x="214" y="208" width="52" height="7" rx="3.5" fill="#96909e"/>
    <rect x="214" y="226" width="72" height="22" rx="5" fill="#ece8e2" opacity=".78"/>
    <rect x="214" y="258" width="160" height="6" rx="3" fill="#302c37"/>
    <rect x="214" y="270" width="118" height="6" rx="3" fill="#302c37"/>
    <rect x="468" y="124" width="132" height="12" rx="6" fill="#9d7cff" opacity=".85"/>
    <rect x="468" y="146" width="132" height="12" rx="6" fill="#302c37"/>
    <rect x="468" y="192" width="132" height="98" rx="12" fill="#1c1a21" stroke="#302c37"/>
    <rect x="484" y="208" width="100" height="7" rx="3.5" fill="#96909e"/>
    <rect x="484" y="226" width="72" height="7" rx="3.5" fill="#302c37"/>
    <rect x="484" y="246" width="82" height="7" rx="3.5" fill="#302c37"/>
    <rect x="484" y="266" width="56" height="7" rx="3.5" fill="#302c37"/>
    <rect x="198" y="306" width="406" height="22" rx="11" fill="#1c1a21" stroke="#302c37"/>
    <rect x="210" y="314" width="120" height="6" rx="3" fill="#302c37"/>
    <circle cx="590" cy="317" r="7" fill="url(#a${uid})"/>
  </svg>`;
}
function landscapeArt(uid) {
  return `<svg viewBox="0 0 640 360" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
    <defs>
      <linearGradient id="sky${uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a2033"/><stop offset=".55" stop-color="#4a2f3f"/><stop offset="1" stop-color="#c07a57"/></linearGradient>
      <linearGradient id="m1${uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3a3244"/><stop offset="1" stop-color="#201c28"/></linearGradient>
      <linearGradient id="m2${uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#26212f"/><stop offset="1" stop-color="#15121a"/></linearGradient>
    </defs>
    <rect width="640" height="360" fill="url(#sky${uid})"/>
    <circle cx="492" cy="104" r="34" fill="#f1c58d" opacity=".9"/>
    <path d="M0 236 L110 138 L198 224 L294 120 L420 236 L512 168 L640 240 V360 H0Z" fill="url(#m1${uid})"/>
    <path d="M294 120 L330 160 L310 158 L338 196 L294 150 L260 186 L282 156 L266 158Z" fill="#efe6e0" opacity=".85"/>
    <path d="M0 286 L132 210 L248 292 L372 208 L500 292 L640 226 V360 H0Z" fill="url(#m2${uid})"/>
    <path d="M0 300 H640 V360 H0Z" fill="#100e14"/>
    <path d="M0 302 L200 296 L380 306 L640 296" stroke="#5c4a56" stroke-width="1.5" fill="none" opacity=".5"/>
    <rect x="0" y="330" width="640" height="30" fill="#0c0b10" opacity=".8"/>
  </svg>`;
}
const svgSrc = svg => "data:image/svg+xml;utf8," + encodeURIComponent(svg.replace(/^<svg /, '<svg xmlns="http://www.w3.org/2000/svg" '));
const DEMO_PAGE_PID = "window@demo";
/* The demo's details: one turn, so the timeline and the prompt bits can be
   looked at without a model call. */
const DEMO_CONTEXT = {
  limit: 70000,
  turns: [{
    at: Date.now() / 1000,
    messages: [
      {role: "system", content: `[system]
You are heare: a voice in the room, not a window to open. You decide when to answer.

[persona]
Your name is Doka. You speak Ukrainian, briefly, and never formal.

[laws]
Never pretend to have done what you did not. Ask before changing the world.

[memory]
The person lives in Burshtyn. The dentist is on Thursday at 10:00.

[the canvas]
The page shows what is better seen than heard: a table, a weather card, a picture.

[the conversation]
Six turns; the last one asked about the weather.`},
      {role: "user", content: "What is the weather in Burshtyn? And remind me about the meeting."},
      {role: "assistant", content: "It is +1° in Burshtyn, feels like −2°. The meeting is at 13:30."},
    ],
    verbs: [
      {function: {name: "show", description: "Put a card in the chat.", parameters: {properties: {html: {}, title: {}, say: {}, width: {}}}}},
      {function: {name: "show_link", description: "Show a web link as a card.", parameters: {properties: {url: {}, title: {}, say: {}}}}},
      {function: {name: "show_media", description: "Show a file from this conversation.", parameters: {properties: {file: {}, kind: {}, title: {}, say: {}}}}},
    ],
  }],
  tasks: [],
};


const weatherHTML = `<div class="wx">
  <div><div class="wx-city">${tr("Burshtyn")}</div><div class="wx-temp">+1°</div><div class="wx-cond">${tr("cloudy · feels like −2°")}</div></div>
  <div class="wx-badge">${tr("now")}</div>
  <div class="wx-rows">
    <div class="wx-row"><span>${tr("wind")}</span><b>6 ${tr("km/h")}</b></div>
    <div class="wx-row"><span>${tr("humidity")}</span><b>81%</b></div>
    <div class="wx-row"><span>${tr("pressure")}</span><b>748 ${tr("mm")}</b></div>
  </div>
  <div class="wx-foot">${tr("updated {time} · source: weather", {time: "21:17"})}</div>
</div>`;
const tableHTML = `<table class="tbl"><thead><tr><th>${tr("Was")}</th><th>${tr("Became")}</th></tr></thead><tbody>
  <tr><td>${tr("A separate board")}</td><td>${tr("A chat and a pinned dock")}</td></tr>
  <tr><td>${tr("A canvas grid")}</td><td>${tr("Artifacts as messages")}</td></tr>
  <tr><td>${tr("Part cards and chips")}</td><td>${tr("One carousel of the body")}</td></tr>
  <tr><td>${tr("The Context tab")}</td><td>${tr("Details in the system column")}</td></tr>
</tbody></table>`;

/* The demo's body and fleet: the same shapes the brain reports, so the
   carousel and the machines can be looked at with no parts connected. */
const DEMO_BODIES = {
  voice: {
    channel: "voice", state: "approved", host: "this Mac", version: "0.9.16",
    about: "listens and speaks", info: tr("microphone · level 3 of 5"), foot: tr("listening") + " · " + tr("{h} h", {h: 2}),
    started: Date.now() / 1000 - 7200,
    values: {state: "on", mic: true, hear: false, volume: .4},
    ui: [
      {kind: "label", head: true, name: "state", value: "on"},
      {kind: "toggle", name: "mic", label: tr("Microphone")},
      {kind: "toggle", name: "hear", label: tr("Hear the answer")},
      {kind: "slider", name: "volume", label: tr("Volume"), min: 0, max: 1, step: .05},
    ],
  },
  eyes: {
    channel: "eyes", state: "approved", host: "this Mac", version: "0.9.16",
    about: "what is in front", info: "Safari · Xcode", foot: tr("updated {for} ago", {for: tr("{m} min", {m: 2})}),
    started: Date.now() / 1000 - 3600,
    values: {state: "2 windows", screen: true, around: true},
    ui: [
      {kind: "label", head: true, name: "state", value: tr("2 windows")},
      {kind: "toggle", name: "screen", label: tr("See the screen")},
      {kind: "toggle", name: "around", label: tr("See around")},
      {kind: "button", name: "refresh", label: tr("Refresh now")},
    ],
  },
  opencode: {
    channel: "opencode", state: "approved", host: "this Mac", version: "0.9.16",
    about: "an agent acts in the system", info: tr("tests · step 2 of 4"), foot: "opencode · 2 " + tr("steps"),
    started: Date.now() / 1000 - 900,
    values: {state: "1 task", allow: true, quiet: false},
    ui: [
      {kind: "label", head: true, name: "state", value: tr("1 task")},
      {kind: "toggle", name: "allow", label: tr("Allow actions")},
      {kind: "toggle", name: "quiet", label: tr("Tests without asking")},
      {kind: "button", name: "run", label: tr("Run the tests")},
    ],
  },
  [DEMO_PAGE_PID]: {
    channel: "web", state: "approved", host: "this Mac", version: "0.9.16",
    about: "a page in the browser", info: tr("2 tabs · 1 phone"), foot: "Chrome · 127.0.0.1",
    started: Date.now() / 1000 - 600,
    values: {state: "this window", browser: "Chrome"},
    ui: [
      {kind: "label", head: true, name: "state", value: tr("this window")},
      {kind: "label", name: "browser", label: tr("This window"), value: "Chrome"},
      {kind: "button", name: "disconnect", label: tr("Disconnect this window")},
      {kind: "button", name: "new", label: tr("New window")},
    ],
  },
  sources: {
    channel: "sources", state: "approved", host: "this Mac", version: "0.9.16",
    about: "the weather and more", info: tr("Burshtyn") + " · +1°", foot: tr("updated {for} ago", {for: tr("{m} min", {m: 5})}),
    started: Date.now() / 1000 - 1800,
    values: {state: "weather", weather: true, rates: true},
    ui: [
      {kind: "label", head: true, name: "state", value: tr("weather")},
      {kind: "toggle", name: "weather", label: tr("Weather")},
      {kind: "toggle", name: "rates", label: tr("Exchange rates")},
      {kind: "button", name: "refresh", label: tr("Refresh the sources")},
    ],
  },
};
const DEMO_MACHINES = [
  { pid: "machine@mac", name: "this Mac", alias: tr("this Mac"), brain: true, keyed: true,
    reported: true, state: "online", version: "0.9.16", platform: "macOS",
    parts: [
      {name: "voice", channel: "voice", on: true, pids: ["voice"]},
      {name: "eyes", channel: "eyes", on: true, pids: ["eyes"]},
      {name: "hands", channel: "opencode", on: true, pids: ["opencode"]},
      {name: "page", channel: "web", on: true, pids: [DEMO_PAGE_PID]},
      {name: "sources", channel: "sources", on: true, pids: ["sources"]},
    ] },
  { pid: "machine@mbp", name: "MacBook", alias: "MacBook", brain: false, keyed: true,
    reported: true, state: "online", version: "0.9.16", platform: "macOS",
    parts: [
      {name: "voice", channel: "voice", on: true},
      {name: "hands", channel: "opencode", on: false},
    ] },
];

/* The mock's own conversation, in three sessions: the thing to look at
   when the real feed is quiet, and the shape every reply aims at. */
const DEMO_FEED = [
  { id: "demo/1", who: "me", time: "21:14", text: tr("What is the weather in Burshtyn? And remind me about the meeting.") },
  { id: "demo/2", who: "it", time: "21:14", html: `<p>${tr("It is +1° in Burshtyn, feels like −2°. Wind 6 km/h. Here is the card — pinned, so it stays at hand.")}</p>` },
  { id: "demo/3", who: "it", time: "21:14", kind: "html", artifact: {label: "weather", title: tr("Weather") + " · " + tr("Burshtyn")}, html: weatherHTML },
  { id: "demo/4", who: "it", time: "21:15", html: `<p>${tr("The meeting is at 13:30 — the dentist. I will remind you 25 minutes before.")}</p>` },
  { id: "demo/5", who: "me", time: "21:16", text: tr("Show me what the new screen looks like.") },
  { id: "demo/6", who: "it", time: "21:16", html: `<p>${tr("Here is the mock. This is not a board any more — a chat, a pinned dock and the body beside it.")}</p>` },
  { id: "demo/7", who: "it", time: "21:16", kind: "image", title: tr("The new screen"), caption: tr("Mock · 11 November"), src: svgSrc(screenArt("scr")) },
  { id: "demo/8", who: "it", time: "21:17", kind: "step", text: tr("hands · building the mock · step 2 of 3") },
  { id: "demo/9", who: "it", time: "21:17", html: `<p>${tr("And a short audio — how it sounds.")}</p>` },
  { id: "demo/10", who: "it", time: "21:17", kind: "audio", title: tr("Voice") + " · 0:12", src: "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=" },
  { id: "demo/11", who: "it", time: "21:17", kind: "video", title: tr("Demo") + " · " + tr("The new screen"), vlabel: tr("Demo") + " · 0:24", vtime: tr("{s} s", {s: 24}), src: "data:video/mp4;base64,AAAAHGZ0eXBpc29tAAACAGlzb21pc28ybXA0MQAAAAhmcmVlAAAAAG1kYXQ=", poster: svgSrc(screenArt("vid")), lead: `<p>${tr("And a short video — how it moves.")}</p>` },
  { id: "demo/12", who: "it", time: "21:18", kind: "game", title: tr("Cube") + " · 3D", wide: true, lead: `<p>${tr("And a small 3D game. Such things live in the chat too — in a sandbox, paused off screen.")}</p>` },
  { id: "demo/13", who: "it", time: "21:18", kind: "link", artifact: {title: tr("heare design system"), url: "https://heare.local/design", host: "heare.local/design"}, lead: `<p>${tr("A link to the design system — so it is not all in my head.")}</p>` },
  { id: "demo/14", who: "it", time: "21:19", kind: "html", artifact: {label: "table", title: tr("What changes")}, html: tableHTML, lead: `<p>${tr("And the table: what goes away from the old screen.")}</p>` },
  { id: "demo/15", who: "it", time: "21:19", kind: "ask", title: tr("hands ask for permission"), text: tr("Run make test in this checkout?") },
];
const DEMO_SESSIONS = {
  demo: { title: () => tr("Today"), messages: DEMO_FEED },
  nov3: { title: () => tr("3 November"), messages: [
    { id: "nov3/1", who: "me", time: "09:12", text: tr("Show me the photo from the Carpathians.") },
    { id: "nov3/2", who: "it", time: "09:12", html: `<p>${tr("Here. It was cold, but beautiful.")}</p>` },
    { id: "nov3/3", who: "it", time: "09:12", kind: "image", title: tr("Carpathians"), caption: tr("October · 3 November"), src: svgSrc(landscapeArt("carp")) },
  ] },
  nov1: { title: () => tr("1 November"), messages: [
    { id: "nov1/1", who: "me", time: "18:40", text: tr("Remind me about the doctor.") },
    { id: "nov1/2", who: "it", time: "18:40", html: `<p>${tr("Thursday, 6 November, at 10:00. Noted.")}</p>` },
  ] },
};
const DEMO_PINS = {
  trail: [
    { mid: "demo/3", kind: "weather", title: tr("Weather"), line: "+1° · " + tr("Burshtyn") },
    { mid: "demo/7", kind: "image", title: tr("The new screen"), line: tr("Mock · 11 November") },
    { mid: "demo/12", kind: "game", title: tr("3D game"), line: tr("cube · pause off screen") },
  ],
  shelf: [
    { mid: "nov3/1", kind: "image", title: tr("Carpathians"), line: tr("October · 3 November"), shelf: true,
      src: svgSrc(landscapeArt("pv1")),
      html: `<figure class="artifact photo"><div class="ph"><img src="${svgSrc(landscapeArt("pv1"))}" alt=""></div><figcaption class="cap">${tr("October · 3 November")}</figcaption></figure>` },
  ],
};

function demo() {
  session = "demo";
  sessionList = [
    {id: "demo", title: tr("Today"), turns: DEMO_FEED.length},
    {id: "nov3", title: tr("3 November"), turns: DEMO_SESSIONS.nov3.messages.length},
    {id: "nov1", title: tr("1 November"), turns: DEMO_SESSIONS.nov1.messages.length},
  ];
  $("name").textContent = tr("Doka");
  voice = {mic: true, speaker: false};
  webPid = DEMO_PAGE_PID;
  phase = "hearing";
  $("sessionpick").replaceChildren(el("option", "", tr("Today")));
  $("sessionpick").value = "demo";
  $("sesslabel").textContent = tr("Today");
  $("modelchip").textContent = "deepseek";
  $("up").textContent = "make test · " + tr("{m} min", {m: 2});
  $("model").textContent = "deepseek · demo";
  $("hands").textContent = "demo";
  $("around").textContent = tr("quiet") + " · " + tr("you are here");
  $("session").textContent = tr("replies: {n}", {n: 6});
  pinState = DEMO_PINS;
  feedClear();
  DEMO_FEED.forEach(m => feedAppend(msgEl(m)));
  feedToEnd();
  state = {bodies: DEMO_BODIES, running: {version: "0.9.16"}, parts: {executors: ["opencode"]}, model: {provider: "deepseek", model: "deepseek-flash", wanted: "deepseek", providers: ["deepseek", "openrouter", "zai"]}, session: {turns: DEMO_FEED.length, started: Date.now()/1000 - 7200}, around: {app: "Safari", present: true}};
  machineList = DEMO_MACHINES;
  parts(DEMO_BODIES);
  pinState = DEMO_PINS;
  dockRender();
  show();
  drawTopbar();
  activity.what = "make test"; activity.at = Date.now() - 120000;
  drawLast();
  setTimeout(dockMeasure, 30);
  initGames();
}
function demoSession(id) {
  const found = DEMO_SESSIONS[id];
  if (!found) return;
  session = id;
  $("sessionpick").replaceChildren(...Object.entries(DEMO_SESSIONS).map(([k, s]) => {
    const o = el("option", "", s.title()); o.value = k; return o;
  }));
  $("sessionpick").value = id;
  $("sesslabel").textContent = found.title();
  feedClear();
  found.messages.forEach(m => feedAppend(msgEl(m)));
  feedToEnd();
  initGames();
}

function line(text, who, via) {
  const e = el("div", "line " + who, text);
  if (VIA[via]) e.append(el("span", "via", VIA[via]));
  return feedAppend(e);
}
const aside = text => feedAppend(el("div", "aside", text));
const LOOK = {
  recall: tr("looking in memory"),
  list_reminders: tr("checking the reminders"),
  read_body: tr("looking at a part"),
  canvas_view: tr("looking at the screen"),
  remind: tr("setting a reminder"),
  cancel_reminder: tr("cancelling a reminder"),
};
const quoted = text => {
  const t = String(text ?? "").trim();
  if (!t) return "";
  const cut = t.length > 40 ? t.slice(0, 40).trimEnd() + "…" : t;
  return ` «${cut}»`;
};
const step = e => {
  const label = LOOK[e.name];
  if (!label) return;
  const words = e.arguments && (e.arguments.query || e.arguments.text);
  aside("· " + label + quoted(words));
};

/* ── sessions ── */
let session = "", pending = [], sessionList = [];
const LINK = /https?:\/\/[^\s«»"<>]+/g;
const ICONS = {image: "▣", text: "▤", file: "▢"};
function fileChip(owner, f) {
  const chip = el("a", "chip");
  chip.href = `/api/sessions/${owner}/files/${f.id}`; chip.target = "_blank"; chip.rel = "noopener";
  chip.append(el("span", "", `${ICONS[f.kind] || ICONS.file} ${f.name}`));
  return chip;
}
function linkChip(url) {
  const chip = el("a", "chip"); let host = url;
  try { host = new URL(url).hostname; } catch { /* shown as written */ }
  chip.href = url; chip.target = "_blank"; chip.rel = "noopener noreferrer";
  chip.append(el("span", "", `↗ ${host}`));
  return chip;
}
function chipped(node, text, owner, files) {
  const links = (text.match(LINK) || []).filter(u => { try { return ["http:", "https:"].includes(new URL(u).protocol); } catch { return false; } });
  if (!links.length && !(files || []).length) return node;
  const row = el("div", "chips");
  (files || []).forEach(f => row.append(fileChip(owner, f)));
  links.forEach(u => row.append(linkChip(u)));
  node.append(row);
  return node;
}
function showPending() {
  $("chips").replaceChildren(...pending.map(f => {
    const chip = fileChip(session, f), off = el("button", "", "✕");
    off.type = "button";
    off.onclick = e => { e.preventDefault(); pending = pending.filter(p => p !== f); showPending(); };
    chip.append(off);
    return chip;
  }));
}
function sessionName(s) {
  const name = s.title || (s.earlier ? tr("Earlier") : tr("New session"));
  return (s.unread ? `● ${s.unread} · ` : "") + name;
}
async function loadSessions() {
  const now = await api("sessions");
  const changed = now.active !== session;
  session = now.active;
  sessionList = now.items || [];
  $("sessionpick").replaceChildren(...sessionList.map(s => {
    const o = el("option", "", sessionName(s)); o.value = s.id; return o;
  }));
  $("sessionpick").value = session;
  const active = sessionList.find(s => s.id === session);
  $("sesslabel").textContent = active ? sessionName(active) : "";
  if (sessionOpen) drawSessionRows();
  if (!changed) return;
  pending = []; showPending();
  const turns = await api(`sessions/${session}/turns`);
  feedClear();
  if (!turns.length) feedEmpty();
  turns.forEach(t => feedAppend(chipped(turnMsg(t), t.text, session, t.files)));
  feedToEnd();
}
let sessionOpen = false;
const sessionAct = (path, body) => api(path, body ? {method: "POST", body} : {method: "POST"})
  .catch(() => aside(tr("Wait for the reply to end.")));
async function switchSession(id) {
  if (DEMO) { demoSession(id); drawSessionRows(); return; }
  await sessionAct(`sessions/${id}/open`);
  await loadSessions();
}
async function newSession() {
  if (DEMO) return;
  await sessionAct("sessions");
  await loadSessions();
}
let dropArmed = 0;
function deleteSession(id, button) {
  if (DEMO) return;
  const now = Date.now();
  if (now - dropArmed > 3000) {
    dropArmed = now;
    button.dataset.armed = "1";
    const was = button.textContent;
    button.textContent = "×?";
    button.title = tr("Sure?");
    setTimeout(() => { if (button.isConnected) { button.dataset.armed = ""; button.textContent = was; button.title = tr("Delete"); } }, 3000);
    return;
  }
  dropArmed = 0;
  api(`sessions/${id}`, {method: "DELETE"}).then(loadSessions)
    .catch(() => aside(tr("Wait for the reply to end.")));
}
function renameSession(row, id, title) {
  if (DEMO) return;
  const name = row.querySelector("b");
  const field = el("input", "mrename");
  field.value = title;
  name.replaceWith(field); field.focus(); field.select();
  let done = false;
  const save = async keep => {
    if (done) return; done = true;
    field.replaceWith(name);
    if (keep && field.value.trim() && field.value.trim() !== title) {
      await api(`sessions/${id}/rename`, {method: "POST", body: {title: field.value.trim()}});
      await loadSessions();
    }
  };
  field.onblur = () => save(true);
  field.onkeydown = ev => {
    if (ev.key === "Enter") field.blur();
    if (ev.key === "Escape") { done = true; field.replaceWith(name); }
  };
}
function drawSessionRows() {
  const box = $("sessionrows");
  if (!box) return;
  const list = sessionList.length ? sessionList : [];
  box.replaceChildren(...list.map(s => {
    const row = el("div", "sessrow" + (s.id === session ? " current" : ""));
    const st = el("div", "st");
    st.append(el("b", "", s.title || sessionName(s)),
              el("small", "", tr("replies: {n}", {n: s.turns || 0}) + (s.unread ? ` · ${s.unread}` : "")));
    const pencil = el("button", "del", "✎");
    pencil.type = "button"; pencil.title = tr("Rename");
    pencil.onclick = ev => { ev.stopPropagation(); renameSession(row, s.id, s.title || ""); };
    const del = el("button", "del", "×");
    del.type = "button"; del.title = tr("Delete");
    del.disabled = list.length < 2;
    del.onclick = ev => { ev.stopPropagation(); deleteSession(s.id, del); };
    row.append(st, pencil, del);
    row.onclick = () => switchSession(s.id);
    return row;
  }));
}
function openSessions() {
  sessionOpen = true;
  openSheet(tr("Sessions"), "sessions");
  drawSessionRows();
}
$("sessionpick").onchange = () => switchSession($("sessionpick").value);
$("sessionnew").onclick = newSession;
$("sessionname").onclick = () => {
  const row = $("sessionrows").querySelector(".sessrow.current");
  const s = sessionList.find(x => x.id === session);
  if (row && s) renameSession(row, s.id, s.title || "");
};
$("sessiondrop").onclick = () => {
  const row = $("sessionrows").querySelector(".sessrow.current .del:last-child");
  if (row) deleteSession(session, row);
};
$("newsession").onclick = newSession;

/* ── the panel ── */
function show() {
  const off = voice && !voice.mic && !voice.speaker;
  const mood = off ? "off" : phase;
  const said = off ? tr("quiet")
    : phase === "thinking" ? tr("thinking")
    : phase === "speaking" ? tr("speaking")
    : voice && voice.mic ? tr("listening") : tr("quiet");
  for (const id of ["face"]) $(id).setAttribute("class", "face " + mood);
  $("nowtext").textContent = said;
  const dot = $("dot");
  if (dot) dot.className = "livedot " + mood;
  $("you").classList.toggle("live", phase === "hearing" && !off);
  $("it").classList.toggle("live", phase === "thinking" || phase === "speaking");
}

function vitals(s) {
  const m = s.model || {};
  if (m.name) { $("name").textContent = m.name; document.title = m.name + " · heare"; }
  const box = $("model"); box.replaceChildren();
  if (m.error) box.append(el("b", "bad", tr("no key for any model")));
  else {
    box.append(el("b", "", m.provider || "?"), document.createTextNode(" · " + (m.model || "?")));
    if (m.fallback) box.append(el("div", "warn", tr("{wanted} is chosen, but it has no key", {wanted: m.wanted})));
  }
  const hands = s.parts.executors.filter(n => n !== "screens");
  $("hands").replaceChildren(hands.length ? el("b", "ok", hands.join(" · ")) : el("span", "warn", tr("no agents found")));
  const around = s.around || {}, playing = around.playing || [];
  const near = [];
  if (around.app) near.push(around.app);
  near.push(playing.length ? tr("playing: {what}", {what: playing.join(", ")}) : tr("quiet"));
  near.push(around.present === false ? tr("not here") : tr("you are here"));
  $("around").replaceChildren(el("span", playing.length ? "warn" : "", near.join(" · ")));
  const t = s.session, mins = Math.round((Date.now() / 1000 - t.started) / 60);
  const up = mins < 1 ? tr("just started") : mins < 60 ? tr("{m} min", {m: mins}) : tr("{h} h {m} min", {h: Math.floor(mins / 60), m: mins % 60});
  const run = s.running || {};
  $("up").textContent = run.version ? `${up} · v${run.version}` : up;
  drawUpdate(s.update);
  $("up").title = run.commit ? tr("commit {c}", {c: run.commit}) : "";
  $("session").textContent = tr("replies: {n}", {n: t.turns});
}

/* ── a newer heare ── */
const UPDATE_SAYS = {
  downloading: u => tr("downloading heare {v}…", {v: u.latest || ""}),
  backing_up: () => tr("backing up memory before the update…"),
  installing: u => tr("installing heare {v} — it will restart now", {v: u.version || u.latest || ""}),
  updated: u => u.previous ? tr("updated from {old} to {new}", {old: u.previous, new: u.current}) : tr("updated to {new}", {new: u.current}),
};
/* Every part of a machine is released together, so a machine is one line.
   A machine paired with its own key can be told to take the new version;
   it fetches and checks it itself. */
const MACHINE_SAYS = {
  asked: () => tr("asked to update"),
  taking: m => (m.progress && m.progress.total)
    ? tr("updating… {pct}%", {pct: Math.min(100, Math.round(100 * m.progress.received / m.progress.total))})
    : tr("updating…"),
  failed: m => tr("could not update: {why}", {why: m.error || ""}),
};
function machinesBehind(u) {
  const hosts = new Map();
  // The brain's own machine, where no app swaps it: its keeper takes it.
  const brain = state && state.running && state.running.version;
  if (brain && brain !== u.latest && !u.can_install) {
    hosts.set(tr("the brain"), {versions: new Set([brain]), machine: "machine@brain"});
  }
  for (const b of Object.values((state && state.bodies) || {})) {
    if (!b.version || b.version === u.latest) continue;
    const host = b.host || "?";
    const seen = hosts.get(host) || {versions: new Set(), machine: null};
    seen.versions.add(b.version);
    seen.machine = seen.machine || b.machine || null;
    hosts.set(host, seen);
  }
  return [...hosts].map(([host, seen]) => {
    const line = el("p", "notes", tr("{host} is on {v}", {host, v: [...seen.versions].join(", ")}));
    const asked = seen.machine && (u.machines || {})[seen.machine];
    if (asked && MACHINE_SAYS[asked.state]) {
      line.append(" · ", MACHINE_SAYS[asked.state](asked));
    }
    if (seen.machine && (!asked || asked.state === "failed")) {
      const go = el("button", "mini yes", tr("Update"));
      go.type = "button";
      go.onclick = () => {
        go.disabled = true;
        api("update/machine", {method: "POST", body: {machine: seen.machine}})
          .then(drawUpdate).catch(() => { go.disabled = false; });
      };
      line.append(" ", go);
    }
    return line;
  });
}
function drawUpdate(u) {
  updateState = u;
  const box = $("update");
  // The brain may be on the newest while a machine is not: its row still shows.
  const behind = u && u.state === "current" ? machinesBehind(u) : [];
  if (behind.length) {
    box.hidden = false; box.classList.remove("bad");
    box.replaceChildren(el("b", "", tr("heare {v} is out", {v: u.latest})), ...behind);
    return;
  }
  if (!u || !["available", "downloading", "backing_up", "installing", "updated", "failed"].includes(u.state)) {
    box.hidden = true; return;
  }
  box.hidden = false;
  box.classList.toggle("bad", u.state === "failed");
  const parts = [];
  if (u.state === "available") {
    parts.push(el("b", "", tr("heare {v} is out", {v: u.latest})), el("span", "", tr("you have {v}", {v: u.current})));
    if (u.can_install) {
      const go = el("button", "mini yes", tr("Update"));
      go.type = "button";
      go.onclick = () => {
        go.disabled = true;
        api("update/install", {method: "POST"}).then(drawUpdate)
          .catch(why => drawUpdate({...u, state: "failed", error: typeof why === "string" && why ? why : tr("the update could not start")}));
      };
      parts.push(go);
    } else if (u.page) {
      const link = el("a", "", tr("release page"));
      link.href = u.page; link.target = "_blank"; link.rel = "noopener";
      parts.push(link);
    }
    parts.push(...machinesBehind(u));
    if (u.notes) parts.push(el("p", "notes", u.notes));
  } else if (u.state === "failed") {
    parts.push(el("b", "", tr("The update failed")), el("span", "", u.error || ""));
    const again = el("button", "mini", tr("Check again"));
    again.type = "button";
    again.onclick = () => { again.disabled = true; api("update/check", {method: "POST"}).then(drawUpdate).catch(() => { again.disabled = false; }); };
    parts.push(again);
  } else {
    parts.push(el("span", "", UPDATE_SAYS[u.state](u)));
  }
  box.replaceChildren(...parts);
}

function spark() {
  if (!speeds.length) return;
  const last = speeds[speeds.length - 1], sorted = [...speeds].sort((a, b) => a - b);
  $("speed").textContent = tr("{last} s · median {mid} s", {last: last.toFixed(1), mid: sorted[Math.floor(sorted.length / 2)].toFixed(1)});
  const max = Math.max(...speeds, 1), w = 70 / Math.max(speeds.length, 8);
  $("spark").innerHTML = speeds.map((v, i) => `<rect x="${i * w}" y="${16 - v / max * 15}" width="${Math.max(w - 1.5, 1)}" height="${v / max * 15}" fill="currentColor" opacity=".55"/>`).join("");
}

// A task event replaces its step on every change; remember the last few so a
// long run does not read as the same word three times.
function noteTask(e) {
  noteActivity(e.step || e.request);
  const key = `${e.id}`;
  let entry = stepLog.get(key);
  if (!entry || entry.request !== e.request) { entry = {request: e.request, steps: []}; stepLog.set(key, entry); }
  const last = entry.steps[entry.steps.length - 1];
  if (e.step && (!last || last.step !== e.step)) entry.steps.push({executor: e.executor, step: e.step});
  entry.steps = entry.steps.slice(-3);
}

let held = [], holding = {live: [], permission: null, question: null};
let askSeen = "";
const holdShow = () => {
  const waiting = (holding.permission ? 1 : 0) + (holding.question ? 1 : 0) + held.length;
  const need = $("mneed");
  need.hidden = !waiting;
  need.textContent = waiting > 9 ? "9+" : String(waiting);
  need.title = waiting ? tr("On my mind") : "";
};
const holdTitle = text => {
  const title = el("div", "atitle");
  title.append(el("i"), document.createTextNode(text));
  return title;
};
/* What is waiting is a card at the foot of the feed, where the person is
   already reading: a leave, a question, a running hand, a held thought. */
function permissionCard(permission) {
  const box = el("div", "ask hold-card");
  box.append(holdTitle(tr("HANDS ASK FOR PERMISSION")),
             el("p", "", tr("{who} wants to: {what}", {who: permission.executor, what: permission.what})));
  if (permission.why) box.append(el("p", "why", tr("I ask because {why}", {why: permission.why})));
  const acts = el("div", "acts");
  const yes = el("button", "btn primary", tr("Allow")), no = el("button", "btn", tr("Not now"));
  yes.type = "button"; no.type = "button";
  yes.onclick = () => api(`permission/${permission.id}`, {method: "POST", body: {allow: true}});
  no.onclick = () => api(`permission/${permission.id}`, {method: "POST", body: {allow: false}});
  acts.append(yes, no);
  box.append(acts);
  return box;
}
function questionCard(question) {
  const box = el("div", "ask hold-card");
  box.append(holdTitle(tr("HANDS")), el("p", "", tr("{who} asks: {what}", {who: question.executor, what: question.text})));
  const field = el("input"); field.placeholder = tr("Answer…"); field.autocomplete = "off";
  const send = el("button", "btn primary", tr("Send"));
  const reply = () => {
    const text = field.value.trim();
    if (!text) return;
    send.disabled = true;
    api(`answer/${question.id}`, {method: "POST", body: {text}}).catch(() => { send.disabled = false; });
  };
  send.onclick = reply;
  field.onkeydown = e => { if (e.key === "Enter") reply(); };
  const row = el("div", "pair"); row.append(field, send);
  box.append(row);
  return box;
}
function taskCard(t) {
  const card = el("div", "task hold-card");
  const line = el("div", "taskline");
  const how = {running: t.step || tr("working"), done: tr("done"), failed: tr("failed"), stopped: tr("stopped")}[t.status];
  const secs = Math.round((t.ended || Date.now() / 1000) - t.started);
  line.append(el("i", "sdot"), el("b", "", t.executor || tr("Hands")), el("span", "", [how, tr("{s} s", {s: secs})].filter(Boolean).join(" · ")));
  if (t.status === "running") {
    const stop = el("button", "mini", tr("stop"));
    stop.type = "button";
    stop.onclick = () => api(`tasks/${t.id}/stop`, {method: "POST"});
    line.append(stop);
  }
  card.append(line);
  if (t.request) card.append(el("p", "taskwhat", t.request));
  const logged = stepLog.get(`${t.id}`);
  if (logged && logged.request === t.request) {
    // The title already carries the step in flight; the trail holds the
    // ones before it, so the card never says the same thing twice.
    const steps = t.status === "running" && logged.steps.at(-1)?.step === t.step
      ? logged.steps.slice(0, -1) : logged.steps;
    if (steps.length) {
      const trail = el("div", "tsteps");
      for (const step of steps) trail.append(el("div", "", `· ${step.step}`));
      card.append(trail);
    }
  }
  return card;
}
function intentCard(i) {
  const words = {soma: tr("unfinished"), reminder: tr("a reminder"), task_done: tr("done"), task_failed: tr("failed")};
  const card = el("div", "task hold-card");
  const line = el("div", "taskline");
  const dots = "●".repeat(i.weight) + "○".repeat(Math.max(0, 3 - i.weight));
  line.append(el("i", "sdot"), el("b", "", i.what), el("span", "", [words[i.why] || "", dots].filter(Boolean).join(" · ")));
  card.append(line);
  const acts = el("div", "acts");
  const now = el("button", "mini yes", tr("say now")), no = el("button", "mini", tr("not now"));
  now.type = "button"; no.type = "button";
  now.onclick = () => api(`intents/${i.id}/now`, {method: "POST"}).then(heldNow);
  no.onclick = () => api(`intents/${i.id}/drop`, {method: "POST"}).then(heldNow);
  acts.append(now, no);
  card.append(acts);
  return card;
}
function renderHold(jump = false) {
  const hold = $("hold");
  if (!hold) return;
  // A question is answered in this card: a refresh must not take the field
  // out from under the person who is typing in it.
  if (typingIn(hold)) { holdShow(); return; }
  const parts = [];
  if (holding.permission) parts.push(permissionCard(holding.permission));
  if (holding.question) parts.push(questionCard(holding.question));
  for (const t of holding.live || []) parts.push(taskCard(t));
  for (const i of held) parts.push(intentCard(i));
  hold.replaceChildren(...parts);
  hold.hidden = !parts.length;
  holdShow();
  if (jump) feedToEnd();
}
function drawIntents() { renderHold(); }
const heldNow = () => api("intents")
  .then(r => { held = r.intents || []; renderHold(); })
  .catch(() => {});

function tasks(list, permission, question) {
  const live = list.filter(t => t.status === "running" || Date.now() / 1000 - (t.ended || 0) < 180);
  const key = `${permission ? permission.id : ""}/${question ? question.id : ""}`;
  const jump = key !== askSeen && Boolean(permission || question);
  askSeen = key;
  holding = {live, permission, question};
  renderHold(jump);
}

/* ── the parts that connect to the brain ── */
const FORM = {input: tr("input"), output: tr("output"), executor: tr("executor")};
const PART_LABEL = {web: tr("Page"), eyes: tr("Eyes"), text: tr("Chat"), voice: tr("Voice"), opencode: tr("CLI hands"), sources: tr("Sources"), "native-hands": tr("Native hands")};
// What a part is, in the person's words. The manifest's `about` is for the
// model (English, how to route work to it); it must not be shown here.
const PART_WHAT = {
  voice: tr("listens and speaks"),
  eyes: tr("what is in front, whether you are here, whether something plays"),
  sources: tr("the weather and more: what is around now"),
  opencode: tr("an agent acts in the system: code, files, commands"),
  "native-hands": tr("quick acts on the bodies: OLED, light, pins"),
  web: tr("a page in the browser"),
  text: tr("a conversation in the terminal"),
};
const PART_STATE = {pending: tr("waiting"), approved: tr("connected"), away: tr("not connected"), rejected: tr("rejected"), expired: tr("timed out")};
const PART_ORDER = {pending: 0, approved: 1, away: 2, rejected: 3, expired: 4};
// Where a card stands on the strip. Always the same order, so a part that
// drops out or comes back moves nothing; only a part that asks comes first.
const PART_PLACE = {voice: 1, eyes: 2, "native-hands": 3, opencode: 3, sources: 6, text: 7};
const placeOf = (pid, b) => b.state === "pending" ? 0
  : pid === webPid ? 4 : pid.startsWith("window@") ? 5
  : PART_PLACE[b.channel] ?? ((b.forms || []).includes("executor") ? 3 : 8);
// A page has one switch, and a part that is away has none to offer: both
// are a chip in one line under the cards, not a card.
const isChip = (pid, b) => !["approved", "pending"].includes(b.state);
const nameOf = (pid, b) => pid === webPid ? tr("Page")
  : pid.startsWith("window@") ? tr("page · {host}", {host: b.host || pid.slice(7)})
  : PART_LABEL[b.channel] || b.channel || pid;

const truth = (b, c) => (b.values && Object.prototype.hasOwnProperty.call(b.values, c.name)) ? b.values[c.name] : c.value;

function controlsFor(pid, b, group = "", act = null) {
  const list = (Array.isArray(b.ui) ? b.ui : []).filter(c => (c.group || "") === group);
  if (b.state !== "approved" || !list.length) return null;
  const grid = el("div", "keys"), extra = el("div", "actions"), facts = el("p", "pfacts");
  const send = act || ((name, value) => turn(pid, name, value));
  const shown = (c, value) => c.unit != null ? `${value} ${tr(c.unit)}` : Math.round(Number(value) * 100) + "%";
  for (const c of list) {
    const value = truth(b, c);
    if (c.kind === "toggle") {
      const row = el("button", "swrow" + (value ? " on" : ""));
      row.type = "button";
      row.setAttribute("role", "switch");
      row.setAttribute("aria-checked", String(Boolean(value)));
      row.title = value ? tr("on") : tr("off");
      row.append(el("span", "", tr(c.label || c.name)), el("i", "sw"));
      row.onclick = () => send(c.name, !value);
      grid.append(row);
    } else if (c.kind === "slider") {
      const row = el("div", "vol"), range = el("input");
      range.type = "range"; range.min = c.min ?? 0; range.max = c.max ?? 1;
      range.step = c.step ?? 0.05; range.value = value;
      const pct = el("span", "", shown(c, value));
      range.oninput = () => { pct.textContent = shown(c, range.value); };
      range.onchange = () => send(c.name, Number(range.value));
      row.append(el("span", "", tr(c.label || c.name)), range, pct);
      grid.append(row);
    } else if (c.kind === "choice") {
      const row = el("div", "vol"), pick = el("select");
      for (const o of c.options || []) {
        const option = el("option", "", tr(o.label || o.value)); option.value = o.value; pick.append(option);
      }
      pick.value = value;
      pick.onchange = () => send(c.name, pick.value);
      row.append(el("span", "", tr(c.label || c.name)), pick, el("span", ""));
      grid.append(row);
    } else if (c.kind === "text") {
      const row = el("div", "vol"), field = el("input");
      field.value = value ?? ""; field.placeholder = tr(c.hint || "");
      field.autocomplete = "off"; field.spellcheck = false;
      field.onchange = () => send(c.name, field.value.trim());
      row.append(el("span", "", tr(c.label || c.name)), field, el("span", ""));
      grid.append(row);
    } else if (c.kind === "label") {
      // The one marked head is what the part is doing now: the card's top
      // says it. The rest are facts — on a card one quiet line, in the
      // drawer a row each, where there is room to read them.
      if (c.head) continue;
      if (group) {
        const row = el("div", "vol");
        row.append(el("span", "", tr(c.label || c.name)), el("b", "", tr(String(value ?? ""))), el("span", ""));
        grid.append(row);
      } else {
        if (facts.childNodes.length) facts.append(" · ");
        facts.append((tr(c.label || c.name)) + " ", el("b", "", tr(String(value ?? "—"))));
      }
    } else if (c.kind === "button") {
      const push = el("button", "mini" + (c.action === "stop" ? " yes" : ""), tr(c.label || c.name));
      push.onclick = () => send(c.name, true);
      extra.append(push);
    } else if (c.kind === "log") {
      if (value) grid.append(el("pre", "plog", String(value)));
    } else if (c.kind === "terminal") {
      if (value) grid.append(terminalView(pid, String(value)));
    }
  }
  const box = el("div", "part-controls");
  if (grid.childElementCount) box.append(grid);
  if (facts.childNodes.length) box.append(facts);
  if (extra.childElementCount) box.append(extra);
  return box.childElementCount ? box : null;
}

// A part's terminal is redrawn on every report: where the person scrolled
// to is kept, and the newest line is followed while they are at the bottom.
const termScroll = {};
// Each line is coloured by how it starts, which is all the hands say of it.
const TERM_LINES = [
  [/^\d\d:\d\d › /, "t-ask"], [/^\$ |^• /, "t-cmd"], [/^(агент|agent): /, "t-said"],
  [/^──/, "t-sep"], [/^\s*✓ /, "t-auto"], [/^\s*\? /, "t-q"], [/^\s*(✗|■)/, "t-bad"], [/^⋯/, "t-run"],
  [/^\s/, "t-out"],
];
function terminalView(pid, text) {
  const pre = el("pre", "pterm");
  for (const line of text.split("\n")) {
    const found = TERM_LINES.find(([re]) => re.test(line));
    pre.append(el("span", found ? found[1] : "", line), "\n");
  }
  const was = termScroll[pid];
  pre.onscroll = () => {
    termScroll[pid] = {top: pre.scrollTop,
                       bottom: pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4};
  };
  requestAnimationFrame(() => {
    pre.scrollTop = !was || was.bottom ? pre.scrollHeight : was.top;
  });
  return pre;
}

// The person's answer to a part, and forgetting one.
/* A part that asks for a key is asked about here, one answer per machine:
   after an update every part of it asks at once. */
async function keyAsks() {
  const box = $("key-asks");
  if (!box) return;
  let asked = [];
  try { asked = await (await fetch("/api/keys/asked")).json(); } catch { return; }
  const hosts = new Map();
  for (const a of asked) {
    const host = a.pid.split("@")[1] || a.pid;
    hosts.set(host, [...(hosts.get(host) || []), a]);
  }
  box.replaceChildren(...[...hosts].map(([host, list]) => {
    const row = el("div", "key-ask");
    const line = list.map(a => `${a.pid.split("@")[0]}: ${a.key} (${a.version || "?"})`).join(", ");
    const say = el("div", "", tr(list.some(a => a.update) ? "{host} updated and asks for keys again" : "{host} asks for keys", {host}));
    const what = el("div", "key-ask-what", line);
    const yes = el("button", "mini yes", tr("Allow")), no = el("button", "mini", tr("Refuse"));
    const answer = allow => fetch("/api/keys/answer", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({host, allow})}).then(keyAsks);
    yes.onclick = () => answer(true);
    no.onclick = () => answer(false);
    row.append(say, what, yes, no);
    return row;
  }));
}
setInterval(keyAsks, 5000); setTimeout(keyAsks, 500);

function partCall(pid, what, btn) {
  btn.disabled = true;
  api(`bodies/${encodeURIComponent(pid)}/${what}`, {method: "POST"}).catch(() => { btn.disabled = false; });
}

/* Forgetting a window takes its key: that page goes back to pairing, and if
   it is this very page, so does the person. So it is asked once, and the
   question survives the redraws that every report causes. */
let unpairing = null;
function partRow(pid, b) {
  const row = el("div", "partrow");
  row.append(el("span", "pdot " + b.state));
  row.append(el("b", "pname", (PART_LABEL[b.channel] || b.channel || pid) + (pid === webPid ? tr(" · this") : "")));
  row.append(el("span", "pstate " + b.state, PART_STATE[b.state] || b.state));
  // The list is the registry, the strip the controls: pointing at a row
  // shows which card on the strip it is.
  row.onmouseenter = () => lit(pid, true);
  row.onmouseleave = () => lit(pid, false);
  const acts = el("div", "pactions");
  if (b.state === "pending") {
    const yes = el("button", "mini yes", tr("Approve")), no = el("button", "mini", tr("Reject"));
    yes.onclick = () => partCall(pid, "approve", yes);
    no.onclick = () => partCall(pid, "reject", no);
    acts.append(yes, no);
  } else if (pid.startsWith("window@") && unpairing === pid) {
    row.classList.add("asking");
    const yes = el("button", "mini yes", tr("Disconnect")), no = el("button", "mini", tr("No"));
    yes.onclick = () => { unpairing = null; partCall(pid, "forget", yes); };
    no.onclick = () => { unpairing = null; parts(state && state.bodies); };
    acts.append(el("span", "pstate", pid === webPid ? tr("this page will ask for a code") : tr("the window will ask for a code")), yes, no);
  } else if (pid.startsWith("window@")) {
    const gone = el("button", "mini", tr("Disconnect"));
    gone.title = tr("take this window's key away");
    gone.onclick = () => { unpairing = pid; parts(state && state.bodies); };
    acts.append(gone);
  } else {
    const gone = el("button", "mini", tr("Forget"));
    gone.onclick = () => partCall(pid, "forget", gone);
    acts.append(gone);
  }
  row.append(acts);
  return row;
}

function lit(pid, on) {
  for (const n of document.querySelectorAll("#partwidgets [data-pid]"))
    if (n.dataset.pid === pid) n.classList.toggle("lit", on);
}

// What a part is called on the body's carousel, and the picture beside it.
const PART_ICON = {
  voice: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/></svg>`,
  eyes: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 12S6 5.8 12 5.8 21.5 12 21.5 12 18 18.2 12 18.2 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.6"/></svg>`,
  hands: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M8 12V5.3a1.4 1.4 0 0 1 2.8 0V11"/><path d="M10.8 10.6V4.3a1.4 1.4 0 0 1 2.8 0v6.3"/><path d="M13.6 10.6V6.2a1.4 1.4 0 0 1 2.8 0V14a6 6 0 0 1-6 6h-.3a4.8 4.8 0 0 1-4.8-4.8v-3.6a1.4 1.4 0 0 1 2.8 0v2"/></svg>`,
  page: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18M6.4 6.6h.01M8.9 6.6h.01"/></svg>`,
  sources: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3.1 3.2 3.1 14.8 0 18M12 3c-3.1 3.2-3.1 14.8 0 18"/></svg>`,
  machines: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>`,
  text: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 6h14M5 10h14M5 14h9M5 18h6"/></svg>`,
};
const partIcon = b => PART_ICON[b.channel] || PART_ICON[PART_LABEL[b.channel]] || PART_ICON.machines;

function lasted(seconds) {
  if (seconds < 90) return tr("just now");
  if (seconds < 5400) return tr("{m} min", {m: Math.round(seconds / 60)});
  if (seconds < 172800) return tr("{h} h", {h: Math.round(seconds / 3600)});
  return tr("{d} d", {d: Math.round(seconds / 86400)});
}
/* One line at the foot: how long it has run and whether it fell since. */
function healthText(b) {
  const bits = [];
  if (b.version) bits.push("v" + b.version);
  if (b.started) bits.push(tr("up {for}", {for: lasted(Date.now() / 1000 - b.started)}));
  if (b.started) bits.push(b.restarts ? tr("restarts: {n}", {n: b.restarts}) : tr("no restarts"));
  if (!bits.length && b.host) bits.push(b.host);
  return bits;
}

/* The controls a part declared, in the sketch's rows: a switch on the right,
   a slider with its value, a button under them. They live inside the card. */
function bodyControls(pid, b, group = "") {
  const list = (Array.isArray(b.ui) ? b.ui : []).filter(c => (c.group || "") === group && !c.head);
  if (b.state !== "approved" || !list.length) return null;
  const box = el("div", "ctrls");
  const send = (name, value) => turn(pid, name, value);
  const shown = (c, value) => c.unit != null ? `${value} ${tr(c.unit)}` : Math.round(Number(value) * 100) + "%";
  for (const c of list) {
    const value = truth(b, c);
    if (c.kind === "toggle") {
      const row = el("div", "ctrlrow");
      row.append(el("span", "", tr(c.label || c.name)));
      const sw = el("button", "sw" + (value ? " on" : ""));
      sw.type = "button"; sw.setAttribute("role", "switch"); sw.setAttribute("aria-checked", String(Boolean(value)));
      sw.title = value ? tr("on") : tr("off");
      sw.onclick = () => send(c.name, !value);
      row.append(sw);
      box.append(row);
    } else if (c.kind === "slider") {
      const row = el("div", "ctrlslider");
      const range = el("input"); range.type = "range";
      range.min = c.min ?? 0; range.max = c.max ?? 1; range.step = c.step ?? 0.05; range.value = value;
      const out = el("b", "", shown(c, value));
      range.oninput = () => { out.textContent = shown(c, range.value); };
      range.onchange = () => send(c.name, Number(range.value));
      row.append(el("span", "", tr(c.label || c.name)), range, out);
      box.append(row);
    } else if (c.kind === "label") {
      const row = el("div", "ctrlrow");
      row.append(el("span", "", tr(c.label || c.name)), el("b", "", tr(String(value ?? "—"))));
      box.append(row);
    } else if (c.kind === "choice") {
      const row = el("div", "ctrlrow");
      const pick = el("select");
      for (const o of c.options || []) {
        const option = el("option", "", tr(o.label || o.value)); option.value = o.value; pick.append(option);
      }
      pick.value = value; pick.onchange = () => send(c.name, pick.value);
      row.append(el("span", "", tr(c.label || c.name)), pick);
      box.append(row);
    } else if (c.kind === "text") {
      const row = el("div", "ctrlrow");
      const field = el("input"); field.value = value ?? ""; field.placeholder = tr(c.hint || "");
      field.autocomplete = "off"; field.spellcheck = false;
      field.onchange = () => send(c.name, field.value.trim());
      row.append(el("span", "", tr(c.label || c.name)), field);
      box.append(row);
    } else if (c.kind === "button") {
      const push = el("button", "ctrlbtn", tr(c.label || c.name));
      push.type = "button";
      push.onclick = () => send(c.name, true);
      box.append(push);
    } else if (c.kind === "log") {
      if (value) box.append(el("pre", "plog", String(value)));
    } else if (c.kind === "terminal") {
      if (value) box.append(terminalView(pid, String(value)));
    }
  }
  return box.childElementCount ? box : null;
}

function partWidget(pid, b) {
  const card = el("div", "part pcard" + (b.state === "pending" ? " wait" : "") + (drawerPid === pid ? " open" : ""));
  card.dataset.pid = pid;
  const top = el("div", "ptop");
  const tile = el("span", "ptile");
  tile.innerHTML = partIcon(b);
  const name = el("b", "sname", nameOf(pid, b));
  name.title = [b.channel, ...(b.forms || []).map(f => FORM[f] || f), b.host,
    b.version && "v" + b.version].filter(Boolean).join(" · ");
  top.append(tile, name);
  const brain = state && state.running && state.running.version;
  if (b.version && brain && b.version !== brain) {
    const odd = el("span", "part-ver", "≠");
    odd.title = tr("the part is v{part}, the brain v{brain}", {part: b.version, brain});
    top.append(odd);
  }
  if (b.state === "approved" && (b.ui || []).some(c => c.group === "settings")) {
    const open = drawerPid === pid;
    const gear = el("button", "gear" + (open ? " on" : ""), "⚙");
    gear.type = "button";
    gear.title = open ? tr("hide settings") : tr("settings");
    gear.setAttribute("aria-label", gear.title);
    gear.setAttribute("aria-expanded", String(open));
    gear.onclick = () => { drawerPid = open ? null : pid; gear.blur(); parts(state && state.bodies); };
    top.append(gear);
  }
  card.append(top);
  const head = (b.ui || []).find(c => c.head && c.kind === "label");
  const now = b.state === "approved" && head ? tr(String(truth(b, head) ?? "")) : PART_STATE[b.state] || b.state;
  const stateLine = el("div", "sstate", now);
  if (b.state === "pending") stateLine.classList.add("wait");
  card.append(stateLine);
  const what = b.info || PART_WHAT[b.channel] || b.about;
  if (what) card.append(el("p", "sinfo", what));
  const controls = bodyControls(pid, b);
  if (controls) card.append(controls);
  if (b.state === "pending") {
    card.append(el("p", "phost", b.host || ""));
    const acts = el("div", "pacts");
    const yes = el("button", "mini yes", tr("Approve")), no = el("button", "mini", tr("No"));
    yes.onclick = () => partCall(pid, "approve", yes);
    no.onclick = () => partCall(pid, "reject", no);
    acts.append(yes, no);
    card.append(acts);
  }
  if (drawerPid === pid) {
    const d = el("div", "pdrawer");
    const top2 = el("div", "pdrawer-top");
    const close = el("button", "", "✕");
    close.type = "button"; close.title = tr("hide settings");
    close.onclick = () => { drawerPid = null; parts(state && state.bodies); };
    top2.append(el("small", "", tr("settings · {part}", {part: nameOf(pid, b)})), close);
    d.append(top2);
    const settings = controlsFor(pid, b, "settings");
    if (settings) d.append(settings);
    card.append(d);
  }
  if (b.state === "approved") {
    const foot = el("div", "sfoot");
    foot.append(el("i", "dot"));
    const health = healthText(b).join(" · ");
    const up = b.started ? tr("up {for}", {for: lasted(Date.now() / 1000 - b.started)}) : "";
    let line = b.foot || "";
    if (!line && b.channel === "web") line = b.host || "";
    if (!line && b.channel === "opencode" && b.values && b.values.agent)
      line = [b.values.agent, up].filter(Boolean).join(" · ");
    if (!line) line = [now, up].filter(Boolean).join(" · ") || health;
    const text = el("span", "", line);
    text.title = health;
    const trouble = b.values && b.values.trouble;
    if (trouble) { text.textContent = "! " + trouble; text.title = trouble; text.classList.add("fell"); }
    foot.append(text);
    card.append(foot);
  }
  if (b.hears === "all" || b.approves) {
    const badges = el("div", "badges");
    if (b.hears === "all") badges.append(el("span", "badge", tr("hears the whole conversation")));
    if (b.approves) badges.append(el("span", "badge", tr("can give leave with a button")));
    card.append(badges);
  }
  return card;
}


function partChip(pid, b) {
  const chip = el("div", "pchip " + b.state);
  chip.dataset.pid = pid;
  chip.append(el("span", "pdot " + b.state), el("b", "", nameOf(pid, b)));
  const controls = controlsFor(pid, b);
  if (controls) chip.append(controls);
  else chip.append(el("span", "pstate", PART_STATE[b.state] || b.state));
  return chip;
}

/* Whether the person is in a field inside *node*: a list open, a value
   half typed, a slider held. Only then is a redraw put off. A switch or a
   button keeps the focus after a click too, and a card that waited for that
   focus to go showed the old value until the page was reloaded. */
const typingIn = node => {
  const now = document.activeElement;
  return Boolean(now && node.contains(now) && /^(INPUT|SELECT|TEXTAREA)$/.test(now.tagName));
};

/* ── features: the brain's own capabilities, in the settings drawer ── */
let featureGroups = [], featureList = [];
const FEATURE_STATE = {running: tr("working"), off: tr("off"), error: tr("failure"), ready: tr("done")};

function featureCard(f) {
  const card = el("article", "feature"), top = el("div", "feat-top");
  top.append(el("b", "feat-name", tr(f.title || f.id)));
  const state = f.state || "off", stateText = FEATURE_STATE[state] || state;
  if (state !== "ready") top.append(el("span", "fstate " + state, stateText));
  card.append(top);
  if (f.about) card.append(el("p", "feat-about", tr(f.about)));
  if (f.note && f.note !== stateText) card.append(el("p", "feat-note", f.note));
  // The note already states the last run; a label would say it twice.
  const visible = (f.controls || []).filter(c => c.kind !== "label" || !f.note
    || !String(f.note).includes(String(c.value ?? "")));
  const controls = controlsFor("feature:" + f.id,
    {state: "approved", ui: visible, values: {}}, "",
    (name, value) => featureTurn(f.id, name, value));
  if (controls) card.append(controls);
  return card;
}

function featureDraw() {
  const box = $("features");
  if (!box) return;
  if (!featureList.length) {
    box.replaceChildren();
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const frag = document.createDocumentFragment();
  const seen = new Set();
  const groups = [...featureGroups].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  for (const g of groups) {
    const items = featureList
      .filter(f => f.group === g.id)
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    if (!items.length) continue;
    seen.add(g.id);
    frag.append(el("h3", "", tr(g.title || g.id)));
    for (const f of items) frag.append(featureCard(f));
  }
  const rest = featureList
    .filter(f => !seen.has(f.group))
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  if (rest.length) {
    frag.append(el("h3", "", tr("Other")));
    for (const f of rest) frag.append(featureCard(f));
  }
  box.replaceChildren(frag);
}

function featureLoad() {
  return api("features").then(answer => {
    featureGroups = Array.isArray(answer && answer.groups) ? answer.groups : [];
    featureList = Array.isArray(answer && answer.features) ? answer.features : [];
    featureDraw();
  }).catch(() => {
    featureGroups = [];
    featureList = [];
    const box = $("features");
    if (!box) return;
    box.hidden = false;
    box.replaceChildren(el("p", "note featurefail", tr("could not read it")));
  });
}

function featureTurn(id, name, value) {
  return api(`features/${encodeURIComponent(id)}/act`, {method: "POST", body: {name, value}})
    .then(answer => {
      if (!answer || !answer.ok || !answer.feature) { featureLoad(); return; }
      const at = featureList.findIndex(f => f.id === id);
      if (at < 0) { featureLoad(); return; }
      featureList[at] = answer.feature;
      featureDraw();
    })
    .catch(() => { featureLoad(); });
}

// Where a part lives is shown apart from what it is: built-in ones live and
// fall with the brain, so nothing here can be let in, cut off or forgotten.
function builtIn() {
  const rows = [el("div", "place", tr("Built-in · in the brain"))];
  const model = $("model").textContent.trim();
  for (const [name, what] of [[tr("Memory"), tr("journal, facts, profile")],
                              [tr("Model"), model === "…" ? "" : model],
                              [tr("Page"), tr("served by the brain")]]) {
    const row = el("div", "part-top builtin");
    row.append(el("span", "pdot approved"), el("b", "part-name", name), el("span", "part-state", what));
    rows.push(row);
  }
  return rows;
}

const cards = new Map();
let drawerPid = null;

function parts(map) {
  const grid = $("partwidgets"), all = Object.entries(map || {});
  drawFleet(all);
  $("partchips").replaceChildren();
  if (!all.length) {
    grid.replaceChildren(el("p", "empty", tr("no part is connected")));
    drawDots([]);
    return;
  }
  const ordered = [...all].sort((a, b) => placeOf(...a) - placeOf(...b));
  const big = ordered.filter(([pid, b]) => !isChip(pid, b));
  if (drawerPid && !big.some(([pid]) => pid === drawerPid)) drawerPid = null;
  // A card the person is working in is left where it is: moving or
  // replacing it would close an open list or take the field being typed in.
  const fresh = big.map(([pid, b]) => {
    const kept = cards.get(pid);
    const card = kept && kept.isConnected && typingIn(kept) ? kept : partWidget(pid, b);
    cards.set(pid, card);
    return card;
  });
  const now = [...grid.children];
  if (now.length === fresh.length && fresh.every((card, i) => card === now[i] || !fresh.includes(now[i]))) {
    fresh.forEach((card, i) => { if (now[i] !== card) now[i].replaceWith(card); });
  } else {
    grid.replaceChildren(...(fresh.length ? fresh : [el("p", "empty", tr("no part is connected now"))]));
  }
  drawDots(big);
}

function drawDots(cards) {
  const dots = $("dots"), count = $("bodycount"), strip = $("partwidgets");
  if (!dots || !count || !strip) return;
  const n = cards.length;
  if (dots.children.length !== n) {
    dots.replaceChildren(...cards.map((_, i) => {
      const b = el("button"); b.type = "button";
      b.setAttribute("aria-label", tr("part {n}", {n: i + 1}));
      b.onclick = () => strip.scrollTo({left: i * strip.clientWidth, behavior: "smooth"});
      return b;
    }));
  }
  const at = () => Math.max(0, Math.min(n - 1, Math.round(strip.scrollLeft / (strip.clientWidth || 1))));
  const show = () => {
    const i = at();
    count.textContent = n ? `${i + 1} / ${n}` : "—";
    [...dots.children].forEach((d, k) => d.classList.toggle("on", k === i));
  };
  show();
  if (!strip.dataset.dots) {
    strip.dataset.dots = "1";
    strip.addEventListener("scroll", () => {
      if (strip._dotRaf) return;
      strip._dotRaf = requestAnimationFrame(() => { strip._dotRaf = 0; show(); });
    }, {passive: true});
  }
}

const tokens = text => Math.ceil(String(text ?? "").length / 4);
/* The prompt's blocks are named in English for the model; the person reads
   them in their own language. */
const BLOCK_TITLES = {
  system: tr("system"), persona: tr("persona"), laws: tr("laws"),
  memory: tr("memory"), "the canvas": tr("the canvas"),
  "the conversation": tr("the conversation"),
};
const blockTitle = t => BLOCK_TITLES[t] || tr(t);
const ROLE_COLOR = {system: "var(--accent)", user: "var(--info)", assistant: "var(--ok)",
                    running: "var(--warn)", failed: "var(--bad)"};
const kNum = n => n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
function ctxBit(title, text, cls, from) {
  const bit = el("div", "ctxbit");
  const role = String(cls || "").split(" ").find(x => ROLE_COLOR[x]);
  if (role) bit.style.setProperty("--role", ROLE_COLOR[role]);
  if (title) bit.append(el("b", "", blockTitle(title)));
  bit.append(el("small", "", kNum(tokens(text))), el("p", "", text));
  if (from) bit.append(el("i", "from", from));
  return bit;
}
function ctxSec(title, count) {
  const row = el("div", "msec");
  row.append(el("b", "", title));
  if (count != null && count !== "") row.append(el("span", "", String(count)));
  return row;
}
function ctxKv(label, value) {
  const row = el("div", "kv");
  row.append(el("span", "", label), el("b", "", value));
  return row;
}
// Where a paragraph comes from, read off how it opens. What matches nothing
// is the persona: it is what the prompt is built from when nothing else adds.
const SOURCES = [
  [/^\[found in memory/, "heare-memory · recall"],
  [/^\[/, "heare-memory · always"],
  [/^The canvas/, "api/canvas.py"],
  [/^The laws/, "core/laws.py"],
  [/^(Your body|No bodies)/, "prompt/body.py"],
];
const sourceOf = text => (SOURCES.find(([opens]) => opens.test(text)) || [0, "prompt/persona.py"])[1];
// The prompt is built of paragraphs; a block of data names itself: «[what — …]».
function promptBits(text) {
  return text.split(/\n{2,}/).filter(p => p.trim()).map(p => {
    const named = p.match(/^\[([^\]\n]*?)(?: — [^\]\n]*)?\]\n?/);
    return named ? ctxBit(named[1], p.slice(named[0].length), "system", sourceOf(p))
      : ctxBit("", p, "system", sourceOf(p));
  });
}
function verbBit(verb) {
  const v = verb.function || verb;
  const takes = Object.keys((v.parameters || v.input_schema || {}).properties || {});
  return ctxBit(v.name, v.description || "", "system", takes.length ? `(${takes.join(", ")})` : "");
}
const ROLES = {user: () => tr("Person"), assistant: () => tr("Assistant")};
let ctxSeen = "", ctxNow = null, ctxPicked = null;  // null: follow the newest turn
function ctxMeasure(turn) {
  const size = m => tokens(m.content || "");
  const system = turn.messages.filter(m => m.role === "system").reduce((s, m) => s + size(m), 0);
  const talk = turn.messages.reduce((s, m) => s + size(m), 0) - system;
  const verbs = tokens(JSON.stringify(turn.verbs || []));
  return {system, talk, verbs, total: system + talk + verbs};
}
function ctxCause(turn, before) {
  const had = new Set((before ? before.messages : []).map(m => m.content));
  const fresh = turn.messages.filter(m => m.role !== "system" && !had.has(m.content));
  const files = fresh.flatMap(m => [...(m.content || "").matchAll(/^«(.+?)»(?::| \()/gm)].map(x => x[1]));
  return {fresh: new Set(fresh.map(m => m.content)), files};
}
function ctxTimeline(turns, shown) {
  const row = el("div", "timeline"), top = Math.max(...turns.map(t => ctxMeasure(t).total), 1);
  turns.forEach((turn, i) => {
    const size = ctxMeasure(turn), before = i ? ctxMeasure(turns[i - 1]).total : size.total;
    const change = size.total - before, tick = el("button", i === shown ? "tick on" : "tick");
    tick.type = "button";
    const stack = el("span", "stack");
    [["verbs", size.verbs], ["system", size.system], ["talk", size.talk]].forEach(([cls, n]) => {
      const part = el("i", cls); part.style.height = `${n / top * 100}%`; stack.append(part);
    });
    tick.append(el("em", change > 0 ? "up" : change < 0 ? "down" : "", i && change ? `${change > 0 ? "+" : ""}${change}` : ""), stack);
    tick.onclick = () => { ctxPicked = i === turns.length - 1 ? null : turn.at; drawContext(); };
    row.append(tick);
  });
  return row;
}
const runningFor = started => {
  if (!started) return "—";
  const mins = Math.max(0, Math.round((Date.now() / 1000 - started) / 60));
  return mins < 60 ? tr("{m} min", {m: mins}) : tr("{h} h {m} min", {h: Math.floor(mins / 60), m: mins % 60});
};
function drawContext() {
  const now = ctxNow, turns = now.turns || [];
  const main = el("section", "ctxmain"), side = el("aside", "ctxside");
  let shown = turns.findIndex(t => t.at === ctxPicked);
  if (shown < 0) { shown = turns.length - 1; ctxPicked = null; }
  const turn = turns[shown];
  if (turn) {
    const before = turns[shown - 1], size = ctxMeasure(turn), all = turn.messages;
    const cause = before ? ctxCause(turn, before) : {fresh: new Set(), files: []};
    const change = before ? size.total - ctxMeasure(before).total : 0;
    const used = size.system + size.talk;
    main.append(ctxSec(tr("Context"), (used / Math.max(now.limit, 1) * 100).toFixed(1) + "%"));
    const gauge = el("div", "gauge");
    gauge.append(el("b", "", kNum(size.total)), el("span", "", tr("tokens")));
    if (change) gauge.append(el("span", change > 0 ? "delta up" : "delta down", `${change > 0 ? "+" : ""}${change}`));
    cause.files.forEach(name => gauge.append(el("span", "delta file", name)));
    gauge.append(el("time", "", new Date(turn.at * 1000).toLocaleTimeString()));
    const fill = el("div", used > now.limit ? "fill over" : "fill");
    const part = (n, cls) => { const i = el("i", cls); i.style.width = `${n / Math.max(used, now.limit) * 100}%`; return i; };
    fill.append(part(size.system), part(size.talk, "talk"));
    const legend = el("div", "legend");
    legend.append(el("span", "", `${tr("System prompt")} ${kNum(size.system)}`),
                  el("span", "talk", `${tr("Conversation")} ${kNum(size.talk)}`),
                  el("span", "verbs", `${tr("verbs")} ${kNum(size.verbs)}`),
                  el("span", "budget", `${used} / ${now.limit}`));
    main.append(gauge, fill, legend, ctxTimeline(turns, shown));
    const system = all.filter(m => m.role === "system");
    const bits = system.flatMap(m => promptBits(m.content || ""));
    if (bits.length) {
      main.append(ctxSec(tr("What is in the prompt"), bits.length));
      const list = el("div", "bitlist");
      bits.forEach(b => list.append(b));
      main.append(list);
    }
    const talk = all.filter(m => m.role !== "system");
    if (talk.length) {
      main.append(ctxSec(tr("Conversation"), kNum(size.talk)));
      const list = el("div", "bitlist");
      talk.forEach(m => list.append(ctxBit(ROLES[m.role] ? ROLES[m.role]() : m.role, m.content || "",
        `${m.role}${cause.fresh.has(m.content) ? " fresh" : ""}`, "core.history")));
      main.append(list);
    }
    if (turn.verbs && turn.verbs.length) {
      main.append(ctxSec(tr("verbs"), turn.verbs.length));
      const list = el("div", "bitlist");
      turn.verbs.forEach(v => list.append(verbBit(v)));
      main.append(list);
    }
  } else {
    main.append(el("div", "note", tr("No turn yet.")));
  }

  const live = state || {}, run = live.running || {}, s = live.session || {};
  side.append(ctxSec(tr("This run")));
  side.append(ctxKv(tr("Session"), tr("turns: {n}", {n: s.turns || 0})),
             ctxKv(tr("Replies"), String(s.turns || 0)),
             ctxKv(tr("Running"), runningFor(s.started)),
             ctxKv(tr("Version"), run.version ? `v${run.version}${run.commit ? " · " + run.commit : ""}` : "—"),
             ctxKv(tr("model"), ($("model").textContent || "—").trim()),
             ctxKv(tr("around"), ($("around").textContent || "—").trim()),
             ctxKv(tr("reply"), ($("speed").textContent || "—").trim()));
  side.append(ctxSec(tr("Machines"), machineList.length || ""));
  if (machineList.length) {
    for (const m of machineList) side.append(ctxKv(m.alias || m.name, tr("parts: {n}", {n: (m.parts || []).length})));
  } else side.append(el("p", "note", tr("no part is connected")));
  side.append(ctxSec(tr("Hands"), (now.tasks || []).length));
  if (!(now.tasks || []).length) side.append(el("p", "note", tr("No tasks yet.")));
  for (const t of now.tasks || []) side.append(ctxKv(`#${t.id} ${t.executor || ""}`, t.status));

  const grid = el("div", "ctxgrid");
  grid.append(main, side);
  $("page-context").replaceChildren(grid);
}

async function showContext() {
  if (DEMO) { ctxNow = DEMO_CONTEXT; ctxSeen = JSON.stringify(DEMO_CONTEXT); drawContext(); return; }
  const now = await api("context").catch(() => null);
  if (!now) return;
  const seen = JSON.stringify(now);
  if (seen === ctxSeen) return;
  ctxSeen = seen; ctxNow = now;
  drawContext();
}
setInterval(() => { if (!$("page-context").hidden) showContext(); }, 3000);


function openDetails() {
  openSheet(tr("Details"), "details");
  $("page-context").hidden = false;
  showContext();
}

let born = null;
const voiceOf = s => {
  const part = Object.values(s.bodies || {}).find(b => b.channel === "voice" && b.state === "approved");
  if (!part) return s.controls.voice || null;
  return {mic: true, speaker: true, ...(part.values || {})};
};
const refresh = () => api("state").then(s => {
  // The assistant was restarted: this page is from before, and may not
  // know what the new one can do. A stale page once showed nothing that
  // was drawn until it was reloaded by hand.
  if (born && s.session.started !== born) return location.reload();
  born = s.session.started;
  state = s; phase = s.phase; voice = voiceOf(s);
  show(); vitals(s); drawTopbar(); tasks(s.tasks, s.permission, s.question); parts(s.bodies);
  heldNow();
  mindSummary();
});

/* the canvas: one screen of widgets, changed in place */
const SHEET = [...document.styleSheets[0].cssRules].filter(r => r.selectorText && r.selectorText.startsWith(".w ")).map(r => r.cssText).join("\n");
const NAMES = ["--bg","--card","--card-2","--line","--ink","--soft","--accent","--accent-ink","--ok","--warn","--bad","--info","--sans","--mono"];
// Read from the chat, not the page: a live card wears the same colours.
const vars = () => { const c = getComputedStyle($("chat")); return NAMES.map(v => `${v}:${c.getPropertyValue(v)}`).join(";"); };
// What a live widget may ask for. It runs in a sealed frame and reaches
// the page only through these: the machine's state, never the person's
// conversation or memory, and it can never speak as the person.
const MAY_GET = /^(system|state|tasks)$/;
const BRIDGE = `<script>
const heare = { _n: 0, _w: {},
  get(path) { return new Promise((ok, no) => { const id = ++this._n; this._w[id] = [ok, no]; parent.postMessage({heare: "get", id, path}, "*"); }); } };
addEventListener("message", e => { const w = heare._w[e.data && e.data.id]; if (!w) return; delete heare._w[e.data.id]; e.data.error ? w[1](new Error(e.data.error)) : w[0](e.data.data); });
new ResizeObserver(() => parent.postMessage({heare: "size", h: document.documentElement.scrollHeight}, "*")).observe(document.documentElement);
<\/script>`;
// The frame's body carries the widget's id, so a rule written for
// `#blk-clock .big` reaches the inside of a live widget too.
const framed = (html, id) => `<!doctype html><meta charset="utf-8"><style>:root{color-scheme:dark;${vars()}} *{box-sizing:border-box} html,body{margin:0;background:transparent;color:var(--ink);font:14px/1.55 var(--sans);overflow:hidden} ${SHEET}</style>${BRIDGE}<body class="w" id="blk-${id}">${html}`;

// The page is a body too: it knocks so the person sees it among the parts,
// and knocks again whenever its stream reconnects (a brain restarted).
const knock = () => api("bodies", {method: "POST", body: {
  channel: "web", forms: ["input", "output"], about: "a page in the browser",
  ui: [
    {name: "aloud", kind: "toggle", label: tr("Hear the reply"), value: aloud},
    {name: "state", kind: "label", label: "State", value: "this window", head: true},
  ]}}).then(answer => { webPid = (answer && answer.pid) || webPid; }).then(() => api("input", {method: "POST", body: {
    kind: "state", channel: "web", values: {aloud}}})).catch(() => {});
if (!DEMO) knock();
addEventListener("message", ev => {
  const m = ev.data, frame = [...document.querySelectorAll("#flow iframe, #pinpreview iframe, #sheet iframe, #livefull iframe")].find(f => f.contentWindow === ev.source);
  if (!m || !m.heare || !frame) return;
  if (m.heare === "size") {
    if (frame.id !== "livefull-frame") frame.style.height = Math.min(Math.max(m.h, 40), 900) + "px";
  }
  else if (m.heare === "get") {
    if (!MAY_GET.test(String(m.path))) return ev.source.postMessage({id: m.id, error: "not allowed"}, "*");
    api(m.path).then(data => ev.source.postMessage({id: m.id, data}, "*"), err => ev.source.postMessage({id: m.id, error: String(err)}, "*"));
  }
});

/* ── controls ── */
const turn = (part, name, value) => DEMO
  ? Promise.resolve().then(() => {
      const body = state && state.bodies && state.bodies[part];
      if (!body) return;
      body.values = {...(body.values || {}), [name]: value};
      if (name === "mic" && voice) voice.mic = !!value;
      parts(state.bodies);
      show(); drawTopbar();
    })
  : api("controls", {method: "POST", body: {part, name, value}}).catch(() => {});
const confirming = on => { $("stop").hidden = on; $("confirm").hidden = !on; };
$("stop").onclick = () => confirming(true);
$("stop-no").onclick = () => confirming(false);
$("stop-yes").onclick = () => api("stop", {method: "POST"}).then(() => { confirming(false); aside(tr("stopped")); });

/* ── talking to it, and hearing it back ── */
const speech = window.speechSynthesis;
/* Every open page sees every reply, but only the page that asked says it
   aloud: two pages with "Чути" on were two voices reading one answer. */
let askedHere = false;
const ask = text => { askedHere = true; const files = pending.map(f => f.id); pending = []; showPending(); return api("message", {method: "POST", body: {text, files}}); };
const speak = (text, channel) => {
  if (!aloud || !text || !speech || (channel !== "screen" && channel !== "web")) return;
  speech.cancel();
  const said = new SpeechSynthesisUtterance(text);
  said.lang = LOCALE;
  speech.speak(said);
};
const setAloud = on => {
  aloud = !!on;
  localStorage.setItem("heare.aloud", aloud ? "1" : "0");
    if (!aloud && speech) speech.cancel();
  if (webPid) api("input", {method: "POST", body: {
    kind: "state", channel: "web", values: {aloud}}}).catch(() => {});
  drawTopbar();
};

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const hear = $("hear");
if (!Recognition) hear.hidden = true;
else {
  const rec = new Recognition();
  rec.lang = LOCALE; rec.interimResults = false; rec.continuous = false;
  let listening = false;
  rec.onresult = e => {
    const text = [...e.results].map(r => r[0].transcript).join(" ").trim();
    if (text) { askedHere = true; api("input", {method: "POST", body: {kind: "heard", channel: "screen", text, addressed: true}}); }
  };
  rec.onend = rec.onerror = () => { listening = false; hear.classList.remove("on"); };
  hear.onclick = () => {
    if (listening) { rec.stop(); return; }
    if (speech) speech.cancel();
    try { rec.start(); listening = true; hear.classList.add("on"); } catch (e) {}
  };
}

/* ── memory: a summary in the pult, the whole of it in a drawer ── */
let mind = null, mindOpen = false, mindTimer = null, mindSeq = 0;
let mindTab = "facts", mindJournalSeq = 0, mindSearchSeq = 0;
const mindDirty = {user: false, threads: false, goals: false};
const MIND_PROFILE = ["user", "threads", "goals"];
const MIND_TABS = ["facts", "reminders", "journal", "search", "hearing", "profile", "sources", "backups"];
const MIND_SOURCE = {
  legacy: tr("from the old memory"),
  consolidated: tr("from words"),
  user_confirmed: tr("you confirmed"),
  user_said: tr("you said"),
  model_inferred: tr("the review's conclusion"),
  tool_output: tr("from a tool"),
};

const mindSource = v => MIND_SOURCE[v] || (v ? String(v) : "—");
const mindDate = sec => {
  const d = new Date(Number(sec) * 1000);
  return Number.isFinite(d.getTime())
    ? d.toLocaleDateString(LOCALE, {day: "2-digit", month: "2-digit", year: "numeric"})
    : "—";
};
const mindTime = sec => {
  const d = new Date(Number(sec) * 1000);
  return Number.isFinite(d.getTime())
    ? d.toLocaleString(LOCALE, {day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"})
    : "—";
};
const mindIn = sec => {
  const diff = Number(sec) - Date.now() / 1000;
  if (!Number.isFinite(diff)) return "";
  if (diff <= 0) return tr("overdue");
  if (diff < 60) return tr("in less than a minute");
  if (diff < 3600) return tr("in {m} min", {m: Math.round(diff / 60)});
  if (diff < 86400) return tr("in {h} h", {h: Math.round(diff / 3600)});
  return tr("in {d} d", {d: Math.round(diff / 86400)});
};
const mindReview = v => {
  if (!v) return tr("not yet");
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(v));
  return m ? m[3] + "." + m[2] : String(v);
};
const mindStamp = v => {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isFinite(d.getTime())
    ? d.toLocaleString(LOCALE, {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"})
    : "—";
};

function mindPaintSummary(d) {
  const c = (d && d.counts) || {};
  $("mind-pending").textContent = c.pending != null ? c.pending : "—";
  $("mind-pending").className = c.pending ? "warn" : "";
  $("mind-confirmed").textContent = c.confirmed != null ? c.confirmed : "—";
  $("mind-reminders").textContent = c.reminders != null ? c.reminders : "—";
  $("mind-review").textContent = d ? mindReview(d.review && d.review.last) : "—";
}
function mindSummary() {
  return api("mind").then(d => { mind = d; mindPaintSummary(d); }).catch(() => {});
}
function mindChips(d) {
  const c = (d && d.counts) || {};
  const chip = (label, value) => {
    const e = el("span", "mind-chip");
    e.append(document.createTextNode(label + " "), el("b", "", value == null ? "—" : String(value)));
    return e;
  };
  $("mind-chips").replaceChildren(
    chip(tr("awaiting a decision"), c.pending),
    chip(tr("facts"), c.confirmed),
    chip(tr("reminders"), c.reminders),
    chip(tr("review"), mindReview(d && d.review && d.review.last)),
    chip(tr("consolidated"), mindStamp(d && d.consolidation && d.consolidation.last)),
  );
}

function mindSec(title, count) {
  const e = el("div", "mind-sec");
  e.append(el("b", "", title));
  if (count != null) e.append(el("span", "", String(count)));
  return e;
}
// A step that cannot be undone asks on the button itself: the second click
// does it. window.confirm is not shown in the app's window — it answers «no»
// in silence, and «Забути» did nothing there (24 October).
function twice(button, asking, run) {
  const label = button.textContent;
  let armed = null;
  button.onclick = () => {
    if (armed) { clearTimeout(armed); armed = null; button.textContent = label; run(); return; }
    button.textContent = asking;
    armed = setTimeout(() => { armed = null; button.textContent = label; }, 4000);
  };
}
function mindAction(button, path, opts, err) {
  button.disabled = true;
  err.textContent = "";
  api(path, opts)
    .then(() => { mindLoad(true); mindSummary(); })
    .catch(() => { button.disabled = false; err.textContent = tr("failed — try again"); });
}
function mindFact(f, pending) {
  const card = el("div", "mind-fact" + (pending ? " pending" : ""));
  card.append(el("p", "", f.text || ""));
  const meta = el("div", "mind-meta");
  meta.append(el("span", "", mindSource(f.source)));
  if (typeof f.confidence === "number") meta.append(el("span", "", tr("confidence {p}%", {p: Math.round(f.confidence * 100)})));
  const at = f.valid_from || f.created_at;
  if (at) meta.append(el("span", "", mindDate(at)));
  card.append(meta);
  const actions = el("div", "mind-actions");
  const err = el("span", "mind-err", "");
  if (pending) {
    const yes = el("button", "mini yes", tr("Confirm"));
    yes.type = "button";
    yes.onclick = () => mindAction(yes, "mind/facts/" + f.id + "/confirm", {method: "POST"}, err);
    const no = el("button", "mini", tr("Reject"));
    no.type = "button";
    no.onclick = () => mindAction(no, "mind/facts/" + f.id + "/reject", {method: "POST"}, err);
    actions.append(yes, no, err);
  } else {
    const forget = el("button", "mini", tr("Forget"));
    forget.type = "button";
    twice(forget, tr("Forget for sure?"),
          () => mindAction(forget, "mind/facts/" + f.id + "/reject", {method: "POST"}, err));
    actions.append(forget, err);
  }
  card.append(actions);
  return card;
}
function mindFacts(d) {
  const facts = (d && d.facts) || {};
  const pending = facts.pending || [], confirmed = facts.confirmed || [];
  const box = $("mind-panel-facts");
  box.replaceChildren(mindSec(tr("Awaiting a decision"), pending.length));
  if (!pending.length) box.append(el("p", "mind-empty", tr("nothing awaits a decision.")));
  else for (const f of pending) box.append(mindFact(f, true));
  box.append(mindSec(tr("Settled facts"), confirmed.length));
  if (!confirmed.length) box.append(el("p", "mind-empty", tr("no settled facts yet.")));
  else for (const f of confirmed) box.append(mindFact(f, false));
}
function mindReminder(r) {
  const card = el("div", "mind-reminder");
  card.append(el("p", "", r.what || ""));
  const meta = el("div", "mind-meta");
  if (r.not_before) {
    meta.append(el("span", "", mindTime(r.not_before)));
    meta.append(el("span", "", mindIn(r.not_before)));
  }
  card.append(meta);
  const actions = el("div", "mind-actions");
  const err = el("span", "mind-err", "");
  const cancel = el("button", "mini", tr("Cancel"));
  cancel.type = "button";
  cancel.onclick = () => mindAction(cancel, "mind/reminders/" + r.id + "/cancel", {method: "POST"}, err);
  actions.append(cancel, err);
  card.append(actions);
  return card;
}
function mindReminders(d) {
  const list = (d && d.reminders) || [];
  const box = $("mind-panel-reminders");
  box.replaceChildren(mindSec(tr("Reminders"), list.length));
  if (!list.length) {
    const empty = el("div", "mind-empty");
    empty.append(el("p", "", tr("nothing to remind.")));
    empty.append(el("p", "", tr("say «remind me…», and it will appear here.")));
    box.append(empty);
    return;
  }
  for (const r of list) box.append(mindReminder(r));
}
function mindCount(key, limit) {
  const ta = $("mind-profile-" + key), out = $("mind-count-" + key);
  if (!ta || !out) return;
  const n = ta.value.length;
  out.textContent = n + " / " + (limit == null ? "—" : limit);
  out.classList.toggle("over", limit != null && n > limit);
}
function mindProfile(d) {
  const p = (d && d.profile) || {};
  const blocks = p.blocks || {}, limits = p.limits || {};
  for (const key of MIND_PROFILE) {
    const ta = $("mind-profile-" + key);
    if (!ta) continue;
    const limit = limits[key];
    if (limit != null) ta.maxLength = limit;
    if (!mindDirty[key] && document.activeElement !== ta) ta.value = blocks[key] || "";
    mindCount(key, limit);
  }
}
/* One renderer for anything remembered: a journal entry, a search hit. It
   knows nothing about where the item came from — only its title, when it
   happened, what it says and the quiet tags beside it. */
function mindItem(item) {
  const row = el("div", "mind-item");
  const top = el("div", "mind-item-top");
  top.append(el("span", "mind-item-title", item.title || item.kind || "—"));
  if (item.kind) top.append(el("span", "mind-item-kind", String(item.kind)));
  top.append(el("span", "mind-item-time", mindStamp(item.at)));
  row.append(top);
  if (item.text) row.append(el("p", "mind-item-text", String(item.text)));
  const meta = (Array.isArray(item.meta) ? item.meta : []).filter(Boolean).map(String);
  if (meta.length) row.append(el("div", "mind-item-meta", meta.join(" · ")));
  return row;
}
const mindWhen = v => {
  const t = Date.parse(v);
  return Number.isFinite(t) ? t : -Infinity;
};
function mindJournalLoad() {
  if (!mindOpen) return;
  const box = $("mind-panel-journal");
  const seq = ++mindJournalSeq;
  if (!box.childElementCount) box.replaceChildren(el("p", "mind-empty", tr("reading the journal…")));
  api("mind/journal?limit=80").then(d => {
    if (seq !== mindJournalSeq || mindTab !== "journal") return;
    const items = ((d && d.items) || []).slice().sort((a, b) => mindWhen(b.at) - mindWhen(a.at));
    box.replaceChildren(mindSec(tr("Journal"), items.length));
    if (!items.length) { box.append(el("p", "mind-empty", tr("the journal is empty."))); return; }
    for (const item of items) {
      const row = mindItem(item);
      if (item.kind === "heard" || item.kind === "said") {
        const actions = el("div", "mind-actions");
        const err = el("span", "mind-err", "");
        const forget = el("button", "mini", tr("Forget"));
        forget.type = "button";
        forget.title = tr("It will be neither in the journal nor in search");
        twice(forget, tr("Forget for sure?"),
              () => mindAction(forget, "mind/journal/forget", {method: "POST", body: {ids: [item.id]}}, err));
        actions.append(forget, err);
        row.append(actions);
      }
      box.append(row);
    }
  }).catch(() => {
    if (seq !== mindJournalSeq || mindTab !== "journal") return;
    const fail = el("div", "mind-empty");
    fail.append(el("p", "mind-err", tr("could not read the journal.")));
    const retry = el("button", "mini", tr("Try again"));
    retry.type = "button";
    retry.onclick = () => mindJournalLoad();
    fail.append(retry);
    box.replaceChildren(mindSec(tr("Journal")), fail);
  });
}
/* ── «Слух»: what the ear heard, to hear again and mark ── */
let hearingSeq = 0, hearingAudio = null;
const HEAR_LABEL = {me: tr("to the assistant"), background: tr("background")};
function hearingPlay(button, pieces) {
  if (hearingAudio) { hearingAudio.pause(); const was = hearingAudio.button; hearingAudio = null;
    if (was) was.textContent = "▶"; if (was === button) return; }
  const queue = pieces.map(p => "/api/heard/" + p.name + ".wav");
  const audio = new Audio();
  hearingAudio = audio; audio.button = button;
  button.textContent = "■";
  const next = () => {
    if (hearingAudio !== audio || !queue.length) { if (hearingAudio === audio) { hearingAudio = null; button.textContent = "▶"; } return; }
    audio.src = queue.shift();
    audio.play().catch(() => { hearingAudio = null; button.textContent = "▶"; });
  };
  audio.onended = next;
  next();
}
function hearingMark(r, label, truth, err) {
  return api("heard/" + encodeURIComponent(r.turn), {method: "POST", body: {label, truth}})
    .then(() => hearingLoad())
    .catch(() => { err.textContent = tr("failed — try again"); });
}
function hearingRow(r) {
  const row = el("div", "mind-item hear-row" + (r.label ? " " + r.label : ""));
  const top = el("div", "mind-item-top");
  const play = el("button", "mini play", "▶");
  play.type = "button"; play.title = tr("listen"); play.setAttribute("aria-label", tr("listen"));
  play.onclick = () => hearingPlay(play, r.pieces);
  top.append(play);
  if (r.label) top.append(el("span", "mind-item-kind", HEAR_LABEL[r.label] || r.label));
  else if (r.dropped) top.append(el("span", "mind-item-kind", tr("dropped as made up")));
  top.append(el("span", "mind-item-time",
    mindTime(r.at) + " · " + tr("{s} s", {s: (r.ms / 1000).toFixed(1)}) + (r.pieces.length > 1 ? " · " + trn(r.pieces.length, "{n} piece", "{n} pieces") : "")));
  row.append(top);
  const heard = r.dropped ? r.pieces.map(p => p.text).join(" ") : r.text;
  if (r.truth && r.truth !== heard) {
    row.append(el("p", "was", heard || "—"));
    row.append(el("p", "mind-item-text", r.truth));
  } else {
    row.append(el("p", "mind-item-text" + (heard ? "" : " none"), heard || tr("nothing recognised")));
  }
  const actions = el("div", "mind-actions");
  const err = el("span", "mind-err", "");
  const right = el("button", "mini yes", r.dropped ? tr("It was me") : tr("Right"));
  right.type = "button";
  right.title = tr("said to the assistant, and recognised so");
  right.onclick = () => { right.disabled = true; hearingMark(r, "me", heard, err); };
  const fix = el("button", "mini", tr("Correct"));
  fix.type = "button";
  fix.onclick = () => {
    const form = el("form", "fix");
    const input = el("input");
    input.value = r.truth || heard || ""; input.setAttribute("aria-label", tr("what was really said"));
    input.placeholder = tr("what you really said");
    const save = el("button", "mini yes", tr("Save")); save.type = "submit";
    form.append(input, save);
    form.onsubmit = ev => { ev.preventDefault(); const t = input.value.trim();
      if (!t) { err.textContent = tr("write what was said"); return; }
      save.disabled = true; hearingMark(r, "me", t, err); };
    actions.replaceWith(form, actions);
    fix.disabled = true;
    input.focus();
  };
  const noise = el("button", "mini", tr("Background"));
  noise.type = "button";
  noise.title = tr("not to the assistant: TV, other people, silence");
  noise.onclick = () => { noise.disabled = true; hearingMark(r, "background", null, err); };
  actions.append(right, fix, noise, err);
  row.append(actions);
  return row;
}
function hearingLoad() {
  if (!mindOpen) return;
  const box = $("mind-panel-hearing");
  const seq = ++hearingSeq;
  if (!box.childElementCount) box.replaceChildren(el("p", "mind-empty", tr("reading the recordings…")));
  api("heard?limit=80").then(d => {
    if (seq !== hearingSeq || mindTab !== "hearing") return;
    const replies = (d && d.replies) || [];
    box.replaceChildren(mindSec(tr("What I heard"), tr("marked {done} of {all}", {done: d.marked || 0, all: d.total || 0})));
    box.append(el("p", "mind-empty", tr("Listen and mark: «Right» — said to me and heard so; «Correct» — what it really was; «Background» — not to me. The marks stay with the recording, so I can learn what to listen for.")));
    if (!replies.length) { box.append(el("p", "mind-empty", tr("no recordings yet — they appear as soon as I hear something."))); return; }
    for (const r of replies) box.append(hearingRow(r));
  }).catch(() => {
    if (seq !== hearingSeq || mindTab !== "hearing") return;
    const fail = el("div", "mind-empty");
    fail.append(el("p", "mind-err", tr("could not read the recordings.")));
    const retry = el("button", "mini", tr("Try again"));
    retry.type = "button"; retry.onclick = () => hearingLoad();
    fail.append(retry);
    box.replaceChildren(mindSec(tr("What I heard")), fail);
  });
}
/* ── «Копії»: the owner's memory in one file, and back ── */
let backupsSeq = 0;
const backupWhen = iso => {
  const d = new Date(iso);
  return Number.isFinite(d.getTime())
    ? d.toLocaleString(LOCALE, {day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"})
    : "—";
};
function backupStatus(text, ok) {
  const s = $("backup-status");
  s.className = "mind-err" + (ok ? " ok" : "");
  s.textContent = text;
}
function backupRow(b) {
  const row = el("div", "mind-item backup-row");
  const top = el("div", "mind-item-top");
  top.append(el("span", "mind-item-time", b.made ? backupWhen(b.made) : b.name));
  if (b.broken) top.append(el("span", "mind-item-kind", tr("damaged")));
  else if (!b.sound) top.append(el("span", "mind-item-kind", tr("without sound")));
  top.append(el("span", "mind-item-time", tr("{mb} MB", {mb: (b.size / 1048576).toFixed(1)})));
  row.append(top);
  if (!b.broken) {
    const c = b.counts || {};
    row.append(el("p", "mind-item-text", [trn(c.facts || 0, "{n} fact", "{n} facts"),
      trn(c.days || 0, "{n} day of journal", "{n} days of journal"),
      trn(c.recordings || 0, "{n} recording", "{n} recordings"),
      trn(c.reminders || 0, "{n} reminder", "{n} reminders")].join(" · ")));
  }
  const actions = el("div", "mind-actions");
  const take = el("a", "mini", tr("Download"));
  take.href = "/api/backups/" + encodeURIComponent(b.name);
  take.setAttribute("download", b.name);
  actions.append(take);
  if (!b.broken) {
    const back = el("button", "mini", tr("Restore"));
    back.type = "button";
    back.title = tr("the current memory is set aside in backups/before-restore-…");
    twice(back, tr("Sure? I will set the current aside"), () => backupRestore(b, back));
    actions.append(back);
  }
  row.append(actions);
  return row;
}
function backupsLoad() {
  if (!mindOpen) return;
  const seq = ++backupsSeq;
  const list = $("backup-list");
  if (!list.childElementCount) list.replaceChildren(el("p", "mind-empty", tr("reading the backups…")));
  api("backups").then(d => {
    if (seq !== backupsSeq) return;
    const items = (d && d.backups) || [];
    list.replaceChildren(mindSec(tr("Backups"), items.length));
    if (!items.length) list.append(el("p", "mind-empty", tr("no backups yet — make the first with the button above.")));
    for (const b of items) list.append(backupRow(b));
    $("backup-folder").textContent = d && d.folder ? tr("kept in {folder}", {folder: d.folder}) : "";
    const r = d && d.restored, note = $("backup-restored");
    note.hidden = !r;
    note.textContent = r ? tr("Restored {at} from the backup of {made}. What was there before is in backups/{aside}.", {at: backupWhen(r.at), made: backupWhen(r.made), aside: r.aside}) : "";
  }).catch(() => {
    if (seq !== backupsSeq) return;
    list.replaceChildren(el("p", "mind-err", tr("could not read the backups.")));
  });
}
function backupMake() {
  const button = $("backup-make");
  button.disabled = true;
  backupStatus(tr("making a backup…"), true);
  api("backups", {method: "POST", body: {sound: $("backup-sound").checked}})
    .then(b => { backupStatus(tr("done: {mb} MB", {mb: (b.size / 1048576).toFixed(1)}), true); backupsLoad(); })
    .catch(() => backupStatus(tr("could not make a backup")))
    .finally(() => { button.disabled = false; });
}
function backupBring(ev) {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  backupStatus(tr("checking {file}…", {file: file.name}), true);
  fetch("/api/backups/bring", {method: "POST", body: file, credentials: "same-origin",
                               headers: {"Content-Type": "application/zip"}})
    .then(r => r.ok ? r.json() : r.text().then(t => Promise.reject(t)))
    .then(() => { backupStatus(tr("the backup is brought in — it is in the list"), true); backupsLoad(); })
    .catch(why => backupStatus(tr("not taken: {why}", {why: typeof why === "string" && why ? why : tr("the file does not fit")})));
}
function backupRestore(b, button) {
  button.disabled = true;
  backupStatus(tr("putting the backup in place — the brain restarts…"), true);
  const asked = Date.now();
  // The page reloads when the brain comes back; it should open here again.
  try { sessionStorage.setItem("heare-restoring", "1"); } catch (_) {}
  api("backups/" + encodeURIComponent(b.name) + "/restore", {method: "POST"}).then(() => {
    let tries = 0;
    const wait = () => {
      if (++tries > 60) { backupStatus(tr("the brain did not come back within a minute — look at the app")); return; }
      setTimeout(() => api("backups").then(d => {
        const r = d && d.restored;
        if (!r || r.name !== b.name || new Date(r.at).getTime() < asked - 1000) return wait();
        backupStatus(tr("restored"), true);
        backupsLoad(); mindLoad(true); mindSummary();
      }).catch(wait), 1000);
    };
    wait();
  }).catch(why => { button.disabled = false; backupStatus(tr("not restored: {why}", {why: typeof why === "string" ? why : ""})); });
}
$("backup-make").onclick = backupMake;
$("backup-file").onchange = backupBring;
function mindSearchSubmit(ev) {
  ev.preventDefault();
  const q = $("mind-search-q").value.trim();
  const status = $("mind-search-status"), said = $("mind-search-said");
  const list = $("mind-search-results");
  if (!q) {
    status.className = "mind-search-status bad";
    status.textContent = tr("write what to recall.");
    return;
  }
  const seq = ++mindSearchSeq;
  status.className = "mind-search-status";
  status.textContent = tr("searching…");
  said.hidden = true;
  said.textContent = "";
  api("mind/search?q=" + encodeURIComponent(q)).then(d => {
    if (seq !== mindSearchSeq) return;
    status.className = "mind-search-status";
    status.textContent = "";
    const text = (d && d.said) || "";
    said.hidden = !text;
    said.textContent = text;
    const items = (d && d.items) || [];
    if (!items.length) { list.replaceChildren(el("p", "mind-empty", tr("I recall nothing."))); return; }
    list.replaceChildren(...items.map(mindItem));
  }).catch(() => {
    if (seq !== mindSearchSeq) return;
    status.className = "mind-search-status bad";
    status.textContent = tr("the search failed");
  });
}
function mindShow(tab) {
  mindTab = tab;
  for (const t of MIND_TABS) {
    const on = t === tab;
    $("mind-tab-" + t).classList.toggle("on", on);
    $("mind-tab-" + t).setAttribute("aria-selected", on ? "true" : "false");
    $("mind-tab-" + t).tabIndex = on ? 0 : -1;
    $("mind-panel-" + t).hidden = !on;
  }
  if (tab === "profile" && mind) mindProfile(mind);
  if (tab === "journal") mindJournalLoad();
  if (tab === "hearing") hearingLoad();
  if (tab === "backups") backupsLoad();
  if (tab === "sources") sourcesLoad();
}
const sourceNotes = {};
const sourceRunning = {};
const sourceAbout = () => ({
  "claude-code": tr("Notes Claude Code keeps about you and your projects."),
  "engram": tr("Engram's records of your work with coding agents."),
  "claude-mem": tr("Observations claude-mem made during coding sessions."),
});
const sourceKinds = () => ({
  user: [tr("Who you are"), tr("Notes about you: role, habits, preferences.")],
  feedback: [tr("How you like to work"), tr("What you asked agents to do differently.")],
  decision: [tr("Your decisions"), tr("Choices you made and why.")],
});
function sourceImport(name, kind, n, busy) {
  const about = sourceKinds()[kind] || [kind, ""];
  const row = el("div", "source-row");
  const words = el("div", "source-words");
  words.append(el("strong", "", about[0]));
  if (about[1]) words.append(el("span", "", about[1]));
  const go = el("button", "btn", trn(n, "Import {n} note", "Import {n} notes"));
  go.type = "button";
  go.disabled = busy;
  go.onclick = () => { go.disabled = true; api(`mind/sources/${encodeURIComponent(name)}/import`, {method: "POST", body: {kinds: [kind]}}).catch(() => { go.disabled = false; }); };
  row.append(words, go);
  return row;
}
function sourceWorking(run) {
  const box = el("div", "source-working");
  box.append(el("strong", "", run.total ? tr("Importing: {done} of {total} notes", run) : tr("Importing: getting started")));
  box.append(el("span", "", tr("You can close this. It keeps going, and what it finds will wait under Facts.")));
  const bar = el("div", "source-bar");
  const fill = el("i");
  fill.style.width = run.total ? `${Math.round(run.done / run.total * 100)}%` : "4%";
  bar.append(fill);
  box.append(bar);
  return box;
}
function sourceBanner() {
  const running = Object.entries(sourceRunning);
  const line = $("imports-now");
  line.hidden = !running.length;
  line.textContent = running.map(([name, run]) => tr("Importing {name}: {done} of {total}", {name, done: run.done, total: run.total || "…"})).join(" · ");
}
function sourceUndo(s) {
  const row = el("div", "source-row undo");
  const words = el("div", "source-words");
  words.append(el("strong", "", trn(s.imported, "{n} fact came from here", "{n} facts came from here")));
  words.append(el("span", "", tr("Removes them from heare, even the ones you confirmed. {name} itself stays as it is.", {name: s.name})));
  const undo = el("button", "btn quiet", tr("Remove from heare"));
  undo.type = "button";
  undo.onclick = () => {
    if (!undo.dataset.sure) { undo.dataset.sure = "1"; undo.textContent = tr("Press again to remove"); return; }
    api(`mind/sources/${encodeURIComponent(s.name)}/undo`, {method: "POST"}).then(sourcesLoad).catch(() => {});
  };
  row.append(words, undo);
  return row;
}
async function sourcesLoad() {
  const list = await api("mind/sources").catch(() => []);
  for (const s of list) {
    if (s.running) sourceRunning[s.name] = s.running; else delete sourceRunning[s.name];
  }
  sourceBanner();
  const panel = $("mind-panel-sources");
  panel.replaceChildren(el("p", "mind-said", tr("Other tools keep notes too. Importing reads their notes and sends them to your model, {model}. Nothing is saved until you confirm it under Facts. The tools' own notes never change.", {model: $("model")?.textContent?.trim() || tr("the current model")})));
  for (const s of list) {
    const block = el("section", "mind-block");
    block.append(el("h3", "", s.name));
    if (sourceAbout()[s.name]) block.append(el("p", "mind-count", sourceAbout()[s.name]));
    if (!s.found) { block.append(el("p", "mind-count", tr("not on this machine"))); panel.append(block); continue; }
    block.append(el("p", "mind-count", tr("{n} records", {n: s.records})));
    const kinds = Object.entries(s.waiting);
    const run = sourceRunning[s.name];
    if (run) block.append(sourceWorking(run));
    kinds.forEach(([kind, n]) => block.append(sourceImport(s.name, kind, n, Boolean(run))));
    if (!kinds.length && !run) block.append(el("p", "mind-count", s.read ? trn(s.read, "All imported: {n} note read.", "All imported: {n} notes read.") : tr("Nothing to import: no notes here are about you.")));
    if (s.imported && !run) block.append(sourceUndo(s));
    if (sourceNotes[s.name] && !run) block.append(el("p", "mind-count", sourceNotes[s.name]));
    panel.append(block);
  }
}
function sourceProgress(e) {
  if (e.finished) {
    delete sourceRunning[e.name];
    sourceNotes[e.name] = e.error ? tr("It stopped: {why}", {why: e.error}) : tr("Read {read}, found {added}", {read: e.read, added: e.added});
  } else sourceRunning[e.name] = {done: e.done, total: e.total};
  sourceBanner();
  if (mindTab === "sources" && mindOpen) sourcesLoad();
}
api("mind/sources").then(list => { list.forEach(s => { if (s.running) sourceRunning[s.name] = s.running; }); sourceBanner(); }).catch(() => {});
function mindLoad(force) {
  if (!force && !mindOpen) return Promise.resolve();
  const seq = ++mindSeq;
  return api("mind").then(d => {
    if (seq !== mindSeq) return;
    mind = d;
    $("mind-status").textContent = "";
    $("mind-status").className = "mind-status";
    mindPaintSummary(d);
    mindChips(d);
    mindFacts(d);
    mindReminders(d);
    mindProfile(d);
    if (mindTab === "journal") mindJournalLoad();
  }).catch(() => {
    if (seq !== mindSeq) return;
    $("mind-status").textContent = tr("memory is unavailable now");
    $("mind-status").className = "mind-status bad";
  });
}
function mindClose() {
  const was = mindOpen;
  mindOpen = false;
  clearInterval(mindTimer);
  mindTimer = null;
  if (was) $("membtn").focus();
  $("mind").classList.remove("on");
  $("mind").setAttribute("aria-hidden", "true");
  
}
function mindOpenDrawer() {
  rightShut();
  mindOpen = true;
  $("mind").classList.add("on");
  $("mind").setAttribute("aria-hidden", "false");
  $("veil").classList.add("on");
  
  mindLoad();
  clearInterval(mindTimer);
  mindTimer = setInterval(() => { if (mindOpen) mindLoad(); }, 15000);
  setTimeout(() => $("mind-close").focus(), 30);
}
$("mind-close").onclick = () => shut();
$("mind-search-form").onsubmit = mindSearchSubmit;
for (const t of MIND_TABS) {
  $("mind-tab-" + t).onclick = () => mindShow(t);
  $("mind-tab-" + t).addEventListener("keydown", ev => {
    if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
    ev.preventDefault();
    const i = MIND_TABS.indexOf(t);
    const next = MIND_TABS[(i + (ev.key === "ArrowRight" ? 1 : MIND_TABS.length - 1)) % MIND_TABS.length];
    mindShow(next);
    $("mind-tab-" + next).focus();
  });
}
for (const key of MIND_PROFILE) {
  const ta = $("mind-profile-" + key), save = $("mind-save-" + key), err = $("mind-err-" + key);
  ta.addEventListener("input", () => {
    mindDirty[key] = true;
    err.className = "mind-err";
    err.textContent = "";
    const limits = (mind && mind.profile && mind.profile.limits) || {};
    mindCount(key, limits[key]);
  });
  save.onclick = () => {
    save.disabled = true;
    err.className = "mind-err";
    err.textContent = "";
    api("mind/profile", {method: "PUT", body: {key, text: ta.value}})
      .then(() => {
        mindDirty[key] = false;
        save.disabled = false;
        err.className = "mind-err ok";
        err.textContent = tr("saved");
        mindLoad();
        mindSummary();
      })
      .catch(() => {
        save.disabled = false;
        err.className = "mind-err";
        err.textContent = tr("failed — try again");
      });
  };
}

/* ── settings ── */
const anyOpen = () => ["system", "mind", "settings", "sheet"].some(id => $(id).classList.contains("on"));
function systemClose() {
  const was = $("system").classList.contains("on");
  $("system").classList.remove("on");
  $("system").inert = true;
  $("system").setAttribute("aria-hidden", "true");
  document.body.classList.remove("sysopen");
  $("idbtn").setAttribute("aria-expanded", "false");
  if (!anyOpen()) $("veil").classList.remove("on");
  if (was) $("idbtn").focus();
}
function systemOpen() {
  shut();
  $("system").classList.add("on");
  $("system").inert = false;
  $("system").setAttribute("aria-hidden", "false");
  document.body.classList.add("sysopen");
  $("idbtn").setAttribute("aria-expanded", "true");
  $("veil").classList.add("on");
  setTimeout(() => $("systemclose").focus(), 30);
}
function openSheet(title, which = "screen") {
  previewHide();
  if (narrow.matches && $("system").classList.contains("on")) systemClose();
  mindClose();
  $("settings").classList.remove("on");
  $("settings").setAttribute("aria-hidden", "true");
  sessionOpen = which === "sessions";
  $("sheettitle").textContent = title;
  $("sessions-panel").hidden = which !== "sessions";
  $("stage").hidden = which === "sessions" || which === "parts";
  $("sheet").classList.toggle("wide", which === "details");
  $("sheet").classList.add("on");
  $("sheet").setAttribute("aria-hidden", "false");
  $("sheet").inert = false;
  $("veil").classList.add("on");
}
function closeSheet() {
  sessionOpen = false;
  $("page-context").hidden = true;
  $("sheet").classList.remove("on");
  $("sheet").setAttribute("aria-hidden", "true");
  $("sheet").inert = true;
  if (!anyOpen()) $("veil").classList.remove("on");
}
// A right drawer never closes the left menu: they are two different
// conversations. The veil and Escape still close everything.
const rightShut = () => {
  mindClose();
  closeSheet();
  $("settings").classList.remove("on");
  $("settings").setAttribute("aria-hidden", "true");
  if (!anyOpen()) $("veil").classList.remove("on");
};
const shut = () => {
  mindClose();
  closeSheet();
  systemClose();
  for (const id of ["veil", "settings"]) $(id).classList.remove("on");
  $("settings").setAttribute("aria-hidden", "true");
};
$("veil").onclick = shut;
document.addEventListener("keydown", e => { if (e.key === "Escape") shut(); });

function voicePid() {
  const part = Object.entries(state?.bodies || {}).find(([, b]) => b.channel === "voice" && b.state === "approved");
  return part ? part[0] : null;
}
function drawTopbar() {
  const mic = $("mic"), sound = $("sound");
  if (mic) {
    const on = !!(voice && voice.mic);
    mic.classList.toggle("on", on); mic.setAttribute("aria-pressed", String(on));
  }
  if (sound) { sound.classList.toggle("on", aloud); sound.setAttribute("aria-pressed", String(aloud)); }
  drawLast();
  const chip = $("modelchip"), model = $("model");
  if (chip) chip.textContent = model && model.textContent ? model.textContent.split(" · ")[0] : "";
}
$("mic").onclick = () => {
  const pid = voicePid();
  if (!pid || !voice) { toast(tr("no microphone")); return; }
  turn(pid, "mic", !voice.mic);
};
$("sound").onclick = () => setAloud(!aloud);
$("idbtn").onclick = () => { if ($("system").classList.contains("on")) systemClose(); else systemOpen(); };
$("systemclose").onclick = () => systemClose();
$("opensettings").onclick = () => openSettings();
$("settingsclose").onclick = () => shut();
$("sheetclose").onclick = closeSheet;
$("membtn").onclick = () => mindOpenDrawer();
$("detbtn").onclick = openDetails;
$("setbtn").onclick = openSettings;
$("sessionsbtn").onclick = openSessions;
$("system").inert = true;
$("sheet").inert = true;

function keyRows(have) {
  const list = $("keylist");
  if (!have) {
    list.replaceChildren(el("p", "empty", tr("could not learn where the keys come from")));
    return;
  }
  list.replaceChildren(...Object.keys(have).map(name => {
    const info = have[name] || {};
    const state = info.env ? "env" : info.stored ? "stored" : "none";
    const row = el("div", "keyrow");
    row.append(el("span", "kname", name));
    const chip = el("span", "kchip " + state,
      {env: tr("in the environment"), stored: tr("saved"), none: tr("none")}[state]);
    row.append(chip);
    if (info.env) {
      const adopt = el("button", "kadopt", tr("Use the one from the environment"));
      adopt.type = "button";
      adopt.onclick = () => adoptKey(name, adopt, chip);
      row.append(adopt);
    }
    return row;
  }));
}
function adoptKey(name, button, chip) {
  button.disabled = true;
  api("keys/adopt", {method: "POST", body: {name}})
    .then(() => {
      chip.className = "kchip stored";
      chip.textContent = tr("saved");
      button.remove();
      $("keynote").textContent = tr("{name} is taken from the environment. It counts from the next reply.", {name});
      refresh();
    })
    .catch(() => {
      $("keynote").textContent = tr("Could not take the key from the environment.");
      button.disabled = false;
    });
}
function keys() {
  api("keys").then(have => {
    const chosen = $("keyname").value;
    $("keyname").replaceChildren(...Object.keys(have).map(n => new Option(((have[n] || {}).source ? "✓ " : "— ") + n, n)));
    if (chosen) $("keyname").value = chosen;
    keyRows(have);
  }).catch(() => keyRows(null));
}
let modelListSeq = 0;
function loadModels(saved) {
  const pick = $("modelpick"), custom = $("modelcustom"), note = $("modelnote");
  const provider = $("provider").value;
  const seq = ++modelListSeq;
  custom.value = "";
  note.textContent = "";
  if (!provider) {
    pick.replaceChildren(new Option(tr("— default —"), ""));
    pick.disabled = false;
    return;
  }
  pick.disabled = true;
  pick.replaceChildren(new Option(tr("searching…"), ""));
  api("models?provider=" + encodeURIComponent(provider)).then(answer => {
    if (seq !== modelListSeq) return;
    const models = (answer && answer.models) || [];
    const frag = document.createDocumentFragment();
    frag.append(new Option(tr("— default —"), ""));
    for (const id of models) frag.append(new Option(id, id));
    pick.replaceChildren(frag);
    pick.disabled = false;
    const want = saved || "";
    if (want && models.includes(want)) pick.value = want;
    else { pick.value = ""; custom.value = want; }
  }).catch(() => {
    if (seq !== modelListSeq) return;
    pick.replaceChildren(new Option(tr("— default —"), ""));
    pick.disabled = false;
    custom.value = saved || "";
    note.textContent = tr("the list did not come — type the id.");
  });
}
function fillProviders() {
  const m = (state && state.model) || {}, offers = m.providers || [];
  const chosen = $("provider").value;
  $("provider").replaceChildren(...offers.map(p => new Option(p, p)));
  $("provider").value = chosen && offers.includes(chosen) ? chosen : (m.wanted || m.provider || "");
}
function openSettings() {
  rightShut();
  fillProviders();
  $("modelcustom").value = "";
  api("settings").then(s => loadModels(s[$("provider").value + "_model"] || ""))
    .catch(() => loadModels(""));
  $("provider").onchange = () => {
    api("settings")
      .then(s => loadModels(s[$("provider").value + "_model"] || ""))
      .catch(() => loadModels(""));
  };
  $("pair-code-box").hidden = true;
  $("pair-code-note").textContent = "";
  keys(); featureLoad();
  $("settings").classList.add("on");
  $("settings").setAttribute("aria-hidden", "false");
  $("veil").classList.add("on");
}
$("save-model").onclick = () => {
  const provider = $("provider").value;
  const chosen = $("modelcustom").value.trim() || $("modelpick").value;
  const change = {llm_provider: provider, [provider + "_model"]: chosen};
  api("settings", {method: "POST", body: change})
    .then(() => { $("modelnote").textContent = tr("Saved. It counts from the next reply."); refresh(); })
    .catch(why => { $("modelnote").textContent = tr("Not saved: {why}", {why}); });
};
$("save-key").onclick = () => {
  const name = $("keyname").value, value = $("keyvalue").value.trim();
  if (!value) return;
  $("keyvalue").value = "";
  api("keys", {method: "POST", body: {name, value}})
    .then(() => {
      $("keynote").textContent = tr("{name} is saved. It counts from the next reply.", {name});
      keys();
      // A fresh install has no provider list until a key exists; saving
      // one here gives the drawer its choices without a close and reopen.
      refresh().then(() => {
        if (!$("settings").classList.contains("on")) return;
        fillProviders();
        api("settings").then(s => loadModels(s[$("provider").value + "_model"] || ""))
          .catch(() => loadModels(""));
      }, () => {});
    })
    .catch(why => { $("keynote").textContent = tr("Not saved: {why}", {why}); });
};
/* The language of what the person hears and sees: the page comes back in it. */
$("lang").value = document.documentElement.lang;
$("lang").onchange = () => api("settings", {method: "POST", body: {language: $("lang").value}})
  .then(() => location.reload());
$("pair-code").onclick = () => {
  const button = $("pair-code"), note = $("pair-code-note");
  button.disabled = true;
  note.textContent = "";
  api("pair-code", {method: "POST"})
    .then(answer => {
      $("pair-code-value").textContent = answer.code;
      $("pair-code-copy").textContent = tr("Copy");
      $("pair-code-box").hidden = false;
    })
    .catch(why => {
      note.textContent = why === "too fast"
        ? tr("wait a couple of seconds and try again")
        : tr("could not get a code");
    })
    .finally(() => { button.disabled = false; });
};
$("pair-code-copy").onclick = () => {
  const code = $("pair-code-value").textContent;
  if (!code) return;
  navigator.clipboard.writeText(code).then(() => {
    $("pair-code-copy").textContent = tr("Copied");
    setTimeout(() => { $("pair-code-copy").textContent = tr("Copy"); }, 1500);
  }).catch(() => {});
};

// Back from a restore: the brain restarted and the page with it. Here, at
// the end, because the drawer needs everything above it defined.
let restoring = null;
try { restoring = sessionStorage.getItem("heare-restoring"); sessionStorage.removeItem("heare-restoring"); } catch (_) {}
if (restoring) { mindOpenDrawer(); mindShow("backups"); }

/* ── machines ── */
let machineList = [], updateState = null;
const machineTurn = (name, part, on) => api(
  `machines/${encodeURIComponent(name)}/parts/${encodeURIComponent(part)}`,
  {method: "POST", body: {on}});
const machineRestart = (name, part) => api(
  `machines/${encodeURIComponent(name)}/parts/${encodeURIComponent(part)}/restart`,
  {method: "POST", body: {}});
const machineRename = (name, alias) => api(
  `machines/${encodeURIComponent(name)}`, {method: "PUT", body: {alias}});
const machineForget = name => api(
  `machines/${encodeURIComponent(name)}/forget`, {method: "POST", body: {}});

async function loadMachines() {
  try {
    const answer = await api("machines");
    machineList = answer.machines || [];
    parts(state && state.bodies);
  } catch (_) {}
}

/* The machine's own line: who it is, whether it is here, and the three
   things one may do to it — name it, forget it, update it. */
function machineTop(machine) {
  const top = el("div", "mtop");
  top.append(el("span", "mdot" + (machine.state === "online" ? " on" : "")));
  const name = el("b", "mname", machine.alias || machine.name);
  if (machine.brain) name.append(el("span", "mbrain", tr("brain")));
  name.title = machine.pid;
  top.append(name, el("span", "mstate", machine.state === "online" ? tr("online") : tr("away")));

  const actions = el("span", "mactions");
  const pencil = el("button", "mact", "✎");
  pencil.type = "button"; pencil.title = tr("rename");
  pencil.onclick = () => {
    const field = el("input", "mrename");
    field.value = machine.alias || machine.name;
    top.replaceChild(field, name); field.focus(); field.select();
    let done = false;
    const save = () => {
      if (done) return; done = true;
      machineRename(machine.name, field.value.trim()).then(loadMachines).catch(loadMachines);
    };
    field.onkeydown = ev => {
      if (ev.key === "Enter") save();
      if (ev.key === "Escape") { done = true; loadMachines(); }
    };
    field.onblur = save;
  };
  actions.append(pencil);

  if (machine.keyed) {
    const drop = el("button", "mact", "✕");
    drop.type = "button"; drop.title = tr("forget");
    // Two presses, not a browser dialog: the app's window swallows those.
    drop.onclick = () => {
      if (drop.dataset.armed !== "1") {
        drop.dataset.armed = "1";
        drop.textContent = "✕?";
        drop.title = tr("forget this machine? its parts stop being trusted");
        setTimeout(() => {
          if (drop.isConnected) { drop.dataset.armed = "0"; drop.textContent = "✕"; }
        }, 4000);
        return;
      }
      machineForget(machine.name).then(loadMachines).catch(loadMachines);
    };
    actions.append(drop);
  }

  const latest = updateState && updateState.latest;
  if (latest && machine.reported && machine.version && machine.version !== latest) {
    const up = el("button", "mact", "↑");
    up.type = "button"; up.title = tr("Update");
    up.onclick = () => {
      up.disabled = true;
      api("update/machine", {
        method: "POST", body: {machine: machine.brain ? "machine@brain" : machine.pid},
      }).catch(() => { up.disabled = false; });
    };
    actions.append(up);
  }
  top.append(actions);
  return top;
}

function machineCard(machine) {
  const card = el("div", "mcard");
  card.append(machineTop(machine));
  const free = machine.disk_free != null ? `${(machine.disk_free / 1e9).toFixed(1)} GB ${tr("free")}` : "";
  const ram = machine.mem_total && machine.mem_free != null
    ? `RAM ${(machine.mem_free / 1e9).toFixed(1)}/${(machine.mem_total / 1e9).toFixed(0)} GB ${tr("free")}`
    : "";
  const seen = machine.seen
    ? `${tr("last seen")} ${new Date(machine.seen * 1000).toLocaleTimeString(LOCALE, {hour: "2-digit", minute: "2-digit"})}`
    : "";
  const load = machine.load && machine.load.length ? `${tr("load")} ${machine.load[0]}` : "";
  const parts = (machine.parts || []).length;
  const line = [machine.version ? "v" + machine.version : "", machine.platform,
                parts ? tr("parts: {n}", {n: parts}) : "", load].filter(Boolean).join(" · ");
  if (line) {
    const meta = el("div", "mmeta", line);
    meta.title = [free, ram, seen].filter(Boolean).join(" · ");
    meta.onclick = () => { meta.title = [free, ram, seen].filter(Boolean).join(" · "); toast(meta.title); };
    card.append(meta);
  }
  return card;
}

/* One part on one machine: the keeper's state, the registry's state, and
   the one action that fits it — approve, switch, or just show why it is
   off. The card on the strip is where its settings live. */
function machineRow(machine, part, entries) {
  const row = el("div", "mrow");
  const pending = entries.find(([, b]) => b.state === "pending");
  // A part this machine cannot run at all: the keeper says why, and the
  // wish is not shown as «pending» forever — its switch would lie.
  const cannot = part.why_not ? tr(part.why_not) : "";
  const desired = part.desired !== null && part.desired !== undefined
    && part.desired !== part.on && !cannot;
  const bad = part.state === "unwell";
  if (pending || desired) row.classList.add("pending");
  row.append(el("span", "mdot" + (pending || desired || bad || cannot ? " warn" : part.on ? " on" : "")));
  row.append(el("span", "name", PART_LABEL[part.channel] || tr(part.label || part.name)));
  if (entries.length > 1) row.append(el("span", "pin", "×" + entries.length));
  const entry = entries[0] && entries[0][1];
  const head = entry && (entry.ui || []).find(c => c.head && c.kind === "label");
  const now = entry && entry.state === "approved" && head ? tr(String(truth(entry, head) ?? "")) : "";
  const label = pending ? PART_STATE.pending
    : bad && part.why ? part.why
    : cannot ? cannot
    : desired ? tr("pending")
    : now || (part.on ? tr("on") : tr("off"));
  row.append(el("span", "state" + ((bad && part.why) || cannot ? " why" : ""), label));

  const end = el("span", "end");
  if (pending) {
    const yes = el("button", "mini yes", "✓");
    const no = el("button", "mini no", "✗");
    yes.type = "button"; yes.title = tr("Approve");
    no.type = "button"; no.title = tr("No");
    yes.onclick = ev => { ev.stopPropagation(); partCall(pending[0], "approve", yes); };
    no.onclick = ev => { ev.stopPropagation(); partCall(pending[0], "reject", no); };
    end.append(yes, no);
  } else {
    const sw = el("input", "mcsw");
    sw.type = "checkbox"; sw.checked = !!part.on;
    sw.disabled = !machine.reported || Boolean(cannot);
    sw.title = cannot || (part.on ? tr("turn off") : tr("turn on"));
    sw.onclick = ev => ev.stopPropagation();
    sw.onchange = () => machineTurn(machine.name, part.name, sw.checked)
      .then(loadMachines)
      .catch(() => { sw.checked = !sw.checked; });
    end.append(sw);
  }
  row.append(end);
  if (entries.length) {
    row.title = entries.map(([pid]) => pid).join(" · ");
    row.onclick = () => openPart(entries[0][0]);
  }
  return row;
}

/* The fleet: one card per machine with its parts; a page or a part that
   the keeper does not list rides in the brain's card, so it stays visible. */
function drawFleet(all) {
  const box = $("bodies");
  if (typingIn(box)) return;
  const byPid = new Map(all), used = new Set();
  const frag = document.createDocumentFragment();
  for (const machine of machineList) {
    const card = machineCard(machine);
    for (const part of machine.parts || []) {
      const entries = (part.pids || []).map(pid => [pid, byPid.get(pid)]).filter(([, b]) => b);
      for (const [pid] of entries) used.add(pid);
      card.append(machineRow(machine, part, entries));
    }
    for (const [pid, b] of all) {
      if (used.has(pid)) continue;
      const belongs = (b.machine || (b.host ? `machine@${b.host}` : "")) === machine.pid;
      if (belongs) { card.append(partRow(pid, b)); used.add(pid); }
    }
    frag.append(card);
  }
  const rest = all.filter(([pid]) => !used.has(pid));
  if (rest.length) {
    const first = frag.querySelector(".mcard");
    if (first) for (const [pid, b] of rest) first.append(partRow(pid, b));
    else {
      const card = el("div", "mcard");
      const top = el("div", "mtop");
      top.append(el("span", "mdot on"), el("b", "mname", tr("this Mac")), el("span", "mstate", tr("online")));
      card.append(top);
      for (const [pid, b] of rest) card.append(partRow(pid, b));
      frag.append(card);
    }
  }
  if (!machineList.length && !rest.length && !all.length)
    frag.append(el("p", "empty", tr("no part is connected")));
  box.replaceChildren(frag);
  $("machinecount").textContent = machineList.length ? String(machineList.length) : "";
}

/* A row points at its card: the list is the fleet, the strip the inspector. */
function openPart(pid) {
  drawerPid = pid;
  parts(state && state.bodies);
  requestAnimationFrame(() => {
    const card = document.querySelector(`#partwidgets [data-pid="${CSS.escape(pid)}"]`);
    if (!card) return;
    card.scrollIntoView({behavior: "smooth", block: "nearest"});
    card.classList.add("lit");
    setTimeout(() => card.classList.remove("lit"), 1600);
  });
}

/* ── live ── */
if (!DEMO) {
  const stream = new EventSource("/api/events");
  stream.onmessage = m => {
    const e = JSON.parse(m.data);
    if (e.kind === "sessions") { loadSessions().catch(() => {}); loadPins(); }
    else if (e.kind === "message") onMessage(e);
    else if (e.kind === "pin") loadPins();
    else if (e.kind === "heard") { writing = null; askedAt = e.at; }
    else if (e.kind === "reply") { writing = writing || line("", "it"); writing.firstChild ? writing.firstChild.textContent += e.text : writing.append(e.text); feedToEnd(); }
    else if (e.kind === "said") { writing = null; const mine = askedHere; askedHere = false; if (e.failed) aside(tr("failure: {why}", {why: e.failed})); else if (mine) speak(e.text, e.channel); }
    else if (e.kind === "overheard") aside(tr("heard «{text}» — {why}", {text: e.text, why: WHY[e.why] || tr("no name, not answering")}));
    else if (e.kind === "phase") {
      phase = e.phase; show();
      if (e.phase === "speaking" && askedAt) { speeds.push(e.at - askedAt); if (speeds.length > 20) speeds.shift(); askedAt = null; spark(); }
    }
    else if (e.kind === "around") refresh();
    else if (e.kind === "cannot") aside(tr("cannot: {what} ({why})", {what: e.what, why: e.why}));
    else if (e.kind === "verb") step(e);
    else if (e.kind === "control" && e.channel === "web" && e.name === "aloud" && (!e.part || e.part === webPid)) setAloud(e.value);
    else if (e.kind === "sources") sourceProgress(e);
    else if (e.kind === "mind") { mindSummary(); if (mindOpen) mindLoad(); }
    else if (e.kind === "update") drawUpdate(e);
    else if (e.kind === "feature") { if ($("settings").classList.contains("on")) featureLoad(); }
    else if (e.kind === "task") noteTask(e);

    if (["task", "permission", "permission_closed", "question", "question_closed", "control", "usage", "said", "key", "halted", "part"].includes(e.kind)) refresh();
  };
  stream.onerror = () => {
    const lost = tr("no connection to heare");
    $("face").setAttribute("class", "face off");
    $("nowtext").textContent = lost;
  };
  // One open does both: the page is present among the parts again after a
  // brain restart (a knock re-registers this window), and the state is fresh.
  stream.onopen = () => { knock(); refresh(); };
  $("say").onsubmit = ev => { ev.preventDefault(); const text = $("text").value.trim(); if (!text && !pending.length) return; $("text").value = ""; ask(text); };
  loadSessions().catch(() => {});
  loadPins();
  refresh(); setInterval(refresh, 15000);
  loadMachines(); setInterval(loadMachines, 15000);
} else {
  $("say").onsubmit = ev => { ev.preventDefault(); toast(tr("This is a mock")); };
  demo();
}
