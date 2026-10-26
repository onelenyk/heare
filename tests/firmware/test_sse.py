from __future__ import annotations

from heare_panel.sse import MAX_LINE, SSEParser


def test_byte_at_a_time_two_event_stream() -> None:
    stream = (
        b'data: {"kind":"phase","phase":"idle"}\n\n'
        b'data: {"kind":"phase","phase":"thinking"}\n\n'
    )
    parser = SSEParser()
    events: list[dict] = []
    for byte in stream:
        events.extend(parser.feed(bytes([byte])))
    assert events == [
        {"kind": "phase", "phase": "idle"},
        {"kind": "phase", "phase": "thinking"},
    ]
    assert parser.buffer_len == 0
    assert parser.dropped == 0


def test_crlf_split_across_chunks() -> None:
    parser = SSEParser()
    assert parser.feed(b'data: {"a":1}\r') == []
    assert parser.feed(b"\n\r") == []
    assert parser.feed(b"\n") == [{"a": 1}]
    assert parser.buffer_len == 0


def test_multiline_data_is_joined_with_newline() -> None:
    parser = SSEParser()
    events = parser.feed(b'data: {"kind":\ndata: "phase"}\n\n')
    assert events == [{"kind": "phase"}]


def test_comment_only_heartbeat_is_ignored() -> None:
    parser = SSEParser()
    assert parser.feed(b": still here\n\n") == []
    assert parser.dropped == 0


def test_two_events_in_one_chunk_keep_their_order() -> None:
    parser = SSEParser()
    events = parser.feed(
        b'data: {"a":1}\n\ndata: {"b":2}\n\n'
    )
    assert events == [{"a": 1}, {"b": 2}]


def test_utf8_split_mid_codepoint() -> None:
    payload = 'data: {"kind":"said","text":"привіт"}\n\n'.encode()
    split = payload.index("і".encode()) + 1
    parser = SSEParser()
    assert parser.feed(payload[:split]) == []
    assert parser.feed(payload[split:]) == [
        {"kind": "said", "text": "привіт"}
    ]
    assert parser.dropped == 0


def test_oversize_line_is_drained_then_next_event_parses() -> None:
    parser = SSEParser()
    oversized = b"x" * 5000
    events: list[dict] = []
    for start in range(0, len(oversized), 500):
        events.extend(parser.feed(oversized[start:start + 500]))
        assert parser.buffer_len <= MAX_LINE
    events.extend(parser.feed(b"\n\n"))
    assert events == []
    assert parser.dropped == 1
    assert parser.buffer_len <= MAX_LINE
    assert parser.feed(b'data: {"kind":"phase","phase":"idle"}\n\n') == [
        {"kind": "phase", "phase": "idle"}
    ]
    assert parser.dropped == 1
    assert parser.buffer_len <= MAX_LINE


def test_invalid_json_is_dropped_and_the_next_event_parses() -> None:
    parser = SSEParser()
    assert parser.feed(b"data: {not json}\n\n") == []
    assert parser.dropped == 1
    assert parser.feed(b'data: {"ok":true}\n\n') == [{"ok": True}]
    assert parser.dropped == 1


def test_eof_mid_event_yields_nothing_and_recovers() -> None:
    parser = SSEParser()
    assert parser.feed(b'data: {"kind":"phase"}') == []
    assert parser.feed(b"\n") == []
    assert parser.buffer_len == 0
    assert parser.feed(b"\n") == [{"kind": "phase"}]


def test_data_prefix_without_space_and_blank_lines_between_events() -> None:
    parser = SSEParser()
    assert parser.feed(b'data:{"n":1}\n\n\n\ndata: {"n":2}\n\n') == [
        {"n": 1},
        {"n": 2},
    ]


def test_non_object_json_is_dropped() -> None:
    parser = SSEParser()
    assert parser.feed(b"data: [1, 2]\n\n") == []
    assert parser.dropped == 1
