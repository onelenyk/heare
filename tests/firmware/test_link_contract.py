"""The link client against the real brain, over a real socket.

The host tests script the brain's answers; this one asks it. Pairing must
produce only a part key, a part knock must be accepted under its own pid, the
part key must not open the person's state, and a stream with ``kinds=`` must
carry only those kinds.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer
from heare.api import pair
from heare.api.server import Screen, build
from heare.config import Settings
from heare_panel import config as schema
from heare_panel import state
from heare_panel.link import Link
from heare_panel.store import Store

from tests.firmware.fakes import FakeNVS
from tests.heare.fakes import open_room

PID = "oled-a1b2@esp32c3-a1b2"


async def test_pair_knock_and_events_against_the_real_brain(tmp_path):
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
    part_headers = {}
    try:
        code = pair.make(settings.pair_codes_file)
        config = schema.validate({
            "version": 1,
            "wifi": {"ssid": "home", "password": ""},
            "brain": {"host": "127.0.0.1", "port": client.server.port},
            "device": {"name": "esp32c3-a1b2"},
        })
        nvs = FakeNVS()
        store = Store(nvs)
        seen: list[str] = []
        link = Link(config, store, on_state=seen.append)

        token = await asyncio.to_thread(link.pair, code)
        assert token
        assert store.get_part_token() == token
        assert store.get_pid() == PID
        assert link.channel == "oled-a1b2"
        assert link.host == "esp32c3-a1b2"
        assert PID.encode() in nvs.blobs.values()
        assert b"machine@" not in b"".join(nvs.blobs.values())

        part_headers = {"X-Heare-Token": token}
        assert (await client.get("/api/state", headers=part_headers)).status == 403

        answer = await asyncio.to_thread(link.knock, token, part=True)
        assert answer["approved"] is True
        assert answer["pid"] == PID
        assert store.get_part_token() == token

        room.core.state.set_phase("thinking")
        link.silence_s = 2.0
        streaming = asyncio.create_task(asyncio.to_thread(link.events, "phase"))
        for _ in range(200):
            if link.state == state.THINK:
                break
            await asyncio.sleep(0.02)
        assert link.state == state.THINK
        link.close()
        await asyncio.wait_for(streaming, 6)
        assert seen[0] == state.IDLE
        assert state.THINK in seen

        response = await client.get("/api/events?kinds=phase", headers=part_headers)
        first = json.loads((await asyncio.wait_for(
            response.content.readline(), 2))[6:])
        assert first["kind"] == "phase"

        room.core.state.emit("said", channel="screen", text="secret words",
                             failed=None)
        room.core.state.set_phase("speaking")
        while True:
            line = await asyncio.wait_for(response.content.readline(), 2)
            if not line.startswith(b"data: "):
                continue
            second = json.loads(line[6:])
            break
        assert second["kind"] == "phase"
        assert second["phase"] == "speaking"
        assert "text" not in second
        response.close()
    finally:
        await client.close()
        await room.close()


def test_the_panel_s_skills_pass_the_brain_s_check_as_they_are():
    from heare.api import skills
    from heare_panel import device

    assert skills.checked(device.skills()) == device.skills()


def test_the_panel_s_pins_are_the_trust_it_was_let_in_with():
    # Live, the panel was let in with these world-changing skills
    # (26 October). A change here makes it wait on the page again: decide
    # that, then change the number.
    from heare.api import skills
    from heare_panel import device

    assert skills.fingerprint(device.skills()) == "dc73103e8c33aa39"
