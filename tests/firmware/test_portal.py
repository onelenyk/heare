"""The captive portal core, tested as bytes and as one page.

The DNS tests compare whole packets, not fields: the OS's probe is unforgiving
about a wrong length or a missing name pointer. The form tests pin the two
rules that matter for trust (percent decoding and no duplicate keys), and the
HTML tests prove a scanned SSID cannot become markup and neither secret can
become page source.
"""

from __future__ import annotations

import struct

import pytest
from heare_panel import config, portal
from heare_panel.portal import (
    AP_IP,
    AP_URL,
    PortalError,
    dns_reply,
    parse_form,
    parse_request,
    probe_response,
    render_page,
)

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


def query(
    name: str = "captive.apple.com",
    qtype: int = portal.DNS_TYPE_A,
    ident: int = 0x1A2B,
    rd: bool = True,
) -> bytes:
    labels = bytearray()
    for label in name.split("."):
        labels.append(len(label))
        labels.extend(label.encode())
    labels.append(0)
    flags = 0x0100 if rd else 0
    header = struct.pack("!HHHHHH", ident, flags, 1, 0, 0, 0)
    return header + bytes(labels) + struct.pack("!HH", qtype, 1)


def question_of(name: str, qtype: int) -> bytes:
    raw = query(name=name, qtype=qtype)
    return raw[12:]


def test_the_caps_and_address_are_the_documented_ones():
    assert AP_IP == "192.168.4.1"
    assert AP_URL == "http://192.168.4.1/"
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


def test_the_page_names_every_field_and_posts_to_save():
    page = render_page(
        [], {"ssid": "kitchen", "brain_host": "192.168.1.10", "name": "kitchen"}
    )
    assert '<form method="post" action="/save">' in page
    for field in (
        "ssid",
        "ssid_manual",
        "password",
        "brain_host",
        "brain_port",
        "name",
        "pair_code",
    ):
        assert f'name="{field}"' in page
    assert '<select name="ssid">' in page


def test_the_scan_fills_the_select_and_marks_the_current_network():
    page = render_page(
        [{"ssid": "kitchen", "rssi": -44}, {"ssid": "study", "rssi": -70}],
        {"ssid": "study"},
    )
    assert '<option value="kitchen">kitchen</option>' in page
    assert '<option value="study" selected>study</option>' in page
    assert "ssid_manual" in page
    manual = page.split('id="ssid_manual"')[1].split(">")[0]
    assert 'value=""' in manual


def test_a_custom_ssid_is_prefilled_in_the_manual_box():
    page = render_page([], {"ssid": "attic"})
    manual = page.split('id="ssid_manual"')[1].split(">")[0]
    assert 'value="attic"' in manual
    assert "no networks found" in page


def test_a_scanned_ssid_cannot_become_markup():
    hostile = '<script>alert("x")</script>'
    page = render_page([{"ssid": hostile, "rssi": -50}], {"ssid": hostile})
    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page


def test_typed_values_are_escaped_into_their_attributes():
    page = render_page(
        [],
        {
            "brain_host": '1.2.3.4"><b>',
            "name": "kitchen' onload='x",
            "brain_port": "9790",
        },
    )
    assert 'value="1.2.3.4&quot;&gt;&lt;b&gt;"' in page
    assert "kitchen&#39; onload=&#39;x" in page


def test_the_password_is_never_reflected():
    page = render_page(
        [],
        {"ssid": "kitchen", "password": PASSWORD},
        error="wifi password must be 8-63 characters or empty",
    )
    assert PASSWORD not in page
    password_tag = page.split('id="password"')[1].split(">")[0]
    assert "value=" not in password_tag


def test_the_pair_code_is_never_reflected_after_submit():
    page = render_page([], {"ssid": "kitchen", "pair_code": PAIR_CODE})
    assert PAIR_CODE not in page
    code_tag = page.split('id="pair_code"')[1].split(">")[0]
    assert "value=" not in code_tag


def test_an_error_is_shown_and_escaped():
    page = render_page([], {}, error='<b>bad & "code"</b>')
    assert '<p class="err" role="alert">' in page
    assert "&lt;b&gt;bad &amp; &quot;code&quot;&lt;/b&gt;" in page
    assert "<b>" not in page


def test_an_error_cannot_echo_a_secret_even_by_accident():
    values = {"password": PASSWORD, "pair_code": PAIR_CODE}
    page = render_page([], values, error=f"bad {PASSWORD} and {PAIR_CODE}")
    assert PASSWORD not in page
    assert PAIR_CODE not in page
    assert "***" in page


