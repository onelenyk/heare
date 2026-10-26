"""The setup mode on the board: one AP, one DNS answer, one small form.

``portal.py`` is the pure core and ``net.py`` owns the radio; this module is
the place where those two meet. It starts a WPA2 access point, answers every
DNS name with the AP's own address, and serves the provisioning page on port
80 from a single ``select`` loop. No threads, no ``asyncio``: one socket is
ready at a time and the board has only about a hundred kilobytes to spare.

Two phases are deliberately separate. A save only proves the Wi-Fi password:
the station is brought up while the AP and the form stay alive, and the new
config reaches ``/config.json`` only after DHCP gave the board an address.
Pairing is a callback (Task 17), and the AP is stopped only when that callback
succeeds or the setup session times out. A wrong pair code must not cost the
person the Wi-Fi password they just typed.

The AP password is generated once per boot from an alphabet with no easily
confused characters and is shown on the OLED and on serial. It is the AP's
password, not the Wi-Fi password: the station's password and the pair code
never reach a log or a page.
"""

import json
import os
import random

try:
    import select
except ImportError:
    select = None

try:
    import socket
except ImportError:
    socket = None

try:
    import machine
except ImportError:
    machine = None

from heare_panel import clock
from heare_panel import config as schema
from heare_panel import net
from heare_panel import portal
from heare_panel import store as storage

SETUP_SSID_PREFIX = net.AP_PREFIX
AP_PASSWORD_LENGTH = 10
AP_PASSWORD_MIN = net.AP_PASSWORD_MIN
AP_PASSWORD_MAX = net.AP_PASSWORD_MAX
# No 0/O, 1/l/I and no easily missed look-alikes: the password is read from a
# tiny screen and typed on a phone, often once.
AP_PASSWORD_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
MAX_ASSOCIATION_FAILURES = 3
SCAN_LIMIT = portal.MAX_NETWORKS
POLL_MS = 200
READ_TIMEOUT_S = 2
SETUP_TIMEOUT_MS = 10 * 60 * 1000
HTTP_PORT = 80
DNS_PORT = 53
AP_IP = portal.AP_IP
HEAD_LIMIT = portal.MAX_HEAD + portal.MAX_BODY + 4

_REASONS = {
    200: "OK",
    302: "Found",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
}

_DEFAULT_OUT = print


class SetupError(Exception):
    """Setup mode cannot start or cannot continue."""


def generate_password(source=None, length=AP_PASSWORD_LENGTH):
    """A password of 8-12 unambiguous characters, from ``source`` if given.

    Rejection sampling keeps every character in the alphabet even though a
    byte has 256 values: values above the largest multiple of the alphabet
    length are discarded rather than biased toward the first letters.
    """
    if not AP_PASSWORD_MIN <= length <= AP_PASSWORD_MAX:
        raise ValueError("AP password must be 8-12 characters")
    rng = source or random
    alphabet = AP_PASSWORD_ALPHABET
    limit = 256 - (256 % len(alphabet))
    out = []
    while len(out) < length:
        value = rng.getrandbits(8)
        if value < limit:
            out.append(alphabet[value % len(alphabet)])
    return "".join(out)


def setup_needed(failures, brain_error=False):
    """Whether boot should open the setup AP.

    Only failed associations to the saved SSID open it. A brain that cannot
    be reached is a normal state with a live Wi-Fi link (the panel shows
    OFFLINE), never a reason to expose an AP.
    """
    if brain_error and not failures:
        return False
    try:
        count = int(failures)
    except (TypeError, ValueError):
        return False
    return count >= MAX_ASSOCIATION_FAILURES


