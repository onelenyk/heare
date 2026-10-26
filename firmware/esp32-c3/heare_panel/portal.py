"""Read one small HTTP request and its form, refusing anything oversized.

The panel's pairing page (``pair_web``) hands the bytes it read from a socket
to ``parse_request`` and the body to ``parse_form``. Nothing here opens a
socket, so the whole path is tested on a laptop. Every limit is a constant,
so a truncated or hostile request is refused before it can grow memory.

(The module once also served a Wi-Fi access point's captive portal; that
setup path was replaced by Bluetooth and is gone.)
"""

MAX_HEAD = 4096
MAX_BODY = 1024
MAX_NETWORKS = 12
DEFAULT_PORT = 9790


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

