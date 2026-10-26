"""The captive portal's pure core: what to say before the panel is on a network.

Nothing here opens a socket. The board's portal loop (Task 16) reads bytes
from a socket, hands them to ``parse_request``/``parse_form``, and writes what
``render_page``, ``dns_reply`` or ``probe_response`` returns. Keeping the
bytes and the page pure is what lets the whole provisioning path be tested on
a laptop.

Three rules shape the code:

- every limit is a constant, so a truncated or hostile packet is refused
  before it can grow memory;
- every value that came from a user or a Wi-Fi scan is escaped on the way
  into the page, and the two secrets (Wi-Fi password, pair code) are never
  written into the page at all;
- a malformed DNS packet returns ``None`` instead of raising, because the
  portal loop must keep listening for the next query.

The DNS hijack answers any A question with the AP's address and refuses AAAA
with an empty NOERROR answer, so phones stop waiting for IPv6 and show the
captive sheet. The three OS probes are redirected to the portal page during
setup: a real "Success" body would tell the OS there is no portal to open.
"""

import struct

AP_IP = "192.168.4.1"
AP_URL = "http://" + AP_IP + "/"
MAX_HEAD = 4096
MAX_BODY = 1024
MAX_NETWORKS = 12
MAX_ERROR = 240
DEFAULT_PORT = 9790

APPLE_PROBE = "/hotspot-detect.html"
ANDROID_PROBE = "/generate_204"
WINDOWS_PROBE = "/connecttest.txt"
PROBES = (APPLE_PROBE, ANDROID_PROBE, WINDOWS_PROBE)

DNS_TYPE_A = 1
DNS_TYPE_AAAA = 28
DNS_TTL = 60
DNS_CLASS_IN = 1

_CSS = (
    "*{box-sizing:border-box}"
    "body{margin:0;padding:18px;background:#111;color:#eee;"
    "font:16px/1.45 system-ui,sans-serif}"
    "h1{font-size:20px;margin:0 0 14px}"
    "label{display:block;margin:10px 0 4px;font-size:13px;color:#aaa}"
    "input,select{width:100%;padding:9px;border:1px solid #444;"
    "border-radius:6px;background:#1b1b1b;color:#eee;font:inherit}"
    ".row{display:flex;gap:10px}.row>*{flex:1}"
    "button{width:100%;margin-top:16px;padding:12px;border:0;"
    "border-radius:6px;background:#3b82f6;color:#fff;font:inherit;"
    "font-weight:600}"
    ".err{margin:0 0 12px;padding:10px;border-radius:6px;"
    "background:#3a1414;color:#ffb4b4}"
    ".hint{margin:2px 0 0;font-size:12px;color:#888}"
    "fieldset{margin:0;padding:0;border:0}"
)


class PortalError(Exception):
    """A request or form the portal cannot use; never holds a secret value."""