def show_credentials(display, ssid, password, out=_DEFAULT_OUT):
    """Print the AP credentials and draw them when a panel is present.

    Serial is the reliable channel and is always written first: the display
    may be absent, stale, or wired to a panel of unknown geometry. The
    password shown here is the AP's WPA2 password, which the person needs to
    join; the station password is never passed to this function.
    """
    out("ap ssid={} password={}".format(ssid, password))
    try:
        panel = getattr(display, "_panel", None) if display is not None else None
        if panel is None or not getattr(display, "available", False):
            return False
        width = getattr(display, "width", 128) or 128
        height = getattr(display, "height", 64) or 64
        columns = max(width // 8, 8)
        panel.fill(0)
        if height >= 64:
            panel.text("WIFI SETUP", 0, 0)
            panel.text(ssid[:columns], 0, 16)
            panel.text(password[:columns], 0, 32)
        else:
            panel.text("SETUP", 0, 0)
            _draw_wrapped(panel, ssid, 1, columns, 2)
            _draw_wrapped(panel, password, 3, columns, 2)
        panel.show()
        return True
    except Exception:
        # A panel that paints or writes badly is not a reason to keep the
        # person out of setup; serial already has both values.
        return False


def _draw_wrapped(panel, text, first_row, columns, max_rows):
    row = first_row
    for start in range(0, len(text), columns):
        if row >= first_row + max_rows:
            return
        panel.text(text[start : start + columns], 0, row * 8)
        row += 1


class Setup:
    """The whole setup transaction: AP, DNS, HTTP, save, hand-off.

    Every collaborator is injectable so the flow can be tested on a laptop:
    ``store``, ``ap``, ``sta``, ``display``, the clock, and the pairing
    callback. On the board the defaults are the real adapters.
    """

    def __init__(
        self,
        store,
        ap=None,
        sta=None,
        display=None,
        password=None,
        tag=None,
        pair=None,
        out=_DEFAULT_OUT,
        connect_timeout_ms=net.DEFAULT_TIMEOUT_MS,
        selector=None,
        sockets=None,
    ):
        self.store = store
        if ap is None:
            self.password = password or generate_password()
            self.ap = net.Ap(password=self.password, tag=tag)
        else:
            self.ap = ap
            if password is None:
                password = getattr(ap, "password", "") or generate_password()
            self.password = password
            try:
                ap.password = password
            except (AttributeError, TypeError):
                pass
        if sta is None:
            self.sta = net.Sta()
        else:
            self.sta = sta
        self.display = display
        self.pair = pair
        self._out = out
        self.connect_timeout_ms = connect_timeout_ms
        self._selector = selector
        self._sockets = sockets
        self.started = False
        self.stop_requested = False
        self.reason = None
        self.sta_ip = None
        self.saved_config = None
        self._udp = None
        self._tcp = None

    @property
    def ssid(self):
        return getattr(self.ap, "ssid", SETUP_SSID_PREFIX)

    def start(self):
        """Start the AP and announce its credentials; no sockets yet."""
        if not getattr(self.ap, "started", False):
            self.ap.start()
        self.started = True
        show_credentials(self.display, self.ssid, self.password, self._out)
        self._out("portal ip={} http={} dns={}".format(AP_IP, HTTP_PORT, DNS_PORT))
        return self.ssid

    def stop(self):
        self.stop_requested = True
        if self.reason is None:
            self.reason = "stopped"

    def close(self):
        """Close the two sockets, then the AP; safe to call more than once."""
        for name in ("_udp", "_tcp"):
            sock = getattr(self, name)
            setattr(self, name, None)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        if getattr(self.ap, "started", False):
            self.ap.stop()
        self.started = False

    def serve(self, timeout_ms=SETUP_TIMEOUT_MS):
        """Run the single-threaded setup loop until paired, stopped or timed out.

        ``timeout_ms`` is measured from the start of the call; the loop polls
        both sockets and checks that the AP is still up on every pass. The AP
        is closed on every exit path, including an exception, so a crashed
        loop cannot leave an open radio behind.
        """
        if not self.started:
            self.start()
        self._open_sockets()
        started = clock.now_ms()
        try:
            while not self.stop_requested:
                if (
                    timeout_ms is not None
                    and clock.elapsed(started, clock.now_ms()) >= timeout_ms
                ):
                    self.reason = "timeout"
                    break
                if not getattr(self.ap, "started", False):
                    self.reason = "ap down"
                    break
                ready = self._wait(self._readers(), POLL_MS)
                for sock in ready:
                    if sock is self._udp:
                        self._serve_dns()
                    elif sock is self._tcp:
                        self._serve_http()
        finally:
            self.close()
        return self.reason

    def handle_dns(self, data):
        """One DNS datagram in, one reply out; malformed input is ignored."""
        return portal.dns_reply(data)

    def handle_http(self, raw):
        """One complete HTTP request in, one response (bytes) out."""
        try:
            method, path, _headers, body = _parse_request(raw)
        except portal.PortalError as error:
            return self._text_response(400, str(error))

        if method == "GET" and path == "/scan":
            return self._scan_response()
        if method == "GET" and path == "/":
            return self._form_response()
        if method == "POST" and path == "/save":
            return self._save(body)

        probe = portal.probe_response(path)
        if probe is not None:
            status, headers, response_body = probe
            return self._response(status, headers, response_body)
        return self._text_response(404, "not found")

    def _readers(self):
        readers = []
        if self._udp is not None:
            readers.append(self._udp)
        if self._tcp is not None:
            readers.append(self._tcp)
        return readers

    def _open_sockets(self):
        if self._udp is not None and self._tcp is not None:
            return
        factory = self._sockets or socket
        if factory is None:
            raise SetupError("socket is unavailable")
        udp = factory.socket(factory.AF_INET, factory.SOCK_DGRAM)
        tcp = factory.socket(factory.AF_INET, factory.SOCK_STREAM)
        try:
            udp.setsockopt(factory.SOL_SOCKET, factory.SO_REUSEADDR, 1)
            udp.bind(("0.0.0.0", DNS_PORT))
            tcp.setsockopt(factory.SOL_SOCKET, factory.SO_REUSEADDR, 1)
            tcp.bind(("0.0.0.0", HTTP_PORT))
            tcp.listen(1)
        except OSError:
            try:
                udp.close()
            except OSError:
                pass
            try:
                tcp.close()
            except OSError:
                pass
            raise
        self._udp = udp
        self._tcp = tcp

    def _wait(self, readers, timeout_ms):
        selector = self._selector
        if selector is None:
            selector = select.select if select is not None else None
        if selector is None:
            raise SetupError("select is unavailable")
        if not readers:
            return []
        return selector(readers, [], [], timeout_ms / 1000.0)[0]

    def _serve_dns(self):
        try:
            data, address = self._udp.recvfrom(512)
        except OSError:
            return
        reply = self.handle_dns(data)
        if reply is None:
            return
        try:
            self._udp.sendto(reply, address)
        except OSError:
            pass

    def _serve_http(self):
        try:
            connection, _address = self._tcp.accept()
        except OSError:
            return
        try:
            try:
                connection.settimeout(READ_TIMEOUT_S)
            except OSError:
                pass
            raw = self._read_request(connection)
            response = self.handle_http(raw)
            connection.sendall(response)
        except OSError:
            pass
        finally:
            try:
                connection.close()
            except OSError:
                pass

    def _read_request(self, connection):
        raw = b""
        while len(raw) < HEAD_LIMIT:
            chunk = connection.recv(512)
            if not chunk:
                break
            raw += chunk
            if _complete(raw):
                break
        return raw

    def _form_response(self, values=None, error=None, status=200):
        if values is None:
            values = self._stored_values()
        page = portal.render_page(self._networks(), values, error)
        return self._response(
            status, {"Content-Type": "text/html; charset=utf-8"}, page
        )

    def _scan_response(self):
        entries = []
        for network in self._networks():
            entries.append(
                {"ssid": network["ssid"], "rssi": network["rssi"]}
            )
        return self._response(
            200,
            {"Content-Type": "application/json; charset=utf-8"},
            json.dumps(entries),
        )

    def _save(self, body):
        try:
            form = portal.parse_form(body)
        except portal.PortalError as error:
            return self._form_response({}, str(error), status=400)

        values = form_values(form)
        base = self._stored_config()
        merged_form = dict(form)
        merged_form["ssid"] = values["ssid"]
        try:
            candidate = schema.merge(base or {}, merged_form)
        except schema.ConfigError as error:
            return self._form_response(values, str(error))

        wifi = candidate["wifi"]
        try:
            ip = self.sta.connect(
                wifi["ssid"], wifi["password"], timeout_ms=self.connect_timeout_ms
            )
        except net.WifiError as error:
            return self._form_response(
                values, "could not join Wi-Fi: " + _clip(str(error), 120)
            )
        except (OSError, ValueError):
            return self._form_response(values, "could not join Wi-Fi")
        except Exception:
            return self._form_response(values, "could not join Wi-Fi")

        try:
            saved = self.store.save_config(candidate)
        except Exception:
            return self._form_response(values, "could not save config")

        self.sta_ip = ip
        self.saved_config = saved
        self._out("sta up ip={}".format(ip))
        self._out("config saved ssid={}".format(wifi["ssid"]))

        if self.pair is None:
            return self._success_response(saved, ip, "Pairing is next.")
        try:
            paired = bool(self.pair(saved))
        except Exception:
            paired = False
        if not paired:
            return self._success_response(
                saved, ip, "Wi-Fi saved. Pairing failed; this page stays available."
            )
        self.stop_requested = True
        self.reason = "paired"
        return self._success_response(saved, ip, "Paired.")

    def _success_response(self, config, ip, note):
        ssid = config["wifi"]["ssid"]
        page = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>heare panel</title>"
            "<style>body{margin:0;padding:18px;background:#111;color:#eee;"
            "font:16px/1.45 system-ui,sans-serif}h1{font-size:20px;margin:0 0 14px}"
            ".hint{color:#9aa}</style></head><body>"
            "<h1>Wi-Fi saved</h1><p>Network <strong>"
            + _escape(ssid)
            + "</strong> at "
            + _escape(str(ip))
            + ".</p><p class=\"hint\">"
            + _escape(note)
            + "</p></body></html>"
        )
        return self._response(
            200, {"Content-Type": "text/html; charset=utf-8"}, page
        )

    def _text_response(self, status, text):
        return self._response(
            status,
            {"Content-Type": "text/plain; charset=utf-8"},
            _clip(text, 240),
        )

    def _response(self, status, headers=None, body=b""):
        if isinstance(body, str):
            body = body.encode("utf-8")
        else:
            body = bytes(body)
        fields = {}
        for name, value in (headers or {}).items():
            fields[name] = value
        fields.setdefault("Content-Length", str(len(body)))
        fields.setdefault("Cache-Control", "no-store")
        fields.setdefault("Connection", "close")
        lines = ["HTTP/1.0 {} {}".format(status, _REASONS.get(status, "OK"))]
        for name, value in fields.items():
            lines.append("{}: {}".format(name, value))
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body

    def _networks(self):
        scan = getattr(self.sta, "scan", None)
        if scan is None:
            return []
        try:
            found = scan()
        except Exception:
            return []
        networks = []
        for item in found or []:
            if not isinstance(item, dict):
                continue
            ssid = item.get("ssid")
            if not isinstance(ssid, str):
                continue
            networks.append({"ssid": ssid, "rssi": item.get("rssi", 0)})
            if len(networks) >= SCAN_LIMIT:
                break
        return networks

    def _stored_config(self):
        try:
            return self.store.load_config()
        except Exception:
            return None

    def _stored_values(self):
        stored = self._stored_config()
        if not stored:
            return {}
        try:
            view = schema.public_view(stored)
        except schema.ConfigError:
            return {}
        return {
            "ssid": view["wifi"]["ssid"],
            "brain_host": view["brain"]["host"],
            "brain_port": str(view["brain"]["port"]),
            "name": view["device"]["name"],
        }


