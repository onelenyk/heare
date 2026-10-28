"""The installer page, which stays as this machine's settings.

The keeper serves it on ``127.0.0.1`` always; it opens by itself only
until the first run is done. Every answer is applied as it is given — a
part is a manifest written or removed, a key is checked and kept — so the
page never runs an installer and never runs a command the person typed.
Everything a machine can run is already installed by ``install.sh``.
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

from heare import logs
from heare import keys, machine
from heare import words
from heare.config import Settings
from heare.pairing import pair
from heare.core.i18n import _

ROLES = machine.ROLES
INSTALLER = Path(__file__).with_name("installer.html")
DEFAULT_PORT = 9791


def want_setup(settings: Settings) -> bool:
    """The page opens by itself until the first run is finished."""
    return settings.setup_done is not True


def _text(data: dict, name: str) -> str:
    value = data.get(name)
    return value.strip() if isinstance(value, str) else ""


class _FormServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address) -> None:
        super().__init__(address, _Handler)


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
            self._send(404, {"errors": [_("no such page")]})
            return
        self._send(200, words.page(INSTALLER.read_text(encoding="utf-8")),
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
            self._send(403, {"errors": [_("another page does not control this form")]})
            return
        kind = (self.headers.get("Content-Type") or "").lower()
        if not kind.startswith("application/json"):
            self._send(415, {"errors": [_("JSON is needed")]})
            return
        path = self.path.split("?", 1)[0]
        if path == "/close":
            self._send(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path not in ("/keys", "/part", "/key", "/role", "/done"):
            self._send(404, {"errors": [_("no such action")]})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._send(400, {"errors": [_("could not read the request")]})
            return
        if not isinstance(data, dict):
            self._send(400, {"errors": [_("could not read the request")]})
            return

        if path == "/keys":
            self._adopt(data)
        elif path == "/part":
            self._turn(data)
        elif path == "/key":
            self._key(data)
        elif path == "/role":
            self._role(data)
        else:
            Settings().set("setup_done", True)
            self._send(200, _machine())

    def _turn(self, data: dict) -> None:
        name, on = data.get("name"), data.get("on")
        if name not in machine.PARTS or not isinstance(on, bool):
            self._send(400, {"errors": [_("unknown part")]})
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
            self._send(400, {"errors": [_("unknown role")]})
            return
        settings = Settings()
        if role == "body":
            url, code = _text(data, "url").rstrip("/"), _text(data, "code")
            if not url.startswith(("http://", "https://")):
                self._send(400, {"errors": [_("the address must start with http:// or https://")]})
                return
            if len(code) != 8:
                self._send(400, {"errors": [_("the code must be 8 characters")]})
                return
            try:
                answer = pair(url, code, "machine", socket.gethostname())
            except OSError as error:
                self._send(400, {"errors": [_("the brain did not connect: {error}").format(error=error)]})
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
            self._send(400, {"errors": [_("an unknown or empty key")]})
            return
        ok, said = machine.check_key(name, value)
        if ok:
            try:
                keys.store(name, value)
            except OSError as error:
                ok, said = False, _("not saved: {error}").format(error=error)
        if ok and name == "DEEPSEEK_API_KEY":
            # The brain's key this page asks for is DeepSeek's; giving it is
            # choosing the provider, so the brain must not keep one it has
            # no key for and fall back on every turn.
            Settings().set("llm_provider", "deepseek")
        self._send(200, {"ok": ok, "said": said, "keys": keys.view()})

    def _adopt(self, data: dict) -> None:
        name = data.get("name")
        if not isinstance(name, str) or name not in keys.NAMES:
            self._send(400, {"errors": [_("an unknown key")]})
            return
        try:
            saved = keys.adopt(name)
        except OSError as error:
            self._send(400, {"errors": [str(error)]})
            return
        if not saved:
            self._send(400, {"errors": [_("no such key in the environment")]})
            return
        self._send(200, {"saved": name})


def _machine() -> dict:
    """What the installer shows: the parts here, and which keys are held."""
    settings = Settings()
    return {"platform": sys.platform,
            "role": settings.role or "",
            "done": settings.setup_done is True,
            "roles": machine.roles(),
            "parts": machine.parts(Path(settings.parts_dir)),
            "keys": keys.view(),
            "url": settings.url or "",
            "api_port": settings.api_port}


def _open(address: str) -> None:
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen(
            [opener, address],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def serve(port: int | None = None) -> None:
    # Exactly the configured port: the app and the person look for the page
    # there, and a page that moved to the next free port quietly was a page
    # nobody could find.
    port = port if port is not None else int(Settings().setup_port or DEFAULT_PORT)
    try:
        httpd = _FormServer(("127.0.0.1", port))
    except OSError as error:
        raise OSError(_("port {port} is taken — the settings page is unavailable: {error}").format(port=port, error=error)) from error

    address = f"http://127.0.0.1:{httpd.server_address[1]}/"
    logs.say(_("form: {address}").format(address=address))
    # The page opens by itself only for the first run; after that it is the
    # settings, there when asked for.
    if not os.environ.get("HEARE_NO_BROWSER") and want_setup(Settings()):
        _open(address)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
