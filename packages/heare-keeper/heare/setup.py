"""The setup form: a machine is configured once, then the form goes away.

The keeper serves this page on ``127.0.0.1`` only while ``setup_done`` is
not true. The page asks a few questions and hands the answers to
``install.sh``; the form never installs anything itself and never runs a
command the person typed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from heare.config import Settings

PARTS = ("eyes", "voice", "hands")
DEFAULT_PORT = 9791
PORT_TRIES = 20
LOG_TAIL = 4000

_RUN_LOCK = threading.Lock()


def want_setup(settings: Settings) -> bool:
    """The form is shown until a run has succeeded."""
    return settings.setup_done is not True


def _text(data: dict, name: str) -> str:
    value = data.get(name)
    return value.strip() if isinstance(value, str) else ""


def _port_value(value) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def validate(data: dict) -> list[str]:
    """What is wrong with the form, in the words the person will read."""
    errors: list[str] = []

    brain = data.get("brain_here")
    if not isinstance(brain, bool):
        errors.append("вкажи, чи ця машина — мозок")
        brain = False

    raw_parts = data.get("parts", [])
    if not isinstance(raw_parts, list):
        errors.append("частини мають бути списком")
        raw_parts = []
    unknown = [str(part) for part in raw_parts if part not in PARTS]
    if unknown:
        errors.append("обрано невідому частину: " + ", ".join(unknown))
    parts = [part for part in raw_parts if part in PARTS]

    if not brain:
        url = _text(data, "url")
        if not url:
            errors.append("вкажи адресу мозку")
        elif not url.startswith(("http://", "https://")):
            errors.append("адреса має починатися з http:// або https://")

        code = _text(data, "code")
        if not code:
            errors.append("вкажи код підключення")
        elif len(code) != 8:
            errors.append("код має бути 8 символів")

        if not parts:
            errors.append("обери хоча б одну частину: очі, голос або руки")
    else:
        value = data.get("port")
        if value is not None and value != "":
            port = _port_value(value)
            if port is None or not 1 <= port <= 65535:
                errors.append("порт мозку має бути від 1 до 65535")

    for name, label in (("deepseek_key", "DeepSeek"), ("groq_key", "Groq")):
        value = data.get(name)
        if value is not None and not isinstance(value, str):
            errors.append(f"ключ {label} має бути текстом")

    return errors


def command(data: dict, installer: str, wheels: str) -> list[str]:
    """The installer argv. Keys travel in the environment, not here."""
    brain = data.get("brain_here") is True
    argv = [
        "sh", installer, "--from", wheels,
        "--role", "mac" if sys.platform == "darwin" else "server",
    ]

    raw_parts = data.get("parts")
    if isinstance(raw_parts, list):
        parts = [part for part in raw_parts if part in PARTS]
        argv += ["--parts", ",".join(parts)]

    if brain:
        port = _port_value(data.get("port"))
        if port is not None:
            argv += ["--port", str(port)]
    else:
        argv += ["--url", _text(data, "url"), "--code", _text(data, "code")]
    return argv


def _remember(data: dict) -> None:
    """Write what the installer will write before it runs, so the keeper
    reads the same config while the install is still going."""
    settings = Settings()
    if data.get("brain_here") is True:
        settings.set("url", "")
        port = _port_value(data.get("port"))
        if port is not None:
            settings.set("api_port", port)
    else:
        settings.set("url", _text(data, "url"))


def _tail(text: str) -> str:
    text = text.strip()
    return text[-LOG_TAIL:] if len(text) > LOG_TAIL else text


def _run(data: dict, installer: str, wheels: str) -> tuple[bool, str]:
    env = os.environ.copy()
    # The keeper is the form's own host: restarting it would kill the page
    # before it can answer. Its plist is already in place from --setup.
    env["HEARE_FROM_SETUP"] = "1"
    for name, variable in (("deepseek_key", "HEARE_DEEPSEEK_KEY"),
                           ("groq_key", "HEARE_GROQ_KEY")):
        value = data.get(name)
        env[variable] = value if isinstance(value, str) else ""
    try:
        finished = subprocess.run(
            command(data, installer, wheels),
            env=env, capture_output=True, text=True, timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    output = (finished.stdout or "") + (finished.stderr or "")
    return finished.returncode == 0, _tail(output)


class _FormServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, installer: str, wheels: str) -> None:
        super().__init__(address, _Handler)
        self.installer = installer
        self.wheels = wheels


class _Handler(BaseHTTPRequestHandler):
    server_version = "heare-setup"

    def log_message(self, fmt: str, *args) -> None:
        pass

    def _send(self, status: int, body, content_type: str = "application/json") -> None:
        if isinstance(body, (dict, list)):
            payload = json.dumps(body, ensure_ascii=False).encode()
            content_type = "application/json; charset=utf-8"
        else:
            payload = str(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/":
            self._send(404, {"errors": ["не знаю такої сторінки"]})
            return
        self._send(200, FORM_HTML, content_type="text/html; charset=utf-8")

    def _same_origin(self) -> bool:
        """The form is a local page: no other origin may drive it."""
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        host = self.headers.get("Host") or ""
        return origin == f"http://{host}"

    def do_POST(self) -> None:
        if not self._same_origin():
            self._send(403, {"errors": ["чужа сторінка не керує цією формою"]})
            return
        kind = (self.headers.get("Content-Type") or "").lower()
        if not kind.startswith("application/json"):
            self._send(415, {"errors": ["потрібен JSON"]})
            return
        path = self.path.split("?", 1)[0]
        if path == "/close":
            self._send(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path != "/setup":
            self._send(404, {"errors": ["не знаю такої дії"]})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._send(400, {"errors": ["не розібрав запит"]})
            return
        if not isinstance(data, dict):
            self._send(400, {"errors": ["не розібрав запит"]})
            return

        errors = validate(data)
        if errors:
            self._send(400, {"errors": errors})
            return

        with _RUN_LOCK:
            try:
                _remember(data)
            except OSError as error:
                self._send(200, {"ok": False, "log": str(error)})
                return
            ok, log = _run(data, self.server.installer, self.server.wheels)
            if ok:
                Settings().set("setup_done", True)
        self._send(200, {"ok": ok, "log": log})


def _install_info() -> tuple[str, str]:
    path = Path.home() / ".heare" / "install.json"
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        return str(info["installer"]), str(info["wheels"])
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit(
            "немає ~/.heare/install.json — постав: install.sh --setup"
        ) from None


def _open(address: str) -> None:
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen(
            [opener, address],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def serve(installer: str | None = None, wheels: str | None = None,
          port: int | None = None) -> None:
    if installer is None or wheels is None:
        found_installer, found_wheels = _install_info()
        installer = installer or found_installer
        wheels = wheels or found_wheels

    start = port if port is not None else int(Settings().setup_port or DEFAULT_PORT)
    httpd: _FormServer | None = None
    for candidate in range(start, start + PORT_TRIES + 1):
        try:
            httpd = _FormServer(("127.0.0.1", candidate), installer, wheels)
            break
        except OSError:
            continue
    if httpd is None:
        raise OSError(f"немає вільного порту від {start} до {start + PORT_TRIES}")

    address = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"форма: {address}", file=sys.stderr, flush=True)
    if not os.environ.get("HEARE_NO_BROWSER"):
        _open(address)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


FORM_HTML = """<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>heare — налаштування</title>
<style>
  :root {
    --bg: #131217;
    --card: #1c1a21;
    --line: #302c37;
    --ink: #ece8e2;
    --soft: #96909e;
    --accent: #9d7cff;
    --bad: #ef7a6b;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  html, body { min-height: 100%; }
  body {
    margin: 0;
    padding: 48px 20px 64px;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    background:
      radial-gradient(900px 520px at 18% -12%, rgba(157, 124, 255, .18), transparent 62%),
      radial-gradient(700px 460px at 112% 112%, rgba(157, 124, 255, .09), transparent 62%),
      var(--bg);
    color: var(--ink);
    font-family: var(--sans);
    -webkit-font-smoothing: antialiased;
  }
  .card {
    width: 100%;
    max-width: 620px;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 20px;
    padding: 34px 34px 30px;
    box-shadow: 0 28px 80px rgba(0, 0, 0, .45), inset 0 1px 0 rgba(255, 255, 255, .03);
    animation: rise .55s cubic-bezier(.22, .8, .3, 1) both;
  }
  @keyframes rise {
    from { opacity: 0; transform: translateY(14px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @media (prefers-reduced-motion: reduce) {
    .card { animation: none; }
  }
  .mark {
    font-family: var(--mono);
    font-size: 12px;
    letter-spacing: .22em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 18px;
  }
  .mark::before {
    content: "";
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-right: 9px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 14px rgba(157, 124, 255, .9);
    vertical-align: 1px;
  }
  h1 {
    margin: 0 0 8px;
    font-size: clamp(26px, 5vw, 34px);
    font-weight: 650;
    letter-spacing: -.02em;
    line-height: 1.1;
  }
  .lede {
    margin: 0 0 30px;
    color: var(--soft);
    font-size: 15px;
    line-height: 1.55;
  }
  fieldset {
    margin: 0 0 22px;
    padding: 0;
    border: 0;
  }
  legend, .label {
    display: block;
    margin-bottom: 10px;
    color: var(--soft);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .14em;
    text-transform: uppercase;
  }
  .segmented {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .segmented label {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 46px;
    padding: 0 14px;
    border: 1px solid var(--line);
    border-radius: 11px;
    color: var(--soft);
    font-size: 15px;
    cursor: pointer;
    transition: border-color .18s ease, color .18s ease, background .18s ease;
  }
  .segmented label:hover { border-color: #443e50; color: var(--ink); }
  .segmented input { position: absolute; opacity: 0; pointer-events: none; }
  .segmented input:checked + span {
    color: var(--ink);
  }
  .segmented label:has(input:checked) {
    border-color: rgba(157, 124, 255, .75);
    background: rgba(157, 124, 255, .1);
    color: var(--ink);
  }
  .segmented label:has(input:focus-visible) {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .parts {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
  }
  .parts label {
    display: flex;
    align-items: center;
    gap: 9px;
    min-height: 44px;
    padding: 0 12px;
    border: 1px solid var(--line);
    border-radius: 11px;
    color: var(--soft);
    font-size: 14px;
    cursor: pointer;
    transition: border-color .18s ease, color .18s ease, background .18s ease;
  }
  .parts label:hover { border-color: #443e50; color: var(--ink); }
  .parts input {
    appearance: none;
    width: 16px;
    height: 16px;
    margin: 0;
    border: 1px solid #4b4558;
    border-radius: 5px;
    background: transparent;
    display: grid;
    place-content: center;
    flex: none;
  }
  .parts input::after {
    content: "";
    width: 8px;
    height: 5px;
    border-left: 1.6px solid var(--bg);
    border-bottom: 1.6px solid var(--bg);
    transform: rotate(-45deg) translateY(-1px);
    opacity: 0;
  }
  .parts input:checked { background: var(--accent); border-color: var(--accent); }
  .parts input:checked::after { opacity: 1; }
  .parts label:has(input:checked) {
    border-color: rgba(157, 124, 255, .55);
    color: var(--ink);
    background: rgba(157, 124, 255, .06);
  }
  .parts input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .field { margin-bottom: 18px; }
  .field input {
    width: 100%;
    min-height: 46px;
    padding: 0 14px;
    border: 1px solid var(--line);
    border-radius: 11px;
    background: #17151c;
    color: var(--ink);
    font-family: var(--sans);
    font-size: 15px;
    transition: border-color .18s ease, box-shadow .18s ease;
  }
  .field input::placeholder { color: #625c6c; }
  .field input:focus {
    outline: none;
    border-color: rgba(157, 124, 255, .75);
    box-shadow: 0 0 0 3px rgba(157, 124, 255, .13);
  }
  .field code, .help code {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--ink);
    background: #262230;
    border: 1px solid var(--line);
    border-radius: 5px;
    padding: 2px 6px;
  }
  .help {
    margin: 8px 2px 0;
    color: var(--soft);
    font-size: 12.5px;
    line-height: 1.5;
  }
  button {
    width: 100%;
    min-height: 50px;
    margin-top: 8px;
    padding: 0 20px;
    border: 1px solid rgba(157, 124, 255, .55);
    border-radius: 12px;
    background: linear-gradient(180deg, #a98cff, #8f6bff);
    color: #15121c;
    font-family: var(--sans);
    font-size: 15px;
    font-weight: 650;
    letter-spacing: .01em;
    cursor: pointer;
    transition: filter .18s ease, transform .12s ease;
  }
  button:hover { filter: brightness(1.06); }
  button:active { transform: translateY(1px); }
  button:disabled { filter: grayscale(.35) brightness(.8); cursor: progress; }
  button.quiet {
    margin-top: 18px;
    background: transparent;
    border-color: var(--line);
    color: var(--ink);
    font-weight: 550;
  }
  button.quiet:hover { border-color: #4b4558; }
  .bad { color: var(--bad); font-size: 13.5px; line-height: 1.5; margin: 12px 2px 0; }
  .tick {
    width: 52px;
    height: 52px;
    display: grid;
    place-items: center;
    margin-bottom: 20px;
    border-radius: 50%;
    border: 1px solid rgba(157, 124, 255, .55);
    background: rgba(157, 124, 255, .12);
    color: var(--accent);
    font-size: 24px;
  }
  pre {
    max-height: 260px;
    overflow: auto;
    margin: 16px 0 0;
    padding: 14px 16px;
    border: 1px solid var(--line);
    border-radius: 11px;
    background: #141218;
    color: #c5becd;
    font-family: var(--mono);
    font-size: 12px;
    line-height: 1.55;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .hint { margin: 14px 2px 0; color: var(--soft); font-size: 12.5px; }
  @media (max-width: 480px) {
    body { padding: 20px 12px 40px; }
    .card { padding: 26px 20px 22px; border-radius: 16px; }
    .parts { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<main class="card" id="form-screen">
  <div class="mark">heare</div>
  <h1>Налаштування машини</h1>
  <p class="lede">Кілька питань — і форма більше не потрібна. Усе зберігається лише на цій машині.</p>
  <form id="setup" autocomplete="off">
    <fieldset>
      <legend>Ця машина — мозок?</legend>
      <div class="segmented">
        <label><input type="radio" name="brain_here" id="brain-yes" value="yes" checked><span>Так, це мозок</span></label>
        <label><input type="radio" name="brain_here" id="brain-no" value="no"><span>Ні, це тіло</span></label>
      </div>
    </fieldset>

    <fieldset>
      <legend>Частини на цій машині</legend>
      <div class="parts">
        <label><input type="checkbox" name="parts" value="eyes" checked><span>Очі</span></label>
        <label><input type="checkbox" name="parts" value="voice" checked><span>Голос</span></label>
        <label><input type="checkbox" name="parts" value="hands" checked><span>Руки</span></label>
      </div>
    </fieldset>

    <div id="remote-fields" hidden>
      <div class="field">
        <label class="label" for="url">Адреса мозку</label>
        <input id="url" name="url" type="text" inputmode="url"
               placeholder="https://heare.example.dev" spellcheck="false">
        <p class="help">Там, де живе мозок. Можна з портом: <code>http://127.0.0.1:9790</code></p>
      </div>
      <div class="field">
        <label class="label" for="code">Код підключення</label>
        <input id="code" name="code" type="text" maxlength="8"
               placeholder="8 символів" autocapitalize="characters" spellcheck="false">
        <p class="help">На машині мозку виконай <code>heare-brain code</code> — код живе 5 хвилин.</p>
      </div>
    </div>

    <div id="brain-fields">
      <div class="field">
        <label class="label" for="deepseek_key">Ключ DeepSeek</label>
        <input id="deepseek_key" name="deepseek_key" type="password"
               placeholder="можна пропустити" autocomplete="off">
      </div>
      <div class="field">
        <label class="label" for="groq_key">Ключ Groq</label>
        <input id="groq_key" name="groq_key" type="password"
               placeholder="можна пропустити" autocomplete="off">
      </div>
      <div class="field">
        <label class="label" for="port">Порт мозку</label>
        <input id="port" name="port" type="number" min="1" max="65535" value="9790">
      </div>
    </div>

    <button type="submit" id="go">Підключити</button>
    <p class="bad" id="form-error" hidden></p>
  </form>
</main>

<main class="card" id="done-screen" hidden>
  <div class="tick">✓</div>
  <h1>Готово</h1>
  <p class="lede" id="done-what"></p>
  <p class="hint">Форма більше не потрібна: закрий її, і далі все тихо стежить наглядач.</p>
  <button class="quiet" id="close">Закрити</button>
</main>

<main class="card" id="fail-screen" hidden>
  <div class="mark">heare</div>
  <h1>Не вийшло</h1>
  <p class="bad" id="fail-why"></p>
  <pre id="fail-log"></pre>
  <button class="quiet" id="back">Спробувати ще</button>
</main>

<script>
  const form = document.getElementById('setup');
  const brainYes = document.getElementById('brain-yes');
  const brainNo = document.getElementById('brain-no');
  const remoteFields = document.getElementById('remote-fields');
  const brainFields = document.getElementById('brain-fields');
  const formScreen = document.getElementById('form-screen');
  const doneScreen = document.getElementById('done-screen');
  const failScreen = document.getElementById('fail-screen');
  const note = document.getElementById('form-error');
  const go = document.getElementById('go');

  function sync() {
    const here = brainYes.checked;
    remoteFields.hidden = here;
    brainFields.hidden = !here;
  }
  brainYes.addEventListener('change', sync);
  brainNo.addEventListener('change', sync);
  sync();

  function showFail(why, log) {
    formScreen.hidden = true;
    doneScreen.hidden = true;
    failScreen.hidden = false;
    document.getElementById('fail-why').textContent = why;
    document.getElementById('fail-log').textContent = log || '';
  }

  function showDone(here, parts) {
    const names = { eyes: 'очі', voice: 'голос', hands: 'руки' };
    const list = parts.map((part) => names[part] || part).join(', ');
    document.getElementById('done-what').textContent = here
      ? 'Мозок на цій машині' + (list ? ', частини: ' + list : '') + '.'
      : 'Частини: ' + list + '. Машина підключена до мозку.';
    formScreen.hidden = true;
    doneScreen.hidden = false;
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const here = brainYes.checked;
    const parts = Array.from(form.querySelectorAll('input[name="parts"]:checked'))
      .map((input) => input.value);
    const payload = {
      brain_here: here,
      parts,
      url: here ? '' : form.url.value.trim(),
      code: here ? '' : form.code.value.trim(),
      deepseek_key: here ? form.deepseek_key.value : '',
      groq_key: here ? form.groq_key.value : '',
    };
    if (here) {
      const raw = form.port.value.trim();
      payload.port = raw ? Number(raw) : 9790;
    }

    note.hidden = true;
    go.disabled = true;
    go.textContent = 'Перевіряю…';
    try {
      const answer = await fetch('/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await answer.json();
      if (!answer.ok) {
        note.textContent = (result.errors || ['не розібрав відповідь']).join(' · ');
        note.hidden = false;
        return;
      }
      if (result.ok) {
        showDone(here, parts);
      } else {
        showFail('Встановлення не завершилось.', result.log || '');
      }
    } catch (error) {
      showFail('Не достукався до наглядача.', String(error));
    } finally {
      go.disabled = false;
      go.textContent = 'Підключити';
    }
  });

  document.getElementById('back').addEventListener('click', () => {
    failScreen.hidden = true;
    formScreen.hidden = false;
  });

  document.getElementById('close').addEventListener('click', async () => {
    try {
      await fetch('/close', { method: 'POST' });
    } catch (error) {
      // The form is already gone; nothing left to close.
    }
    document.querySelector('#done-screen .hint').textContent =
      'Форму закрито. Це вікно можна закрити.';
    document.getElementById('close').disabled = true;
  });
</script>
</body>
</html>
"""
