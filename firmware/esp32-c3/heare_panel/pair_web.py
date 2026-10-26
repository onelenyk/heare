"""The panel's own page on the home Wi-Fi: pair it with a brain.

Bluetooth setup stops at Wi-Fi. After that the panel serves one form on
port 80 as ``heare-panel.local`` and shows that name and a four-digit code
on its screen. Anyone on the network can open the page, so a pairing needs
the code from the screen too; five wrong codes close the page until the next
boot shows a new one.

One request at a time from a non-blocking ``accept`` that the main loop
polls, so the watchdog keeps being fed between requests. ``respond`` is the
whole page logic and takes bytes in and bytes out, which is what the host
tests drive; ``PairPage.poll`` only moves bytes between it and a socket.

The pair code and the screen code are never echoed into the page or a log.
"""

try:
    import socket
except ImportError:
    socket = None

from heare_panel import portal

PORT = 80
READ_TIMEOUT_S = 2
REQUEST_LIMIT = portal.MAX_HEAD + portal.MAX_BODY
MAX_CODE_FAILURES = 5
DEFAULT_NAME = "panel"

_MESSAGES = {
    "config": "Перевірте адресу мозку (IPv4), порт і назву (a–z, 0–9, дефіс).",
    "pair": "Мозок не прийняв код пари. Візьміть новий: heare-brain code.",
    "wifi": "Панель втратила Wi-Fi. Перезавантажте її.",
}


class PairPage:
    """The form, its screen code and what a successful pairing returned."""

    def __init__(self, handler, code, defaults=None, out=print):
        self.handler = handler
        self.code = code
        self.defaults = dict(defaults or {})
        self.out = out
        self.failures = 0
        self.paired = False
        self._sock = None

    def start(self, port=PORT):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        sock.listen(2)
        sock.setblocking(False)
        self._sock = sock

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def poll(self):
        """Serve at most one waiting request; True once the panel is paired."""
        if self._sock is None:
            return self.paired
        try:
            connection, address = self._sock.accept()
        except OSError:
            return self.paired
        try:
            try:
                connection.settimeout(READ_TIMEOUT_S)
            except OSError:
                pass
            raw = _read(connection)
            connection.sendall(self.respond(raw, _client(address)))
        except OSError:
            pass
        finally:
            try:
                connection.close()
            except OSError:
                pass
        return self.paired

    def respond(self, raw, client=""):
        try:
            method, path, _headers, body = portal.parse_request(raw)
        except portal.PortalError:
            return _response(400, _page(self._values(client), "Запит не розібрано."))
        if method == "POST" and path == "/pair":
            return self._pair(body, client)
        if method != "GET":
            return _response(405, _page(self._values(client), None))
        return _response(200, _page(self._values(client), None))

    def _pair(self, body, client):
        try:
            form = portal.parse_form(body)
        except portal.PortalError:
            return _response(400, _page(self._values(client), "Форму не розібрано."))
        values = self._values(client, form)

        if self.failures >= MAX_CODE_FAILURES:
            return _response(
                429, _page(values, "Забагато спроб. Перезавантажте панель: буде новий код.")
            )
        if (form.get("screen_code") or "").strip() != self.code:
            self.failures += 1
            self.out("pair page wrong screen code")
            return _response(403, _page(values, "Код з екрана панелі не той."))

        try:
            result = self.handler(
                {
                    "cmd": "save",
                    "brain_host": values["brain_host"],
                    "brain_port": values["brain_port"],
                    "name": values["name"],
                    "pair_code": (form.get("pair_code") or "").strip().upper(),
                }
            )
        except Exception as error:  # noqa: BLE001 - the page must keep serving
            self.out("pair page handler failed " + type(error).__name__)
            result = {"ok": False, "error": "handler"}

        if isinstance(result, dict) and result.get("ok"):
            self.paired = True
            self.out("pair page paired")
            return _response(200, _done_page(values["name"]))
        error = result.get("error", "") if isinstance(result, dict) else ""
        return _response(
            200, _page(values, _MESSAGES.get(error, "Не вдалося: " + str(error) + "."))
        )

    def _values(self, client, form=None):
        """What the form shows: typed values first, then stored, then guesses.

        The person opening this page is usually on the brain's own machine,
        so its address is the first guess for the brain's.
        """
        form = form or {}
        defaults = self.defaults
        return {
            "brain_host": _pick(form, defaults, "brain_host") or client,
            "brain_port": _pick(form, defaults, "brain_port") or str(portal.DEFAULT_PORT),
            "name": _pick(form, defaults, "name") or DEFAULT_NAME,
        }


