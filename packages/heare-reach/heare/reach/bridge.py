"""The agent's way back to the person: show them something.

    python -m heare.reach.bridge --socket ~/.heare/hands-123.sock

An MCP server over stdio. The agent starts it (our own config names it),
lists its tools, and calls ``show``: a card or a link in the chat. The call
goes down the hands' socket, and the hands carry it through the door — the
agent never holds the brain's key.

A question is not here: opencode asks one natively (``question``), and two
ways to ask the same thing would be one too many.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

TOOLS = [{
    "name": "show",
    "description": (
        "Show the person something in the chat: a small html card or a link. "
        "Use it when a picture is worth more than words — a table, a diff, a "
        "chart, a page you made. The card is drawn beside the conversation; "
        "do not repeat its contents in your answer."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "html": {
                "type": "string",
                "description": (
                    "The card's body, as html. No scripts, no styles, no "
                    "remote pictures: the page keeps only the markup."
                ),
            },
            "url": {
                "type": "string",
                "description": "A page to link instead of a card (http/https).",
            },
            "title": {"type": "string", "description": "The card's name."},
            "say": {
                "type": "string",
                "description": "One short sentence about what it shows.",
            },
        },
        "required": [],
    },
}]


def _reply(body: dict) -> None:
    sys.stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def _call(socket: str, request: dict) -> str:
    reader, writer = await asyncio.open_unix_connection(socket)
    try:
        writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
        await writer.drain()
        line = await reader.readline()
        return str((json.loads(line or b"{}") or {}).get("answer") or "")
    finally:
        writer.close()
        await writer.wait_closed()


async def serve(socket: str) -> None:
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        try:
            message = json.loads(line)
        except ValueError:
            continue
        method, rid = message.get("method"), message.get("id")
        if method == "initialize":
            _reply({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "heare", "version": "0"},
            }})
        elif method == "tools/list":
            _reply({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = message.get("params") or {}
            arguments = params.get("arguments") or {}
            if params.get("name") != "show":
                _reply({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": "no such tool"}],
                    "isError": True,
                }})
                continue
            try:
                answer = await _call(socket, {"tool": "show", **arguments})
            except OSError as exc:
                answer = f"the person is not reachable: {exc}"
            _reply({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": answer}], "isError": False,
            }})
        elif rid is not None:
            _reply({"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32601, "message": "not offered"}})


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.reach.bridge")
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.socket))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
