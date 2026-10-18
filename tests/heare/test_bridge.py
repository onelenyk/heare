"""The bridge: the agent's `ask` over MCP, down to the hands."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path


async def test_the_agent_sees_one_tool_and_its_call_reaches_the_hands():
    socket = Path(tempfile.mkdtemp(prefix="hb", dir="/tmp")) / "s.sock"
    heard: list[dict] = []

    async def handle(reader, writer):
        heard.append(json.loads((await reader.readline()) or b"{}"))
        writer.write((json.dumps({"answer": "той, що в /tmp"}) + "\n").encode())
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(socket))
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "heare.reach.bridge", "--socket", str(socket),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)

    async def call(message: dict) -> dict:
        assert proc.stdin and proc.stdout
        proc.stdin.write((json.dumps(message) + "\n").encode())
        await proc.stdin.drain()
        return json.loads(await asyncio.wait_for(proc.stdout.readline(), 5))

    try:
        init = await call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05",
                                      "capabilities": {},
                                      "clientInfo": {"name": "t", "version": "0"}}})
        assert init["result"]["capabilities"]["tools"] == {}

        listed = await call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert [tool["name"] for tool in listed["result"]["tools"]] == ["ask"]

        called = await call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "ask",
                                        "arguments": {"question": "який файл?"}}})
        assert called["result"]["content"][0]["text"] == "той, що в /tmp"
        assert heard == [{"tool": "ask", "question": "який файл?"}]
    finally:
        proc.terminate()
        await proc.wait()
        server.close()
        await server.wait_closed()
