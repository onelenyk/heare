"""The setup form: a machine is configured once, then the form goes away.

The keeper serves this page on ``127.0.0.1`` only while ``setup_done`` is
not true. The page asks a few questions and hands the answers to
``install.sh``; the form never installs anything itself and never runs a
command the person typed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from heare import keys, machine
from heare.config import Settings
from heare.pairing import pair

PARTS = ("eyes", "voice", "hands")
ROLES = ("all", "body", "brain")
INSTALLER = Path(__file__).with_name("installer.html")
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
        # The key this form asks for is DeepSeek's; choosing it is choosing
        # the provider, so the brain must not keep a provider it has no key
        # for and fall back on every turn.
        if keys.get("DEEPSEEK_API_KEY"):
            settings.set("llm_provider", "deepseek")
    else:
        settings.set("url", _text(data, "url"))


def _keep_keys(data: dict) -> list[str]:
    """Store the keys the person typed. A store that will not answer is a
    note in the log, not a failed install: the installer also gets them."""
    notes: list[str] = []
    for field, name in (("deepseek_key", "DEEPSEEK_API_KEY"),
                        ("groq_key", "GROQ_API_KEY")):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            keys.store(name, value)
        except OSError as error:
            notes.append(f"{field}: {error}")
    return notes


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
        path = self.path.split("?", 1)[0]
        if path == "/keys":
            self._send(200, keys.view())
            return
        if path == "/machine":
            self._send(200, _machine())
            return
        if path != "/":
            self._send(404, {"errors": ["не знаю такої сторінки"]})
            return
        self._send(200, INSTALLER.read_text(encoding="utf-8"),
                   content_type="text/html; charset=utf-8")

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
        if path not in ("/setup", "/keys", "/part", "/key", "/role", "/done"):
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

        if path == "/keys":
            self._adopt(data)
            return
        if path == "/part":
            self._turn(data)
            return
        if path == "/key":
            self._key(data)
            return
        if path == "/role":
            self._role(data)
            return
        if path == "/done":
            Settings().set("setup_done", True)
            self._send(200, _machine())
            return

        if not self.server.installer:
            self._send(400, {"errors": ["тут немає встановлювача — частини вмикаються перемикачами"]})
            return
        errors = validate(data)
        if errors:
            self._send(400, {"errors": errors})
            return

        with _RUN_LOCK:
            notes = _keep_keys(data)
            try:
                _remember(data)
            except OSError as error:
                self._send(200, {"ok": False, "log": "\n".join([*notes, str(error)])})
                return
            ok, log = _run(data, self.server.installer, self.server.wheels)
            if ok:
                Settings().set("setup_done", True)
        self._send(200, {"ok": ok, "log": "\n".join([*notes, log])})

    def _turn(self, data: dict) -> None:
        name, on = data.get("name"), data.get("on")
        if name not in machine.PARTS or not isinstance(on, bool):
            self._send(400, {"errors": ["невідома частина"]})
            return
        try:
            machine.turn(Path(Settings().parts_dir), name, on)
        except (ValueError, OSError) as error:
            self._send(400, {"errors": [str(error)]})
            return
        self._send(200, _machine())

    def _role(self, data: dict) -> None:
        """Where the brain is. A body is paired with a brain elsewhere by its
        code; who starts the brain itself is the shell's business (the app,
        or the service on a server), and it reads this answer."""
        role = data.get("role")
        if role not in ROLES:
            self._send(400, {"errors": ["невідома роль"]})
            return
        settings = Settings()
        if role == "body":
            url, code = _text(data, "url").rstrip("/"), _text(data, "code")
            if not url.startswith(("http://", "https://")):
                self._send(400, {"errors": ["адреса має починатися з http:// або https://"]})
                return
            if len(code) != 8:
                self._send(400, {"errors": ["код має бути 8 символів"]})
                return
            try:
                answer = pair(url, code, "machine", socket.gethostname())
            except OSError as error:
                self._send(400, {"errors": [f"мозок не підключив: {error}"]})
                return
            token = Path(settings.machine_token_file)
            token.parent.mkdir(parents=True, exist_ok=True)
            token.write_text(answer["token"], encoding="utf-8")
            token.chmod(0o600)
            settings.set("url", url)
        else:
            settings.set("url", "")
        settings.set("role", role)
        # A first run starts with every part this machine can have: one
        # asked to turn each on by itself had to guess that it must.
        folder = Path(settings.parts_dir)
        if role != "brain" and want_setup(settings) and not machine.running(folder):
            for part in machine.parts(folder):
                if not part["why_not"]:
                    machine.turn(folder, part["name"], True)
        self._send(200, _machine())

    def _key(self, data: dict) -> None:
        """Check a key with its provider and keep it only if it works."""
        name, value = data.get("name"), data.get("value")
        if name not in keys.NAMES or not isinstance(value, str) or not value.strip():
            self._send(400, {"errors": ["невідомий або порожній ключ"]})
            return
        ok, said = machine.check_key(name, value)
        if ok:
            try:
                keys.store(name, value)
            except OSError as error:
                ok, said = False, f"не зберіг: {error}"
        self._send(200, {"ok": ok, "said": said, "keys": keys.view()})

    def _adopt(self, data: dict) -> None:
        name = data.get("name")
        if not isinstance(name, str) or name not in keys.NAMES:
            self._send(400, {"errors": ["невідомий ключ"]})
            return
        try:
            saved = keys.adopt(name)
        except OSError as error:
            self._send(400, {"errors": [str(error)]})
            return
        if not saved:
            self._send(400, {"errors": ["немає такого ключа в оточенні"]})
            return
        self._send(200, {"saved": name})


def _machine() -> dict:
    """What the installer shows: the parts here, and which keys are held."""
    settings = Settings()
    return {"platform": sys.platform,
            "role": settings.role or "",
            "done": settings.setup_done is True,
            "parts": machine.parts(Path(settings.parts_dir)),
            "keys": keys.view(),
            "url": settings.url or "",
            "api_port": settings.api_port}


def _install_info() -> tuple[str, str]:
    """The installer and its wheels, when install.sh --setup left them;
    empty in an app, which has every part in it and installs nothing."""
    path = Path.home() / ".heare" / "install.json"
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        return str(info["installer"]), str(info["wheels"])
    except (OSError, ValueError, KeyError, TypeError):
        return "", ""


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
    # The page opens by itself only for the first run; after that it is the
    # settings, there when asked for.
    if not os.environ.get("HEARE_NO_BROWSER") and want_setup(Settings()):
        _open(address)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
