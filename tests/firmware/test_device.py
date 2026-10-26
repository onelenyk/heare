from heare_panel import device


class FakePin:
    def __init__(self, number, mode):
        self.number, self.mode, self.level = number, mode, 0

    def value(self, level=None):
        if level is None:
            return self.level
        self.level = level


def _device():
    made = {}

    def factory(number, mode):
        made[(number, mode)] = FakePin(number, mode)
        return made[(number, mode)]

    return device.Device(factory), made


def test_the_board_offers_only_pins_nothing_else_uses():
    names = {s["name"] for s in device.skills() if s["effect"] == "change"}
    assert names == {"pin" + str(n) for n in device.FREE_PINS}
    for taken in (5, 6, 8, 9, 12, 13, 14, 15, 16, 17, 18, 19):
        assert "pin" + str(taken) not in names


def test_a_pin_outside_the_list_is_never_touched():
    board, made = _device()
    for name in ("pin9", "pin12", "pin18", "pinx", "led2"):
        assert board.apply(name, True, 0) is False
    assert made == {}


def test_a_switched_pin_becomes_an_output_and_reads_back():
    board, made = _device()
    assert board.levels()["pin3"] == 0
    assert board.apply("pin3", True, 0) is True
    assert made[(3, "out")].level == 1
    assert board.levels()["pin3"] == 1
    assert board.apply("pin3", "yes", 0) is False


def test_words_are_shown_for_their_time_then_the_face_comes_back():
    board, _ = _device()
    assert board.apply("show", {"text": "chainyk gotovyi\nhi", "seconds": 2}, 1000)
    assert board.text(1500) == ["chainyk", "gotovyi", "hi"]
    assert board.text(3001) is None
    assert board.apply("show", {"text": ""}, 0) is False


def test_the_light_is_lit_by_pulling_its_pin_low():
    board, made = _device()
    board.apply("led", "on", 0)
    board.tick(0)
    assert made[(device.LED_PIN, "out")].level == 0
    board.apply("led", "blink", 0)
    board.tick(device.BLINK_MS)
    assert made[(device.LED_PIN, "out")].level == 1
    assert board.apply("led", "disco", 0) is False


def test_lines_fit_the_small_window():
    assert device.lines("abcdefghijklmnop") == ["abcdefghi", "jklmnop"]
    assert len(device.lines("a\nb\nc\nd\ne")) == device.MAX_LINES


def test_every_skill_has_the_form_the_brain_keeps():
    for skill in device.skills():
        assert {"name", "about", "effect", "params"} <= set(skill) <= {
            "name", "about", "effect", "params", "render"}
        assert skill["effect"] in ("read", "harmless", "change")
        assert skill["params"]["type"] == "object"


def test_a_call_is_answered_with_what_came_of_it():
    board, made = _device()
    assert board.call("show", {"text": "Привіт", "seconds": 5}, 0) == (True, "показано: Pryvit")
    assert board.call("light", {"mode": "blink"}, 0) == (True, "світло: blink")
    assert board.call("pin3", {"on": True}, 0) == (True, "pin3: 1")
    assert board.call("read_pins", {}, 0)[1]["pin3"] == 1
    assert board.call("pin9", {"on": True}, 0) == (False, "такого скіла немає")
    assert board.call("pin3", {"on": "yes"}, 0)[0] is False
    assert board.call("light", {"mode": "disco"}, 0)[0] is False
    assert (9, "out") not in made


def test_ukrainian_is_written_out_for_the_font():
    assert device.latin("Привіт, Назаре!") == "Pryvit, Nazare!"
    assert device.latin("Щастя їжака") == "Shchastia izhaka"
    assert device.latin("hello 42") == "hello 42"


def test_a_picture_the_hands_drew_is_shown_for_its_time():
    import base64

    board, _made = _device()
    bits = bytes(range(256)) + bytes(104)
    ok, said = board.call("image", {"bitmap": base64.b64encode(bits).decode(), "seconds": 2}, 0)
    assert (ok, said) == (True, "картинку показано")
    assert board.drawing(1000) == bits
    assert board.drawing(2001) is None


def test_a_picture_of_the_wrong_size_is_refused_and_words_replace_a_picture():
    import base64

    board, _made = _device()
    assert board.call("image", {"bitmap": base64.b64encode(bytes(10)).decode()}, 0)[0] is False
    assert board.call("image", {"bitmap": "not base64!!"}, 0)[0] is False
    board.call("image", {"bitmap": base64.b64encode(bytes(360)).decode()}, 0)
    board.call("show", {"text": "hi"}, 0)
    assert board.drawing(1) is None and board.text(1) == ["hi"]


def test_the_image_skill_asks_for_the_window_s_own_pixels():
    image = next(s for s in device.skills() if s["name"] == "image")
    assert image["render"] == {"svg": {"to": "mono", "width": 72, "height": 40, "as": "bitmap"}}
