"""Print the system prompt the running brain gives its model now.

    make prompt

Reads the master key from ~/.heare/machine/api_token and asks the brain on
this Mac; the standard library only, so it runs without the app's packages.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home() / ".heare"
PORT = os.environ.get("HEARE_PORT", "9790")


def main() -> int:
    try:
        token = (HOME / "machine" / "api_token").read_text(encoding="utf-8").strip()
    except OSError:
        print(f"немає ключа в {HOME / 'machine' / 'api_token'} — мозок запущений?",
              file=sys.stderr)
        return 1
    request = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/prompt",
                                     headers={"X-Heare-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:
            text = answer.read().decode("utf-8")
    except (urllib.error.URLError, OSError) as exc:
        print(f"мозок не відповів: {exc}", file=sys.stderr)
        return 1
    print(text)
    print(f"\n— {len(text)} символів, ~{len(text) // 4} токенів", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