def form_values(form):
    """The non-secret fields to echo into the page after a submit.

    The Wi-Fi password and pair code are intentionally absent: the page
    renders both empty on every response, including a validation error.
    ``ssid_manual`` wins over the scan's ``ssid`` because the form's free-text
    box is how a hidden or unlisted network is entered.
    """
    if not isinstance(form, dict):
        form = {}
    return {
        "ssid": form.get("ssid_manual") or form.get("ssid") or "",
        "brain_host": form.get("brain_host") or "",
        "brain_port": form.get("brain_port") or "",
        "name": form.get("name") or "",
    }


def status(store=None, out=_DEFAULT_OUT):
    """Print presence only: whether a config, a token and a pid exist.

    No SSID, address, token or pid value is printed. This is the one command
    that is safe to run while somebody is looking over a shoulder.
    """
    store = store or _store()
    try:
        config = store.load_config()
    except Exception:
        config = None
    try:
        token = store.get_part_token()
    except Exception:
        token = None
    try:
        pid = store.get_pid()
    except Exception:
        pid = None
    line = "config={} part_token={} pid={}".format(
        "present" if config else "absent",
        "present" if token else "absent",
        "present" if pid else "absent",
    )
    out(line)
    return {
        "config": bool(config),
        "part_token": bool(token),
        "pid": bool(pid),
    }


