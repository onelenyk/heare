"""The bridge: the agent's `show` over MCP, down to the hands."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path


async def _bridge(socket: Path):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "heare.reach.bridge", "--socket", str(socket),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    assert proc.stdin and proc.stdout

    async def call(message: dict) -> dict:
        proc.stdin.write((json.dumps(message) + "\n").encode())
        await proc.stdin.drain()
        # A loaded runner is slow to start a child; the answer is what matters.
        return json.loads(await asyncio.wait_for(proc.stdout.readline(), 60))

    return proc, call


async def test_the_agent_sees_one_tool_and_its_call_reaches_the_hands():
    socket = Path(tempfile.mkdtemp(prefix="hb", dir="/tmp")) / "s.sock"
    heard: list[dict] = []

    async def handle(reader, writer) -> None:
        heard.append(json.loads((await reader.readline()) or b"{}"))
        writer.write((json.dumps({"answer": "Shown."}) + "\n").encode())
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(socket))
    proc, call = await _bridge(socket)
    try:
        init = await call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05",
                                      "capabilities": {},
                                      "clientInfo": {"name": "t", "version": "0"}}})
        assert init["result"]["capabilities"]["tools"] == {}

        listed = await call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in listed["result"]["tools"]]
        assert names == ["show"]

        called = await call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "show",
                                        "arguments": {"html": "<b>x</b>",
                                                      "title": "T"}}})
        assert called["result"]["content"] == [{"type": "text", "text": "Shown."}]
        assert heard == [{"tool": "show", "html": "<b>x</b>", "title": "T"}]

        unknown = await call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                              "params": {"name": "ask", "arguments": {}}})
        assert unknown["result"]["isError"] is True
    finally:
        server.close()
        await server.wait_closed()
        proc.kill()
        await proc.wait()


async def test_a_hands_that_is_gone_is_an_answer_not_a_crash():
    socket = Path(tempfile.mkdtemp(prefix="hb", dir="/tmp")) / "gone.sock"
    proc, call = await _bridge(socket)
    try:
        await call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05",
                               "capabilities": {},
                               "clientInfo": {"name": "t", "version": "0"}}})
        called = await call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "show",
                                        "arguments": {"html": "<b>x</b>"}}})
        text = called["result"]["content"][0]["text"]
        assert "not reachable" in text and called["result"]["isError"] is False
    finally:
        proc.kill()
        await proc.wait()