def parse_request(raw):
    """Split one HTTP request into ``(method, path, headers, body)``.

    The head is capped at 4 KB and the declared body at 1 KB before either is
    copied, so a bad ``Content-Length`` cannot make the board allocate. Header
    names are lowercased; a POST without ``Content-Length`` and a repeated
    ``Content-Length`` are refused rather than guessed at.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise PortalError("request must be bytes")
    raw = bytes(raw)
    head, separator, rest = raw.partition(b"\r\n\r\n")
    if not separator:
        if len(raw) > MAX_HEAD:
            raise PortalError(f"request head is larger than {MAX_HEAD} bytes")
        raise PortalError("request has no header terminator")
    if len(head) > MAX_HEAD:
        raise PortalError(f"request head is larger than {MAX_HEAD} bytes")

    lines = head.decode("utf-8").split("\r\n")
    parts = lines[0].split()
    if len(parts) != 3:
        raise PortalError("malformed request line")
    method = parts[0].upper()
    if not method or not _letters(method):
        raise PortalError("malformed request method")
    target = parts[1]
    path = target.split("?", 1)[0]
    if not path:
        path = "/"

    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise PortalError("malformed header line")
        name, value = line.split(":", 1)
        name = name.strip().lower()
        if not name:
            raise PortalError("malformed header line")
        value = value.strip()
        if name in headers:
            if name == "content-length":
                raise PortalError("duplicate content-length header")
            headers[name] = headers[name] + ", " + value
        else:
            headers[name] = value

    declared = headers.get("content-length")
    if declared is None:
        if method == "POST":
            raise PortalError("POST requires Content-Length")
        return method, path, headers, b""

    if not declared or not _digits(declared):
        raise PortalError("bad content-length")
    length = int(declared)
    if length > MAX_BODY:
        raise PortalError(f"request body is larger than {MAX_BODY} bytes")
    if len(rest) < length:
        raise PortalError("request body is shorter than Content-Length")
    return method, path, headers, rest[:length]


def parse_form(body):
    """Decode an ``application/x-www-form-urlencoded`` body into a dict.

    ``+`` is a space and ``%XX`` is one byte, all before UTF-8 decoding, so a
    name such as ``Caf%C3%A9`` arrives as text and not as mojibake. A key may
    appear once: the form has one value per field, and a repeated key is a
    sign of a hand-made request, not of a browser.
    """
    if not isinstance(body, (bytes, bytearray)):
        raise PortalError("form body must be bytes")
    body = bytes(body)
    if len(body) > MAX_BODY:
        raise PortalError(f"form body is larger than {MAX_BODY} bytes")

    form = {}
    if not body:
        return form
    for pair in body.split(b"&"):
        if not pair:
            continue
        name, separator, value = pair.partition(b"=")
        if not separator:
            value = b""
        key = _unquote(name)
        if key in form:
            raise PortalError(f"duplicate form field: {key}")
        form[key] = _unquote(value)
    return form


def render_page(networks, values, error=None):
    """The one provisioning page: scan results, fields, and one small style.

    ``networks`` is a list of ``{"ssid", "rssi"}`` dicts. ``values`` is what
    the user typed last time (or what a scan selected); only non-secret fields
    are echoed. The password and pair code are rendered empty on every
    response, including an error, so a secret never appears in page source.

    The scan select is ``ssid``; the free-text box is ``ssid_manual`` so a
    browser never submits two values under one name (which ``parse_form``
    rejects). Task 16 prefers a non-empty ``ssid_manual``.
    """
    values = values if isinstance(values, dict) else {}
    current = values.get("ssid")
    if not isinstance(current, str):
        current = ""

    scanned = []
    options = []
    for network in networks or []:
        if not isinstance(network, dict):
            continue
        ssid = network.get("ssid")
        if not isinstance(ssid, str) or not ssid or len(ssid) > 32:
            continue
        if ssid in scanned:
            continue
        scanned.append(ssid)
        chosen = " selected" if ssid == current else ""
        options.append(
            f'<option value="{_escape(ssid)}"{chosen}>{_escape(ssid)}</option>'
        )
        if len(options) >= MAX_NETWORKS:
            break
    if not options:
        options.append('<option value="" disabled selected>no networks found</option>')

    manual = current if current and current not in scanned else ""
    message = ""
    if error:
        message = (
            f'<p class="err" role="alert">'
            f"{_escape(_clip(_without_secrets(error, values), MAX_ERROR))}</p>"
        )

    port = values.get("brain_port")
    if port is None or port == "":
        port = DEFAULT_PORT

    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>heare panel</title><style>" + _CSS + "</style></head><body>"
        "<h1>heare panel</h1>"
        + message
        + '<form method="post" action="/save"><fieldset>'
        "<label>Wi-Fi network</label>"
        '<select name="ssid">' + "".join(options) + "</select>"
        '<p class="hint">Choose a network above, or type its name below.</p>'
        '<label for="ssid_manual">Network name</label>'
        '<input id="ssid_manual" name="ssid_manual" maxlength="32" '
        'autocomplete="off" value="' + _escape(_clip(manual, 32)) + '">'
        '<label for="password">Wi-Fi password</label>'
        '<input type="password" id="password" name="password" minlength="8" '
        'maxlength="63" autocomplete="new-password">'
        '<div class="row">'
        '<div><label for="brain_host">Brain IP</label>'
        '<input id="brain_host" name="brain_host" inputmode="decimal" '
        'maxlength="15" value="'
        + _escape(_clip(values.get("brain_host"), 15))
        + '"></div>'
        '<div><label for="brain_port">Port</label>'
        '<input type="number" id="brain_port" name="brain_port" min="1" '
        'max="65535" value="' + _escape(_clip(port, 5)) + '"></div></div>'
        '<label for="name">Device name</label>'
        '<input id="name" name="name" maxlength="24" autocomplete="off" value="'
        + _escape(_clip(values.get("name"), 24))
        + '">'
        '<label for="pair_code">Pair code</label>'
        '<input id="pair_code" name="pair_code" maxlength="8" '
        'autocomplete="off" autocapitalize="characters">'
        '<button type="submit">Connect</button>'
        "</fieldset></form></body></html>"
    )
    return page


def dns_reply(query):
    """Answer one DNS query, or ``None`` when it cannot be read.

    The reply is built from the query itself: ID echoed, the first question
    copied, and recursion-desired copied back. ``A`` gets the AP address;
    ``AAAA`` and every other type get a NOERROR answer with no records
    (NODATA), which is what makes a phone fall back to IPv4 quickly.
    """
    parsed = _question(query)
    if parsed is None:
        return None
    ident, flags, question, qtype = parsed
    response = 0x8000 | (flags & 0x0100) | 0x0080
    if qtype != DNS_TYPE_A:
        return struct.pack("!HHHHHH", ident, response, 1, 0, 0, 0) + question
    answer = (
        b"\xc0\x0c"
        + struct.pack("!HHIH", DNS_TYPE_A, DNS_CLASS_IN, DNS_TTL, 4)
        + b"\xc0\xa8\x04\x01"
    )
    return struct.pack("!HHHHHH", ident, response, 1, 1, 0, 0) + question + answer


def probe_response(path):
    """The answer to an OS captive probe, or ``None`` for the portal itself.

    All three probes (Apple, Android, Windows) are redirected, not answered
    with their expected body: during setup the OS must decide there is a
    portal and open the sheet. ``/`` and ``/save`` belong to the portal and
    return ``None`` so the caller serves the page or parses the POST. Any
    other path is redirected too, which keeps a browser on the AP.
    """
    if not isinstance(path, str) or not path:
        return None
    path = path.split("?", 1)[0]
    if path == "/" or path == "/save":
        return None
    location = AP_URL if path in PROBES else "/"
    headers = {
        "Location": location,
        "Content-Type": "text/plain",
        "Content-Length": "0",
        "Cache-Control": "no-store",
    }
    return 302, headers, b""


def _question(query):
    """``(id, flags, question_bytes, qtype)`` for the first question, or None.

    A query's QNAME is a sequence of length-prefixed labels ending in zero.
    Compression pointers are legal only in answers, so a pointer inside a
    query is treated as malformed; every length and offset is checked before
    it is used.
    """
    if not isinstance(query, (bytes, bytearray)):
        return None
    raw = bytes(query)
    if len(raw) < 12:
        return None
    ident, flags, qdcount = struct.unpack("!HHH", raw[:6])
    if qdcount < 1:
        return None

    position = 12
    name_length = 0
    while True:
        if position >= len(raw):
            return None
        size = raw[position]
        position += 1
        if size == 0:
            break
        if size & 0xC0:
            return None
        if size > 63 or position + size > len(raw):
            return None
        name_length += size + 1
        if name_length > 255:
            return None
        position += size

    if position + 4 > len(raw):
        return None
    qtype = struct.unpack("!H", raw[position : position + 2])[0]
    end = position + 4
    return ident, flags, raw[12:end], qtype


def _clip(value, limit):
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


def _without_secrets(message, values):
    """An error text with any echoed password or pair code replaced.

    A caller should pass a validation message, not a value. This is the last
    line of defence: even a careless message cannot put a secret in the page.
    """
    text = message if isinstance(message, str) else str(message)
    for key in ("password", "pair_code"):
        secret = values.get(key)
        if isinstance(secret, str) and secret:
            text = text.replace(secret, "***")
    return text


def _escape(value):
    """Text safe for an attribute or a page, escaping ``& < > " '``."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _digits(value):
    for ch in value:
        if ch not in "0123456789":
            return False
    return True


def _letters(value):
    for ch in value:
        if ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            return False
    return True


def _unquote(part):
    """Decode one form component: ``+`` is space, ``%XX`` is a byte."""
    out = bytearray()
    index = 0
    while index < len(part):
        ch = part[index]
        if ch == 43:
            out.append(32)
            index += 1
        elif ch == 37:
            if index + 2 >= len(part):
                raise PortalError("invalid percent escape")
            high = _hex(part[index + 1])
            low = _hex(part[index + 2])
            if high < 0 or low < 0:
                raise PortalError("invalid percent escape")
            out.append(high * 16 + low)
            index += 3
        else:
            out.append(ch)
            index += 1
    try:
        return out.decode("utf-8")
    except UnicodeError:
        raise PortalError("form field is not valid UTF-8")


def _hex(ch):
    if 48 <= ch <= 57:
        return ch - 48
    if 65 <= ch <= 70:
        return ch - 55
    if 97 <= ch <= 102:
        return ch - 87
    return -1