def forget(store=None, reset=None, out=_DEFAULT_OUT):
    """Erase the part token, the pid and ``/config.json``, then reboot.

    The machine token was never stored, so there is nothing else to erase.
    ``reset`` is injectable for the host test; on the board the default is
    ``machine.reset`` and does not return.
    """
    store = store or _store()
    try:
        store.clear_part_token()
    except OSError:
        pass
    _erase_key(store, storage.KEY_PID)
    _erase_key(store, storage.KEY_CONFIG_VER)
    _remove_config(store)
    out("forget part_token=cleared pid=cleared config=cleared")
    (reset or _reset)()


def provision(store=None, out=_DEFAULT_OUT, **options):
    """The serial/repl entry point: start setup mode and serve until done."""
    setup = Setup(store or _store(), out=out, **options)
    setup.start()
    return setup.serve(SETUP_TIMEOUT_MS)


def command(name, store=None, reset=None, out=_DEFAULT_OUT):
    """Dispatch one serial command: ``provision``, ``status`` or ``forget``."""
    name = (name or "").strip().lower()
    if name == "status":
        return status(store, out)
    if name == "forget":
        return forget(store, reset, out)
    if name == "provision":
        return provision(store, out)
    raise ValueError("unknown command: {}".format(name))


def _complete(raw):
    """Whether ``raw`` holds a head and all of the body it declares."""
    head, separator, rest = raw.partition(b"\r\n\r\n")
    if not separator:
        return False
    length = 0
    for line in head.split(b"\r\n")[1:]:
        name, colon, value = line.partition(b":")
        if not colon:
            continue
        if name.strip().lower() == b"content-length":
            try:
                length = int(value.strip())
            except ValueError:
                return True
    return len(rest) >= length


