"""Request and form parsing for the pairing page, tested as bytes.

The form tests pin the two rules that matter for trust: percent decoding and
no duplicate keys.
"""

from __future__ import annotations


import pytest
from heare_panel import config, portal
from heare_panel.portal import PortalError, parse_form, parse_request

PASSWORD = "hunter2-secret"
PAIR_CODE = "ABCD2345"


def http(
    method: str,
    target: str,
    headers: tuple[tuple[str, str], ...] = (),
    body: bytes = b"",
) -> bytes:
    lines = [f"{method} {target} HTTP/1.1"]
    lines.extend(f"{name}: {value}" for name, value in headers)
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def test_the_caps_are_the_documented_ones():
    assert portal.MAX_HEAD == 4096
    assert portal.MAX_BODY == 1024
    assert portal.DEFAULT_PORT == config.DEFAULT_PORT


def test_a_get_is_split_into_method_path_headers_and_no_body():
    raw = http(
        "GET",
        "/hotspot-detect.html?from=phone",
        (("Host", "captive.apple.com"), ("Accept", "*/*")),
    )
    method, path, headers, body = parse_request(raw)
    assert method == "GET"
    assert path == "/hotspot-detect.html"
    assert headers == {"host": "captive.apple.com", "accept": "*/*"}
    assert body == b""


def test_the_method_is_uppercased_and_a_form_body_is_kept_whole():
    raw = http(
        "post",
        "/save",
        (
            ("Content-Length", "23"),
            ("Content-Type", "application/x-www-form-urlencoded"),
        ),
        b"ssid=kitchen&password=x",
    )
    method, path, headers, body = parse_request(raw)
    assert method == "POST"
    assert path == "/save"
    assert headers["content-length"] == "23"
    assert body == b"ssid=kitchen&password=x"


def test_a_body_longer_than_content_length_is_truncated():
    raw = http("POST", "/save", (("Content-Length", "3"),), b"abcEXTRA")
    assert parse_request(raw)[3] == b"abc"


def test_a_post_without_content_length_is_refused():
    with pytest.raises(PortalError, match="POST requires Content-Length"):
        parse_request(http("POST", "/save"))


def test_a_repeated_content_length_is_refused():
    raw = http(
        "POST", "/save", (("Content-Length", "1"), ("Content-Length", "1")), b"a"
    )
    with pytest.raises(PortalError, match="duplicate content-length"):
        parse_request(raw)


def test_a_body_over_the_cap_is_refused_before_it_is_read():
    raw = http("POST", "/save", (("Content-Length", "1025"),), b"x" * 1025)
    with pytest.raises(PortalError, match="request body is larger than 1024"):
        parse_request(raw)


def test_a_body_of_exactly_the_cap_is_accepted():
    body = b"a=" + b"x" * 1022
    raw = http("POST", "/save", (("Content-Length", "1024"),), body)
    assert parse_request(raw)[3] == body


def test_a_short_body_is_refused():
    raw = http("POST", "/save", (("Content-Length", "10"),), b"abc")
    with pytest.raises(PortalError, match="shorter than Content-Length"):
        parse_request(raw)


def test_a_head_over_the_cap_is_refused():
    raw = http("GET", "/", (("X-Long", "a" * 5000),))
    with pytest.raises(PortalError, match="request head is larger than 4096"):
        parse_request(raw)


def test_an_unterminated_head_is_refused():
    with pytest.raises(PortalError, match="no header terminator"):
        parse_request(b"GET / HTTP/1.1\r\nHost: x\r\n")


@pytest.mark.parametrize(
    "raw",
    [
        "GET / HTTP/1.1\r\n\r\n",
        None,
        b"GET /\r\n\r\n",
        b"G3T / HTTP/1.1\r\n\r\n",
        b"GET / HTTP/1.1\r\nBroken\r\n\r\n",
        b"POST /save HTTP/1.1\r\nContent-Length: 1x\r\n\r\nx",
        b"POST /save HTTP/1.1\r\nContent-Length: \r\n\r\n",
    ],
)
def test_malformed_requests_raise_portal_error(raw):
    with pytest.raises(PortalError):
        parse_request(raw)


def test_parse_form_decodes_every_field():
    body = b"ssid=kitchen&password=hunter2-secret&brain_port=9790&name=kitchen"
    assert parse_form(body) == {
        "ssid": "kitchen",
        "password": PASSWORD,
        "brain_port": "9790",
        "name": "kitchen",
    }


def test_plus_is_a_space_and_percent_is_one_byte():
    assert parse_form(b"name=my+panel&ssid=Caf%C3%A9%20Net") == {
        "name": "my panel",
        "ssid": "Café Net",
    }
    assert parse_form(b"password=a%2Bb")["password"] == "a+b"


def test_an_empty_or_missing_value_is_an_empty_string():
    assert parse_form(b"") == {}
    assert parse_form(b"pair_code=") == {"pair_code": ""}
    assert parse_form(b"pair_code") == {"pair_code": ""}
    assert parse_form(b"a=1&&b=2&") == {"a": "1", "b": "2"}


def test_a_repeated_key_is_refused():
    with pytest.raises(PortalError, match="duplicate form field: ssid"):
        parse_form(b"ssid=one&ssid=two")


def test_a_repeated_key_is_caught_after_percent_decoding():
    with pytest.raises(PortalError, match="duplicate form field: ssid"):
        parse_form(b"ssid=one&%73sid=two")


@pytest.mark.parametrize("body", [b"a=%", b"a=%2", b"a=%zz", b"a=%2G"])
def test_a_bad_percent_escape_is_refused(body):
    with pytest.raises(PortalError, match="invalid percent escape"):
        parse_form(body)


def test_invalid_utf8_is_refused():
    with pytest.raises(PortalError, match="not valid UTF-8"):
        parse_form(b"ssid=%FF")


def test_a_form_over_the_cap_is_refused():
    with pytest.raises(PortalError, match="form body is larger than 1024"):
        parse_form(b"a=" + b"x" * 1025)


def test_a_form_must_be_bytes():
    with pytest.raises(PortalError, match="form body must be bytes"):
        parse_form("ssid=kitchen")


