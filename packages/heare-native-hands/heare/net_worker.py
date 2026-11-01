"""One HTTP request, run alone inside the sandbox.

Started by ``heare.net`` as a bare script: the standard library only, no
environment, no keys, a JSON request on stdin and a JSON answer on stdout. A
sandbox profile cannot name address ranges, so the home network is refused
here, at name resolution: redirects and rebinding pass through the same door.
"""

import ipaddress
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

KEEP_BYTES = 200_000
TIMEOUT_S = 15
_resolve = socket.getaddrinfo


def public_only(host, *args, **kwargs):
    found = _resolve(host, *args, **kwargs)
    kept = [row for row in found
            if ipaddress.ip_address(row[4][0].split("%")[0]).is_global]
    if not kept:
        raise OSError(f"{host} is not a public address")
    return kept


def fetch(asked: dict) -> dict:
    socket.getaddrinfo = public_only
    body = asked.get("body")
    # Sites behind a firewall turn away the name «Python-urllib» on sight.
    headers = {"User-Agent": "heare", **(asked.get("headers") or {})}
    request = urllib.request.Request(
        asked["url"], method=asked["method"], headers=headers,
        data=body.encode("utf-8") if body else None)
    context = ssl.create_default_context(cafile=asked.get("cafile"))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
    started = time.monotonic()
    try:
        response = opener.open(request, timeout=TIMEOUT_S)
    except urllib.error.HTTPError as refused:
        response = refused  # a 404 is an answer, not a failure
    raw = response.read(KEEP_BYTES)
    return {"status": response.status, "url": response.url,
            "ms": int((time.monotonic() - started) * 1000),
            "type": response.headers.get("Content-Type", ""),
            "text": raw.decode("utf-8", "replace")}


if __name__ == "__main__":
    try:
        answer = fetch(json.load(sys.stdin))
    except Exception as failed:
        answer = {"error": f"{type(failed).__name__}: {failed}"}
    print(json.dumps(answer))
