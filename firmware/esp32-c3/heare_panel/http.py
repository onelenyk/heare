"""The brain's door, spoken in HTTP/1.0 over a plain socket.

The panel has no TLS and no chunked decoder: HTTP/1.0 closes the stream at
the end, so a response body is either ``Content-Length`` bytes or everything
until EOF. That is why this client is small enough to trust.

The socket is injected, never imported: the same bytes run against a fake on
a laptop and against ``socket.socket`` on the board.
"""

import json

MAX_HEAD = 4096
MAX_BODY = 8192
_READ = 256


class HttpError(Exception):
    """A response the client cannot use; ``status`` is set when it was read."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def build_request(method: str, path: str, host: str, token: str,
                  body: bytes | str | dict | None = None,
                  port: int = 80) -> bytes:
    """One HTTP/1.0 request: the exact bytes the brain expects.

    A body is always declared, even when it is empty, because a server is
    allowed to wait for what the head announced. The port joins the ``Host``
    header only when it is not the default, which is what a virtual host
    sees from a browser.
    """
    payload = _payload(body)
    authority = host if port == 80 or host.endswith(f":{port}") else f"{host}:{port}"
    return (
        f"{method.upper()} {path} HTTP/1.0\r\n"
        f"Host: {authority}\r\n"
        f"X-Heare-Token: {token}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode() + payload


def read_head(sock) -> tuple[int, dict[str, str]]:
    """The status and headers, read one byte at a time.

    One byte at a time is deliberate: it cannot swallow the first byte of the
    body or of an SSE frame, and a real socket has the head in memory long
    before this loop runs. A head is small; correctness is worth the calls.
    """
    raw = bytearray()
    while True:
        if len(raw) > MAX_HEAD:
            raise HttpError(f"response head is larger than {MAX_HEAD} bytes")
        chunk = sock.recv(1)
        if not chunk:
            raise HttpError("connection closed before the response head was complete")
        raw.extend(chunk)
        if raw.endswith(b"\r\n\r\n"):
            break

    head = bytes(raw[: -len(b"\r\n\r\n")]).decode("latin-1")
    lines = head.split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise HttpError(f"malformed status line: {lines[0]!r}")
    status = int(parts[1])

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise HttpError(f"malformed header line: {line!r}")
        name, value = line.split(":", 1)
        name = name.strip().lower()
        if not name:
            raise HttpError(f"malformed header line: {line!r}")
        value = value.strip()
        headers[name] = f"{headers[name]}, {value}" if name in headers else value
    return status, headers


def post_json(sock, method: str, path: str, host: str, token: str,
              body: bytes | str | dict | None = None,
              port: int = 80) -> tuple[int, dict]:
    """Send one request and parse whatever JSON the answer carries.

    A non-2xx answer is returned, not raised: the status is part of what the
    caller must decide on (401 says the token is gone, 403 says the part may
    not do this). Only a malformed or oversized answer is an exception.
    """
    sock.sendall(build_request(method, path, host, token, body, port))
    status, headers = read_head(sock)

    declared = headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError as error:
            raise HttpError(f"bad content-length: {declared!r}", status) from error
        if length < 0 or length > MAX_BODY:
            raise HttpError(f"response body too large: {length} bytes", status)
        raw = _read_exact(sock, length, status)
    else:
        raw = _read_to_eof(sock, status)

    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return status, {}
    try:
        parsed = json.loads(text)
    except ValueError as error:
        raise HttpError("response body was not JSON", status) from error
    if not isinstance(parsed, dict):
        raise HttpError("response body was not a JSON object", status)
    return status, parsed


def open_stream(sock, method: str, path: str, host: str, token: str,
                port: int = 80) -> tuple[int, dict[str, str]]:
    """Send a stream request and read only its head.

    ``chunked`` is a tripwire, not a feature: the brain answers HTTP/1.0 with
    a raw stream that ends at EOF. Seeing chunked means the contract broke
    (or the request became HTTP/1.1), and a silent partial frame is worse
    than a clear failure.
    """
    sock.sendall(build_request(method, path, host, token, None, port))
    status, headers = read_head(sock)
    encoding = headers.get("transfer-encoding", "").lower()
    if "chunked" in encoding:
        raise HttpError("chunked is not expected for HTTP/1.0", status)
    if status != 200:
        raise HttpError(f"stream refused with status {status}", status)
    return status, headers


def _payload(body: bytes | str | dict | None) -> bytes:
    if body is None:
        return b""
    if isinstance(body, dict):
        return json.dumps(body).encode()
    if isinstance(body, str):
        return body.encode()
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    raise TypeError(f"a body is bytes, a string or a dict, not {type(body).__name__}")


def _read_exact(sock, length: int, status: int) -> bytes:
    body = bytearray()
    while len(body) < length:
        chunk = sock.recv(min(_READ, length - len(body)))
        if not chunk:
            raise HttpError(
                "connection closed before the response body was complete", status)
        body.extend(chunk)
    return bytes(body)


def _read_to_eof(sock, status: int) -> bytes:
    body = bytearray()
    while True:
        chunk = sock.recv(_READ)
        if not chunk:
            return bytes(body)
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise HttpError(f"response body is larger than {MAX_BODY} bytes", status)