def _pick(form, defaults, key):
    value = form.get(key)
    if value is None or str(value).strip() == "":
        value = defaults.get(key)
    return "" if value is None else str(value).strip()


def _client(address):
    try:
        return str(address[0])
    except (TypeError, IndexError):
        return ""


def _read(connection):
    raw = b""
    while len(raw) < REQUEST_LIMIT:
        chunk = connection.recv(512)
        if not chunk:
            break
        raw += chunk
        if _complete(raw):
            break
    return raw


def _complete(raw):
    head, separator, rest = raw.partition(b"\r\n\r\n")
    if not separator:
        return False
    for line in head.split(b"\r\n")[1:]:
        name, colon, value = line.partition(b":")
        if colon and name.strip().lower() == b"content-length":
            try:
                return len(rest) >= int(value.strip())
            except ValueError:
                return True
    return True


_REASONS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed", 429: "Too Many Requests"}


def _response(status, html):
    body = html.encode("utf-8")
    head = (
        "HTTP/1.0 {} {}\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Content-Length: {}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).format(status, _REASONS.get(status, "OK"), len(body))
    return head.encode("utf-8") + body


def _escape(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_STYLE = (
    "body{font:16px system-ui,sans-serif;background:#14111f;color:#ece8f7;"
    "max-width:26rem;margin:0 auto;padding:1.5rem 1rem}"
    "h1{font-size:1.4rem;margin:0 0 .3rem}p{color:#b4adc9;margin:.3rem 0 1rem}"
    "label{display:block;margin:.9rem 0 .3rem;font-size:.85rem;color:#b4adc9}"
    "input{width:100%;box-sizing:border-box;padding:.7rem;border-radius:.5rem;"
    "border:1px solid #3a3352;background:#1d1830;color:inherit;font-size:1rem}"
    "button{margin-top:1.3rem;width:100%;padding:.8rem;border:0;border-radius:.5rem;"
    "background:#8b6cf0;color:#fff;font-size:1rem}"
    ".err{background:#3b1b26;color:#ffb3c1;padding:.7rem;border-radius:.5rem}"
    ".ok{background:#183326;color:#b6f0cf;padding:.7rem;border-radius:.5rem}"
)


def _shell(inner):
    return (
        '<!doctype html><html lang="uk"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>heare · панель</title><style>" + _STYLE + "</style></head><body>"
        + inner + "</body></html>"
    )


def _page(values, error):
    alert = '<p class="err">' + _escape(error) + "</p>" if error else ""
    return _shell(
        "<h1>Панель → мозок</h1>"
        "<p>Панель уже в Wi-Fi. Залишилось сказати їй, де мозок.</p>"
        + alert
        + '<form method="post" action="/pair">'
        '<label>Код з екрана панелі</label>'
        '<input name="screen_code" inputmode="numeric" maxlength="4" required autofocus>'
        '<label>Адреса мозку</label>'
        '<input name="brain_host" value="' + _escape(values["brain_host"]) + '" required>'
        '<label>Порт</label>'
        '<input name="brain_port" inputmode="numeric" value="' + _escape(values["brain_port"]) + '">'
        '<label>Назва панелі (a–z, 0–9, дефіс)</label>'
        '<input name="name" value="' + _escape(values["name"]) + '">'
        '<label>Код пари (heare-brain code)</label>'
        '<input name="pair_code" maxlength="8" autocapitalize="characters" required>'
        "<button>Спарити</button></form>"
    )


def _done_page(name):
    return _shell(
        "<h1>Готово</h1>"
        '<p class="ok">Панель «' + _escape(name) + "» спарена з мозком "
        "і перезавантажується в роботу.</p>"
        "<p>Цю сторінку можна закрити.</p>"
    )
