"""The panel's HTTP/1.0 client, driven byte for byte.

Every response here is bytes, never a live server: the point is that the
client survives a head split one byte at a time, an answer with no
``Content-Length``, and an answer that is not JSON at all.
"""

from __future__ import annotations

import json

import pytest
from heare_panel.http import (
    HttpError,
    build_request,
    open_stream,
    post_json,
    read_head,
)

from tests.firmware.fakes import FakeSocket


class Trickle(FakeSocket):
    """A socket that hands over one byte per call, however much is asked."""

    def recv(self, size: int) -> bytes:
        return super().recv(1)


def _answer(status: str = "200 OK", headers: tuple[tuple[str, str], ...] = (),
            body: bytes = b"") -> bytes:
    lines = [f"HTTP/1.0 {status}"]
    lines.extend(f"{name}: {value}" for name, value in headers)
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def test_a_knock_is_exact_bytes_in_the_order_the_brain_expects():
    request = build_request(
        "POST", "/api/bodies", "192.168.1.10", "abc", b'{"v":1}')
    head, _, body = request.partition(b"\r\n\r\n")
    assert head.split(b"\r\n") == [
        b"POST /api/bodies HTTP/1.0",
        b"Host: 192.168.1.10",
        b"X-Heare-Token: abc",
        b"Content-Type: application/json",
        b"Content-Length: 7",
        b"Connection: close",
    ]
    assert body == b'{"v":1}'


def test_the_port_is_only_named_when_it_is_not_the_default():
    plain = build_request("GET", "/api/events", "10.0.0.2", "t")
    moved = build_request("GET", "/api/events", "10.0.0.2", "t", port=9790)
    assert b"\r\nHost: 10.0.0.2\r\n" in plain
    assert b"\r\nHost: 10.0.0.2:9790\r\n" in moved


def test_an_empty_request_still_declares_an_empty_body():
    request = build_request("GET", "/api/events", "brain", "t")
    assert b"Content-Length: 0\r\n" in request
    assert request.endswith(b"\r\n\r\n")


def test_a_dictionary_body_is_encoded_as_json():
    request = build_request("POST", "/api/bodies", "brain", "t",
                            {"channel": "panel"})
    head, _, body = request.partition(b"\r\n\r\n")
    assert f"Content-Length: {len(body)}".encode() in head
    assert json.loads(body) == {"channel": "panel"}


def test_a_head_split_one_byte_at_a_time_is_still_one_head():
    raw = _answer("200 OK", (("Content-Type", "text/event-stream"),
                             ("Cache-Control", "no-cache")))
    sock = Trickle(raw)
    status, headers = read_head(sock)
    assert status == 200
    assert headers == {"content-type": "text/event-stream",
                       "cache-control": "no-cache"}
    assert sock.incoming == b""


def test_header_names_are_lowercased_whatever_their_case():
    sock = FakeSocket(_answer("403 Forbidden", (
        ("CONTENT-LENGTH", "2"), ("X-HEARE-TOKEN", "d"),
    )))
    status, headers = read_head(sock)
    assert status == 403
    assert headers == {"content-length": "2", "x-heare-token": "d"}


@pytest.mark.parametrize("status", [401, 403])
def test_an_auth_refusal_is_returned_not_raised(status):
    body = b'{"message": "no"}'
    sock = FakeSocket(_answer(f"{status} No", (
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ), body))
    got_status, payload = post_json(
        sock, "POST", "/api/bodies", "brain", "stale", b"{}")
    assert got_status == status
    assert payload == {"message": "no"}


def test_post_json_reads_exactly_the_declared_length():
    body = b'{"approved": true}'
    sock = FakeSocket(_answer("200 OK", (("Content-Length", str(len(body))),),
                              body + b"NEXT-RESPONSE"))
    status, payload = post_json(
        sock, "POST", "/api/bodies", "brain", "t", b"{}")
    assert status == 200
    assert payload == {"approved": True}
    assert sock.incoming == b"NEXT-RESPONSE"