def _parse_request(raw):
    """``portal.parse_request`` with a MicroPython-safe head decode.

    MicroPython 1.29 knows only UTF-8, while the pure parser decodes the
    request head as latin-1. HTTP header bytes are ASCII by definition, so
    the fallback parses the same grammar with UTF-8 and is used only when
    the board raises ``LookupError`` for the missing codec.
    """
    try:
        return portal.parse_request(raw)
    except LookupError:
        return _ascii_request(raw)


def _ascii_request(raw):
    if not isinstance(raw, (bytes, bytearray)):
        raise portal.PortalError("request must be bytes")
    raw = bytes(raw)
    head, separator, rest = raw.partition(b"\r\n\r\n")
    if not separator:
        if len(raw) > portal.MAX_HEAD:
            raise portal.PortalError(
                "request head is larger than {} bytes".format(portal.MAX_HEAD)
            )
        raise portal.PortalError("request has no header terminator")
    if len(head) > portal.MAX_HEAD:
        raise portal.PortalError(
            "request head is larger than {} bytes".format(portal.MAX_HEAD)
        )

    lines = head.decode("utf-8", "replace").split("\r\n")
    parts = lines[0].split()
    if len(parts) != 3:
        raise portal.PortalError("malformed request line")
    method = parts[0].upper()
    if not method or not _letters(method):
        raise portal.PortalError("malformed request method")
    path = parts[1].split("?", 1)[0]
    if not path:
        path = "/"

    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise portal.PortalError("malformed header line")
        name, value = line.split(":", 1)
        name = name.strip().lower()
        if not name:
            raise portal.PortalError("malformed header line")
        value = value.strip()
        if name in headers:
            if name == "content-length":
                raise portal.PortalError("duplicate content-length header")
            headers[name] = headers[name] + ", " + value
        else:
            headers[name] = value

    declared = headers.get("content-length")
    if declared is None:
        if method == "POST":
            raise portal.PortalError("POST requires Content-Length")
        return method, path, headers, b""
    if not declared or not _digits(declared):
        raise portal.PortalError("bad content-length")
    length = int(declared)
    if length > portal.MAX_BODY:
        raise portal.PortalError(
            "request body is larger than {} bytes".format(portal.MAX_BODY)
        )
    if len(rest) < length:
        raise portal.PortalError("request body is shorter than Content-Length")
    return method, path, headers, rest[:length]


def _digits(value):
    for char in value:
        if char not in "0123456789":
            return False
    return True


def _letters(value):
    for char in value:
        if char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            return False
    return True


def _erase_key(store, key):
    erase = getattr(getattr(store, "nvs", None), "erase_key", None)
    if erase is None:
        return
    try:
        erase(key)
    except OSError:
        return
    commit = getattr(store.nvs, "commit", None)
    if commit is not None:
        commit()


def _remove_config(store):
    path = getattr(store, "path", None)
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _store():
    return storage.Store()


def _reset():
    if machine is None:
        raise SetupError("machine is unavailable")
    machine.reset()


def _escape(value):
    text = value if isinstance(value, str) else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _clip(value, limit):
    text = value if isinstance(value, str) else str(value)
    return text[:limit]
