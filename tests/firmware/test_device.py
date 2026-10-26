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
    names = {a["name"] for a in device.acts() if a["kind"] == "switch"}
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
