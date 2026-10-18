"""The agent's way to ask the person something mid-work.

    python -m heare.reach.bridge --socket ~/.heare/hands-123.sock

An MCP server over stdio: the agent starts it, lists its tools, and the
one tool — ``ask`` — goes down the socket to the hands, which carries the
question through the door to the person and waits for their words.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

TOOLS = [{
    "name": "ask",
    "description": (
        "Ask the person a question and wait for their answer. Use it when the "
        "task cannot go on without a word from them — a path, a choice, a "
        "detail only they know."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
}]


def _reply(body: dict) -> None:
    sys.stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def _ask(socket: str, question: str) -> str:
    reader, writer = await asyncio.open_unix_connection(socket)
    try:
        writer.write((json.dumps({"tool": "ask", "question": question},
                                 ensure_ascii=False) + "\n").encode())
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
            msg = json.loads(line)
        except ValueError:
            continue
        method, rid = msg.get("method"), msg.get("id")
        if method == "initialize":
            _reply({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "heare", "version": "0"},
            }})
        elif method == "tools/list":
            _reply({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            arguments = params.get("arguments") or {}
            if params.get("name") != "ask":
                _reply({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": "no such tool"}],
                    "isError": True,
                }})
                continue
            try:
                answer = await _ask(socket, str(arguments.get("question") or ""))
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
