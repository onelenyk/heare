"""The link client, driven against scripted sockets.

Every test here is about a promise the brain makes in ``server.py`` and a
promise the panel must not break: the knock body is exact, the machine key is
used once, a part knock names its own pid, a 401 is final, and a stream is
never opened without a knock.
"""

from __future__ import annotations

import json

import pytest
from heare_panel import link as link_module
from heare_panel import state
from heare_panel.link import (
    ABOUT,
    EVENT_KINDS,
    VERSION,
    Link,
    LinkError,
    NeedsProvisioning,
)
from heare_panel.store import Store

from tests.firmware.fakes import FakeClock, FakeNVS, FakeSocket

PID = "oled-a1b2@esp32c3-a1b2"
MACHINE_PID = "machine@esp32c3-a1b2"


def _config(host: str = "192.168.1.10", port: int = 9790) -> dict:
    return {
        "version": 1,
        "wifi": {"ssid": "home", "password": "secret123"},
        "brain": {"host": host, "port": port},
        "device": {"name": "esp32c3-a1b2"},
    }


def _store() -> Store:
    return Store(FakeNVS())


def _json(payload: dict, status: str = "200 OK") -> bytes:
    body = json.dumps(payload).encode()
    return (
        f"HTTP/1.0 {status}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode() + body


def _text(status: str, body: str = "") -> bytes:
    raw = body.encode()
    return (
        f"HTTP/1.0 {status}\r\nContent-Type: text/plain\r\n"
        f"Content-Length: {len(raw)}\r\n\r\n"
    ).encode() + raw


def _stream(*events: dict, status: str = "200 OK") -> bytes:
    head = (
        f"HTTP/1.0 {status}\r\nContent-Type: text/event-stream\r\n"
        "Cache-Control: no-cache\r\n\r\n"
    ).encode()
    return head + b"".join(
        ("data: " + json.dumps(event) + "\n\n").encode() for event in events
    )


class ServerSocket(FakeSocket):
    """A socket that answers its own request, so no test orders sockets."""

    def __init__(self, server: FakeServer) -> None:
        super().__init__()
        self.server = server

    def sendall(self, data: bytes) -> None:
        super().sendall(data)
        request = self.server.parse(bytes(data))
        self.server.requests.append(request)
        self.feed(self.server.next(request))


class FakeServer:
    """A list of canned answers, one per request, plus what was asked."""

    def __init__(self, answers: list[bytes]) -> None:
        self.answers = list(answers)
        self.requests: list[dict] = []

    def socket(self) -> ServerSocket:
        return ServerSocket(self)

    def next(self, request: dict) -> bytes:
        assert self.answers, f"no answer for {request['method']} {request['path']}"
        return self.answers.pop(0)

    @staticmethod
    def parse(raw: bytes) -> dict:
        head, _, body = raw.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        method, path, _version = lines[0].split(b" ", 2)
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(b":")
            headers[name.strip().lower().decode()] = value.strip().decode()
        return {
            "method": method.decode(),
            "path": path.decode(),
            "headers": headers,
            "body": body,
        }

    def paths(self) -> list[str]:
        return [request["path"] for request in self.requests]

    def bodies(self, path: str = "/api/bodies") -> list[dict]:
        return [
            json.loads(request["body"])
            for request in self.requests
            if request["path"] == path
        ]


def _link(server: FakeServer, store: Store | None = None, **kwargs) -> Link:
    return Link(_config(), store or _store(), socket_factory=server.socket, **kwargs)


def test_pair_exchanges_the_machine_key_for_a_part_key():
    server = FakeServer([
        _json({"pid": MACHINE_PID, "token": "machine-secret"}),
        _json({"approved": True, "pid": PID, "token": "part-secret"}),
    ])
    store = _store()
    link = _link(server, store)

    assert link.pair(" abcd2345 ") == "part-secret"

    pairing = server.requests[0]
    assert pairing["method"] == "POST"
    assert pairing["path"] == "/api/pair"
    assert pairing["headers"]["x-heare-token"] == ""
    assert json.loads(pairing["body"]) == {
        "code": "ABCD2345", "kind": "machine", "name": "esp32c3-a1b2"}

    knock = server.requests[1]
    assert knock["path"] == "/api/bodies"
    assert knock["headers"]["x-heare-token"] == "machine-secret"
    assert json.loads(knock["body"]) == {
        "v": 1,
        "version": VERSION,
        "key": PID,
        "host": "esp32c3-a1b2",
        "channel": "oled-a1b2",
        "forms": ["output"],
        "about": ABOUT,
        "ui": [{"name": "now", "kind": "label", "label": "Screen",
                "value": "BOOT", "head": True}],
        "hears": "all",
        "approves": True,
    }
    assert "started" not in json.loads(knock["body"])

    assert store.get_part_token() == "part-secret"
    assert store.get_pid() == PID
    assert link.state == state.BRAIN
    assert link.auto_ap is False


def test_the_machine_key_is_in_no_stored_blob():
    server = FakeServer([
        _json({"pid": MACHINE_PID, "token": "machine-secret"}),
        _json({"approved": True, "pid": PID, "token": "part-secret"}),
    ])
    nvs = FakeNVS()
    store = Store(nvs)
    _link(server, store).pair("ABCD2345")

    stored = {key: bytes(value) for key, value in nvs.blobs.items()}
    assert stored["part_token"] == b"part-secret"
    assert stored["device_pid"] == PID.encode()
    assert b"machine-secret" not in b"".join(stored.values())


def test_a_part_knock_names_its_own_pid_and_does_not_rotate_the_key():
    server = FakeServer([
        _json({"approved": True, "pid": PID, "token": "would-rotate"}),
    ])
    store = _store()
    store.set_part_token("part-secret")
    store.set_pid(PID)
    link = _link(server, store)

    answer = link.knock("part-secret", part=True)

    assert answer["approved"] is True
    assert server.requests[0]["headers"]["x-heare-token"] == "part-secret"
    body = json.loads(server.requests[0]["body"])
    assert body["key"] == PID
    assert body["key"] == link.pid
    assert store.get_part_token() == "part-secret"


def test_a_401_on_knock_clears_the_key_and_says_pair():
    server = FakeServer([_text("401 Unauthorized", "no token")])
    store = _store()
    store.set_part_token("stale-secret")
    store.set_pid(PID)
    link = _link(server, store)

    with pytest.raises(NeedsProvisioning) as raised:
        link.knock("stale-secret", part=True)

    assert store.get_part_token() is None
    assert link.auto_ap is True
    assert link.needs_provisioning is True
    assert link.state == state.PAIR
    assert "stale-secret" not in str(raised.value)
    assert len(server.requests) == 1


def test_supervise_stops_on_401_instead_of_hammering(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(link_module.time, "sleep", slept.append)
    server = FakeServer([_text("401 Unauthorized", "no token")])
    store = _store()
    store.set_part_token("stale-secret")
    store.set_pid(PID)
    link = _link(server, store, clock=FakeClock())

    with pytest.raises(NeedsProvisioning):
        link.supervise()

    assert len(server.requests) == 1
    assert slept == []
    assert link.state == state.PAIR


def test_a_429_waits_a_minute_and_then_keeps_going(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(link_module.time, "sleep", slept.append)
    server = FakeServer([
        _text("429 Too Many Requests", "slow down"),
        _json({"approved": True, "pid": PID}),
        _stream(),
    ])
    store = _store()
    store.set_part_token("part-secret")
    store.set_pid(PID)
    seen: list[str] = []

    def on_state(word: str) -> None:
        seen.append(word)
        link.close()

    link = _link(server, store, clock=FakeClock(), on_state=on_state)
    link.supervise()

    assert len(slept) == 1
    assert 60 <= slept[0] < 61
    assert server.paths() == ["/api/bodies", "/api/bodies",
                              "/api/events?kinds=" + EVENT_KINDS]
    assert seen[0] == state.IDLE


def test_silence_re_knocks_before_opening_the_stream_again(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(link_module.time, "sleep", slept.append)
    server = FakeServer([
        _json({"approved": True, "pid": PID}),
        _stream(),
        _text("401 Unauthorized", "gone"),
    ])
    store = _store()
    store.set_part_token("part-secret")
    store.set_pid(PID)
    link = _link(server, store, clock=FakeClock())

    with pytest.raises(NeedsProvisioning):
        link.supervise()

    assert server.paths() == [
        "/api/bodies",
        "/api/events?kinds=" + EVENT_KINDS,
        "/api/bodies",
    ]
    assert slept == [1.0]
    assert link.state == state.PAIR


def test_backoff_grows_to_thirty_seconds_with_jitter_below_one():
    clock = FakeClock()
    link = _link(FakeServer([]), clock=clock)

    assert [link._delay(attempt) for attempt in range(6)] == [1, 2, 4, 8, 15, 30]
    assert link._delay(99) == 30
    clock.advance(500)
    assert link._delay(0) == 1.5
    clock.advance(499)
    assert 1 <= link._delay(0) < 2


def test_report_posts_only_when_the_values_change():
    server = FakeServer([
        _json({"ok": True}),
        _json({"ok": True}),
    ])
    store = _store()
    store.set_part_token("part-secret")
    store.set_pid(PID)
    link = _link(server, store)

    assert link.report({"now": "IDLE"}) is True
    assert link.report({"now": "IDLE"}) is False
    assert link.report({"now": "THINK"}) is True

    reports = server.bodies("/api/input")
    assert len(reports) == 2
    assert reports[0] == {"channel": "oled-a1b2", "kind": "state",
                          "values": {"now": "IDLE"}}
    assert reports[1]["values"] == {"now": "THINK"}
    assert all("text" not in report for report in reports)


def test_events_use_the_header_and_feed_the_reducer():
    server = FakeServer([
        _stream({"kind": "phase", "phase": "hearing"}),
    ])
    store = _store()
    store.set_part_token("part-secret")
    store.set_pid(PID)
    seen: list[str] = []
    link = _link(server, store, on_state=seen.append)

    assert link.events() is True

    request = server.requests[0]
    assert request["path"] == "/api/events?kinds=" + EVENT_KINDS
    assert "token=" not in request["path"]
    assert request["headers"]["x-heare-token"] == "part-secret"
    assert seen == [state.IDLE, state.HEAR]
    assert link.state == state.HEAR


def test_an_unapproved_knock_shows_pair_and_backs_off(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(link_module.time, "sleep", slept.append)
    server = FakeServer([
        _json({"approved": False, "why": "still waiting"}),
    ])
    store = _store()
    store.set_part_token("part-secret")
    store.set_pid(PID)
    link = _link(server, store, clock=FakeClock())

    link.supervise(stop=lambda: len(server.requests) >= 1)

    assert len(server.requests) == 1
    assert slept == [1.0]
    assert link.state == state.PAIR


def test_a_rejected_knock_shows_an_error(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(link_module.time, "sleep", slept.append)
    server = FakeServer([_json({"approved": False, "why": "rejected"})])
    store = _store()
    store.set_part_token("part-secret")
    store.set_pid(PID)
    link = _link(server, store, clock=FakeClock())

    link.supervise(stop=lambda: len(server.requests) >= 1)

    assert slept == [1.0]
    assert link.state == state.ERROR


def test_a_refused_pair_code_is_a_clear_link_error():
    server = FakeServer([_text("401 Unauthorized", "bad code")])
    link = _link(server)

    with pytest.raises(LinkError) as raised:
        link.pair("ZZZZZZZZ")

    assert "pair code" in str(raised.value)
    assert "ZZZZZZZZ" not in str(raised.value)
    assert len(server.requests) == 1


def test_no_key_reaches_stdout_stderr_or_repr(capsys):
    server = FakeServer([
        _json({"pid": MACHINE_PID, "token": "machine-secret"}),
        _json({"approved": True, "pid": PID, "token": "part-secret"}),
    ])
    link = _link(server)
    link.pair("ABCD2345")

    captured = capsys.readouterr()
    assert "machine-secret" not in captured.out
    assert "machine-secret" not in captured.err
    assert "part-secret" not in captured.out
    assert "part-secret" not in captured.err
    assert "part-secret" not in repr(link)
    assert "machine-secret" not in repr(link)


def test_a_stream_401_clears_the_key_before_it_raises():
    server = FakeServer([_text("401 Unauthorized", "gone")])
    store = _store()
    store.set_part_token("stale-secret")
    store.set_pid(PID)
    link = _link(server, store)

    with pytest.raises(NeedsProvisioning):
        link.events()

    assert store.get_part_token() is None
    assert link.state == state.PAIR


def test_a_missing_key_asks_for_pairing_before_any_socket():
    server = FakeServer([])
    link = _link(server)

    with pytest.raises(NeedsProvisioning):
        link.events()
    with pytest.raises(NeedsProvisioning):
        link.supervise()

    assert server.requests == []
    assert link.auto_ap is True


def test_identity_comes_from_the_stored_pid_after_pairing():
    store = _store()
    store.set_pid(PID)
    link = Link(_config(), store, socket_factory=FakeServer([]).socket)

    assert link.channel == "oled-a1b2"
    assert link.host == "esp32c3-a1b2"
    assert link.pid == PID


def test_identity_accepts_a_bare_board_tag_from_the_config():
    config = _config()
    config["device"]["name"] = "a1b2"
    link = Link(config, _store(), socket_factory=FakeServer([]).socket)

    assert link.channel == "oled-a1b2"
    assert link.host == "esp32c3-a1b2"
    assert link.pid == PID


def test_a_knock_with_the_stored_part_key_needs_no_flag():
    server = FakeServer([_json({"approved": True, "pid": PID})])
    store = _store()
    store.set_part_token("part-secret")
    store.set_pid(PID)
    link = _link(server, store)

    assert link.knock("part-secret")["approved"] is True
    assert json.loads(server.requests[0]["body"])["key"] == PID


def test_permit_says_only_yes_to_the_one_permission():
    server = FakeServer([_json({"answered": True}), _json({"answered": False})])
    store = _store()
    store.set_part_token("part-secret")
    link = _link(server, store)

    assert link.permit(7) is True
    assert link.permit(8) is False

    assert server.paths() == ["/api/permission/7", "/api/permission/8"]
    assert server.bodies("/api/permission/7") == [{"allow": True}]
    assert server.requests[0]["headers"]["x-heare-token"] == "part-secret"


def test_permit_without_a_key_asks_nothing():
    server = FakeServer([])

    assert _link(server, _store()).permit(7) is False
    assert server.requests == []
