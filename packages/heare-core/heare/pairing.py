"""Pairing with a brain that is somewhere else: a code in, a key out.

The core's own client for the one door that needs no key. Parts do not use
it — they knock with the machine's key — but the keeper's setup form and
the installer do, and both speak to a brain they do not share a disk with.
"""

from __future__ import annotations

import json
import urllib.request


def pair(base: str, code: str, kind: str, name: str) -> dict:
    """Ask a brain for a key. A machine is answered with a token; a window
    gets its key in a cookie and the answer holds only its name."""
    body = json.dumps({"code": code, "kind": kind, "name": name}).encode()
    request = urllib.request.Request(
        f"{base.rstrip('/')}/api/pair", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as answer:
        return json.loads(answer.read().decode())