def test_the_page_has_no_external_assets_and_fits_the_c3():
    networks = [
        {"ssid": f"network-{index:02d}-" + "x" * 20, "rssi": -40 - index}
        for index in range(64)
    ]
    page = render_page(
        networks,
        {
            "ssid": "network-00-" + "x" * 20,
            "brain_host": "9" * 100,
            "name": "n" * 100,
        },
        error="e" * 10000,
    )
    assert len(page.encode("utf-8")) < 6 * 1024
    assert page.count("<option") == portal.MAX_NETWORKS
    assert "<script" not in page.lower()
    assert "src=" not in page
    assert "href=" not in page
    assert "url(" not in page


def test_the_page_survives_missing_values_and_networks():
    page = render_page(None, None)
    assert "<h1>heare panel</h1>" in page
    assert f'value="{portal.DEFAULT_PORT}"' in page


def test_a_dns_a_answer_is_one_exact_golden_packet():
    raw = query()
    reply = dns_reply(raw)
    expected = (
        struct.pack("!HHHHHH", 0x1A2B, 0x8180, 1, 1, 0, 0)
        + question_of("captive.apple.com", portal.DNS_TYPE_A)
        + b"\xc0\x0c"
        + struct.pack("!HHIH", 1, 1, 60, 4)
        + bytes((192, 168, 4, 1))
    )
    assert reply == expected
    assert reply[-4:] == bytes((192, 168, 4, 1))


def test_a_dns_aaaa_answer_is_noerror_with_no_records():
    raw = query(qtype=portal.DNS_TYPE_AAAA)
    reply = dns_reply(raw)
    expected = struct.pack("!HHHHHH", 0x1A2B, 0x8180, 1, 0, 0, 0) + question_of(
        "captive.apple.com", portal.DNS_TYPE_AAAA
    )
    assert reply == expected
    assert struct.unpack("!H", reply[2:4])[0] & 0x000F == 0
    assert struct.unpack("!H", reply[6:8])[0] == 0


def test_the_dns_id_and_rd_bit_are_echoed():
    raw = query(ident=0xABCD, rd=False)
    reply = dns_reply(raw)
    assert reply[:2] == b"\xab\xcd"
    assert struct.unpack("!H", reply[2:4])[0] == 0x8080


def test_a_multi_label_name_is_copied_into_the_answer():
    raw = query(name="a.b.c.example.com")
    reply = dns_reply(raw)
    assert reply is not None
    assert question_of("a.b.c.example.com", portal.DNS_TYPE_A) in reply
    assert struct.unpack("!H", reply[6:8])[0] == 1


def test_an_unknown_type_gets_nodata():
    raw = query(qtype=16)
    reply = dns_reply(raw)
    assert reply is not None
    assert struct.unpack("!H", reply[6:8])[0] == 0
    assert reply[12:] == question_of("captive.apple.com", 16)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\x00" * 11,
        struct.pack("!HHHHHH", 1, 0x0100, 0, 0, 0, 0),
        struct.pack("!HHHHHH", 1, 0x0100, 1, 0, 0, 0),
        struct.pack("!HHHHHH", 1, 0x0100, 1, 0, 0, 0) + b"\x05ab",
        struct.pack("!HHHHHH", 1, 0x0100, 1, 0, 0, 0) + b"\x03abc\x00\x00",
        struct.pack("!HHHHHH", 1, 0x0100, 1, 0, 0, 0)
        + b"\xc0\x0c"
        + struct.pack("!HH", 1, 1),
        struct.pack("!HHHHHH", 1, 0x0100, 1, 0, 0, 0) + b"\x40" + b"x" * 64,
        None,
        "GET",
    ],
)
def test_malformed_dns_returns_none_instead_of_raising(raw):
    assert dns_reply(raw) is None


def test_every_truncation_of_a_real_query_is_refused():
    raw = query()
    for cut in range(len(raw)):
        assert dns_reply(raw[:cut]) is None


@pytest.mark.parametrize("path", portal.PROBES)
def test_every_os_probe_is_redirected_to_the_portal(path):
    status, headers, body = probe_response(path)
    assert status == 302
    assert headers["Location"] == AP_URL
    assert headers["Content-Length"] == "0"
    assert body == b""


@pytest.mark.parametrize(
    "path",
    ["/generate_204?ts=1", "/hotspot-detect.html?x=1"],
)
def test_probe_variants_are_redirected_to_the_ap(path):
    status, headers, _ = probe_response(path)
    assert status == 302
    assert headers["Location"] == AP_URL


@pytest.mark.parametrize("path", ["/favicon.ico", "/index.html", "/anything"])
def test_any_other_path_is_redirected_to_the_portal_root(path):
    status, headers, _ = probe_response(path)
    assert status == 302
    assert headers["Location"] == "/"


@pytest.mark.parametrize("path", ["/", "/save", "/save?x=1", "", None, 42])
def test_the_portal_owns_its_own_paths(path):
    assert probe_response(path) is None