def test_post_json_reads_to_eof_when_there_is_no_content_length():
    sock = FakeSocket(_answer("200 OK", (), b'{"ok": 1}\r\n'))
    status, payload = post_json(sock, "GET", "/api/state", "brain", "t")
    assert status == 200
    assert payload == {"ok": 1}


def test_an_empty_body_is_an_empty_object():
    sock = FakeSocket(_answer("204 No Content"))
    status, payload = post_json(sock, "GET", "/api/nothing", "brain", "t")
    assert status == 204
    assert payload == {}


def test_a_body_cut_short_by_eof_is_an_error():
    sock = FakeSocket(_answer("200 OK", (("Content-Length", "20"),), b"{}"))
    with pytest.raises(HttpError, match="body was complete"):
        post_json(sock, "GET", "/api/state", "brain", "t")


def test_a_head_without_its_terminator_is_an_error():
    sock = FakeSocket(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n")
    with pytest.raises(HttpError, match="head was complete"):
        read_head(sock)


def test_a_head_larger_than_four_kilobytes_is_an_error():
    sock = FakeSocket(b"HTTP/1.0 200 OK\r\nX: " + b"a" * 4096)
    with pytest.raises(HttpError, match="larger than 4096"):
        read_head(sock)


def test_a_declared_body_larger_than_eight_kilobytes_is_an_error():
    sock = FakeSocket(_answer("200 OK", (("Content-Length", "8193"),)))
    with pytest.raises(HttpError, match="too large"):
        post_json(sock, "GET", "/api/state", "brain", "t")


def test_a_body_read_to_eof_past_eight_kilobytes_is_an_error():
    sock = FakeSocket(_answer("200 OK", (), b"x" * 8193))
    with pytest.raises(HttpError, match="larger than 8192"):
        post_json(sock, "GET", "/api/state", "brain", "t")


def test_a_non_json_body_is_an_error():
    sock = FakeSocket(_answer("200 OK", (("Content-Length", "7"),), b"no json"))
    with pytest.raises(HttpError, match="not JSON"):
        post_json(sock, "GET", "/api/state", "brain", "t")


def test_a_json_list_is_not_a_response_object():
    sock = FakeSocket(_answer("200 OK", (("Content-Length", "2"),), b"[]"))
    with pytest.raises(HttpError, match="JSON object"):
        post_json(sock, "GET", "/api/state", "brain", "t")


def test_open_stream_reads_the_head_and_leaves_the_stream_alone():
    sock = FakeSocket(_answer("200 OK", (
        ("Content-Type", "text/event-stream"),
        ("Cache-Control", "no-cache"),
    ), b"data: {\"kind\": \"phase\"}\n\n"))
    status, headers = open_stream(sock, "GET", "/api/events", "brain", "t")
    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    assert sock.incoming == b'data: {"kind": "phase"}\n\n'


@pytest.mark.parametrize("encoding", ["chunked", "Chunked", "gzip, chunked"])
def test_chunked_is_a_tripwire_not_a_parser(encoding):
    sock = FakeSocket(_answer("200 OK", (("Transfer-Encoding", encoding),)))
    with pytest.raises(HttpError, match="chunked is not expected"):
        open_stream(sock, "GET", "/api/events", "brain", "t")


def test_a_stream_that_is_not_ok_says_which_status():
    sock = FakeSocket(_answer("403 Forbidden", (("Content-Length", "0"),)))
    with pytest.raises(HttpError) as raised:
        open_stream(sock, "GET", "/api/events", "brain", "stale")
    assert raised.value.status == 403
    assert "403" in str(raised.value)


def test_the_stream_request_is_sent_before_the_head_is_read():
    sock = FakeSocket(_answer("200 OK"))
    open_stream(sock, "GET", "/api/events", "brain", "secret", port=9790)
    assert sock.sent.startswith(b"GET /api/events HTTP/1.0\r\n")
    assert b"X-Heare-Token: secret\r\n" in sock.sent
    assert b"Host: brain:9790\r\n" in sock.sent
