"""A text body: type to the brain, read what it says back.

    uv run python -m heare.chat

A body, not the brain: it holds nothing and remembers nothing. It sends
what you type to the brain's door and prints what comes back. Run the brain
first — ``uv run python -m heare.brain``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx

from heare import persona
from heare.config import Settings


def _token(settings: Settings) -> str:
    return Path(settings.api_token_file).read_text(encoding="utf-8").strip()


def _show(event: dict, name: str) -> None:
    """Print what the brain said; note the things it could not do."""
    if event.get("kind") == "said" and event.get("text"):
        print(f"{name}: {event['text']}", flush=True)
    elif event.get("kind") in ("failed", "cannot"):
        print(f"  ({event.get('why') or event['kind']})", flush=True)


async def _print_events(client: httpx.AsyncClient, base: str, name: str) -> None:
    async with client.stream("GET", f"{base}/api/events") as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                _show(json.loads(line[6:]), name)


def _line() -> str | None:
    """One line from the terminal, read without readline's thread trouble.

    ``input`` goes through readline, which is not safe from a thread while
    another task writes the reply to the same terminal: it has read one
    multi-byte character in pieces and raised UnicodeDecodeError. ``sys.stdin``
    is asked directly instead, and a mangled byte becomes «�», not a crash.
    """
    print("ти: ", end="", flush=True)
    line = sys.stdin.readline()
    return line.rstrip("\n") if line else None


async def _typed(client: httpx.AsyncClient, base: str) -> None:
    """Send each line you type to the brain, until the input ends."""
    while True:
        line = await asyncio.to_thread(_line)
        if line is None:
            return
        if line.strip():
            await client.post(f"{base}/api/message", json={"text": line.strip()})


async def run() -> int:
    settings = Settings()
    base = f"http://127.0.0.1:{settings.api_port}"
    name = persona.name() or "heare"
    try:
        sys.stdin.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    try:
        secret = _token(settings)
    except OSError:
        print(f"немає ключа ({settings.api_token_file}) — мозок запущений?",
              file=sys.stderr)
        return 1
    headers = {"X-Heare-Token": secret}
    async with httpx.AsyncClient(headers=headers, timeout=None) as client:
        try:
            await client.get(f"{base}/api/state")
        except httpx.HTTPError as exc:
            print(f"мозок не відповідає на {base}: {exc}", file=sys.stderr)
            return 1
        printer = asyncio.create_task(_print_events(client, base, name))
        try:
            await _typed(client, base)
        finally:
            printer.cancel()
            await asyncio.gather(printer, return_exceptions=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.chat")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
