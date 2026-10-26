from heare_panel import pair_web


def _get(path="/"):
    return f"GET {path} HTTP/1.1\r\nHost: heare-panel.local\r\n\r\n".encode()


def _post(fields):
    body = "&".join(f"{key}={value}" for key, value in fields.items()).encode()
    head = (
        "POST /pair HTTP/1.1\r\nHost: heare-panel.local\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode()
    return head + body


def _form(**changes):
    fields = {
        "screen_code": "4821",
        "brain_host": "192.168.1.10",
        "brain_port": "9790",
        "name": "desk",
        "pair_code": "abcd2345",
    }
    fields.update(changes)
    return fields


def _page(handler=None, **kwargs):
    calls = []

    def default(command):
        calls.append(command)
        return {"ok": True, "ip": "192.168.1.50", "pid": "oled-desk@esp32c3-desk"}

    page = pair_web.PairPage(handler or default, "4821", out=lambda line: None, **kwargs)
    return page, calls


def _status(response):
    return int(response.split(b" ", 2)[1])


def test_the_form_guesses_the_brain_is_the_machine_that_opened_it():
    page, _calls = _page()

    response = page.respond(_get(), client="192.168.1.10")

    assert _status(response) == 200
    assert b'value="192.168.1.10"' in response
    assert b'value="9790"' in response


def test_a_right_screen_code_pairs_through_the_handler():
    page, calls = _page()

    response = page.respond(_post(_form()))

    assert _status(response) == 200
    assert "Готово".encode() in response
    assert page.paired is True
    assert calls == [
        {
            "cmd": "save",
            "brain_host": "192.168.1.10",
            "brain_port": "9790",
            "name": "desk",
            "pair_code": "ABCD2345",
        }
    ]


def test_a_wrong_screen_code_never_reaches_the_handler():
    page, calls = _page()

    response = page.respond(_post(_form(screen_code="0000")))

    assert _status(response) == 403
    assert calls == []
    assert page.paired is False


def test_five_wrong_codes_close_the_page_even_for_the_right_one():
    page, calls = _page()
    for _ in range(pair_web.MAX_CODE_FAILURES):
        page.respond(_post(_form(screen_code="0000")))

    response = page.respond(_post(_form()))

    assert _status(response) == 429
    assert calls == []


def test_a_refused_pair_code_says_so_and_keeps_what_was_typed():
    page, _calls = _page(handler=lambda command: {"ok": False, "error": "pair"})

    response = page.respond(_post(_form(name="kitchen")))

    assert "Мозок не прийняв код пари".encode() in response
    assert b'value="kitchen"' in response
    assert page.paired is False


def test_secrets_are_never_echoed_into_the_page():
    page, _calls = _page(handler=lambda command: {"ok": False, "error": "pair"})

    response = page.respond(_post(_form()))

    assert b"4821" not in response
    assert b"ABCD2345" not in response.upper()


def test_a_handler_that_raises_is_an_error_page_not_a_dead_server():
    def broken(command):
        raise MemoryError

    page, _calls = _page(handler=broken)

    response = page.respond(_post(_form()))

    assert _status(response) == 200
    assert page.paired is False


def test_garbage_is_a_400():
    page, _calls = _page()

    assert _status(page.respond(b"nonsense")) == 400
