"""The brain, spoken to over a raw socket, in HTTP/1.0.

The device has no chunked decoder, so this proves the wire, not the client:
an HTTP/1.0 ``StreamResponse`` arrives as a head and a raw stream, with no
``Transfer-Encoding``. If aiohttp ever changes that, this test fails before
a board does.
"""

from __future__ import annotations

import asyncio
import socket

from aiohttp.test_utils import TestClient, TestServer
from heare.api.server import Screen, build
from heare.config import Settings
from heare_panel.http import build_request, read_head

from tests.firmware.fakes import FakeSocket
from tests.heare.fakes import open_room


def _raw_head(host: str, port: int, request: bytes, timeout: float = 5.0) -> bytes:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request)
        raw = bytearray()
        while not raw.endswith(b"\r\n\r\n"):
            chunk = sock.recv(1)
            if not chunk:
                break
            raw.extend(chunk)
        return bytes(raw)


async def test_a_raw_http_1_0_event_stream_has_no_transfer_encoding(tmp_path):
    room = await open_room()
    settings = Settings(
        path=tmp_path / "config.toml", api_token_file=tmp_path / "token",
        screens_dir=tmp_path / "screens", parts_file=tmp_path / "parts.json",
        parts_dir=tmp_path / "parts",
        part_tokens_file=tmp_path / "part_tokens.json",
        pair_codes_file=tmp_path / "pair_codes.json",
        memory_dir=tmp_path / "memory", heard_dir=tmp_path / "heard",
        backups_dir=tmp_path / "home" / "backups",
    )
    room.core.add_output(Screen(room.core))
    client = TestClient(TestServer(build(room.core, settings)))
    await client.start_server()
    master = {"X-Heare-Token": (tmp_path / "token").read_text()}
    try:
        knock = asyncio.create_task(client.post("/api/bodies", headers=master, json={
            "channel": "panel", "forms": ["output"], "key": "panel@test",
            "host": "test", "hears": "all"}))
        await asyncio.sleep(0.05)
        await client.post("/api/bodies/panel@test/approve", headers=master)
        token = (await (await knock).json())["token"]

        server = client.server
        request = build_request(
            "GET", "/api/events?kinds=phase", "127.0.0.1", token,
            port=server.port)
        raw = await asyncio.to_thread(
            _raw_head, server.host, server.port, request)

        sock = FakeSocket(raw)
        status, headers = read_head(sock)
        assert status == 200
        assert "transfer-encoding" not in headers
        assert headers["content-type"].startswith("text/event-stream")
    finally:
        await client.close()
        await room.close()
