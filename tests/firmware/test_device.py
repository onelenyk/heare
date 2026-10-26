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
    pin = next(s for s in device.skills() if s["name"] == "pin")
    offered = set(pin["params"]["properties"]["n"]["enum"])
    assert offered == set(device.FREE_PINS)
    for taken in (5, 6, 8, 9, 12, 13, 14, 15, 16, 17, 18, 19):
        assert taken not in offered
    assert [s["name"] for s in device.skills() if s["effect"] == "change"] == ["pin"]


def test_the_knock_s_skills_are_small_enough_for_the_board():
    import json

    # 26 October: 10 KB of skills in every knock starved the board.
    assert len(json.dumps(device.skills()).encode()) < 4096

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
    assert board.call("show", {"text": "Привіт", "seconds": 5}, 0) == (True, "показано: Привіт")
    assert board.call("light", {"mode": "blink"}, 0) == (True, "світло: blink")
    assert board.call("pin", {"n": 3, "on": True}, 0) == (True, "pin3: 1")
    assert board.call("read_pins", {}, 0)[1]["pin3"] == 1
    assert board.call("pin", {"n": 9, "on": True}, 0) == (False, "пін недоступний")
    assert board.call("pin", {"n": 3, "on": "yes"}, 0)[0] is False
    assert board.call("pin3", {"on": True}, 0) == (False, "такого скіла немає")
    assert board.call("light", {"mode": "disco"}, 0)[0] is False
    assert (9, "out") not in made


def test_the_image_skill_asks_for_the_window_s_own_pixels():
    image = next(s for s in device.skills() if s["name"] == "image")
    assert image["render"] == {"svg": {"to": "mono", "width": 72, "height": 40, "as": "bitmap"}}


def _seen(board, now):
    layer, scene = board.screen.seen(now)
    return layer, scene.name, dict(scene.fields)


def test_words_are_shown_for_their_time_then_the_face_comes_back():
    board, _ = _device()
    assert board.apply("show", {"text": "chainyk gotovyi\nhi", "seconds": 2}, 1000)
    assert _seen(board, 1500) == ("flash", "text", {"lines": ["chainyk", "gotovyi", "hi"]})
    assert _seen(board, 3001)[:2] == ("face", "state")
    assert board.apply("show", {"text": ""}, 0) is False


def test_a_picture_the_hands_drew_is_shown_for_its_time():
    import base64

    board, _made = _device()
    bits = bytes(range(256)) + bytes(104)
    ok, said = board.call("image", {"bitmap": base64.b64encode(bits).decode(), "seconds": 2}, 0)
    assert (ok, said) == (True, "картинку показано")
    assert _seen(board, 1000) == ("flash", "picture", {"bits": bits})
    assert _seen(board, 2001)[0] == "face"


def test_a_picture_of_the_wrong_size_is_refused_and_words_replace_a_picture():
    import base64

    board, _made = _device()
    assert board.call("image", {"bitmap": base64.b64encode(bytes(10)).decode()}, 0)[0] is False
    assert board.call("image", {"bitmap": "not base64!!"}, 0)[0] is False
    board.call("image", {"bitmap": base64.b64encode(bytes(360)).decode()}, 0)
    board.call("show", {"text": "hi"}, 0)
    assert _seen(board, 1) == ("flash", "text", {"lines": ["hi"]})


WEATHER = {"id": "weather", "fields": {"t": 21},
           "layout": {"cols": [{"widget": "icon", "name": "sun"},
                               {"w": 2, "widget": "number", "bind": "t", "unit": "°"}]}}


def test_a_scene_is_shown_on_its_layer_and_its_fields_are_updated():
    board, _ = _device()
    assert board.call("screen", {"scene": WEATHER, "layer": "pin"}, 0) == (
        True, "сцену weather показано на pin")
    layer, scene = board.screen.seen(0)
    assert (layer, scene.name, scene.fields) == ("pinned", "weather", {"t": 21})
    version = scene.version
    assert board.call("update", {"id": "weather", "fields": {"t": 22}}, 0)[0] is True
    assert scene.fields["t"] == 22 and scene.version == version + 1
    assert board.call("update", {"id": "nope", "fields": {}}, 0)[0] is False


def test_a_flash_scene_leaves_and_a_pinned_one_is_there_again():
    board, _ = _device()
    board.call("screen", {"scene": WEATHER, "layer": "pin"}, 0)
    board.call("screen", {"scene": {"id": "hi", "layout": {"widget": "text", "text": "Привіт"}},
                          "seconds": 2}, 0)
    assert board.screen.seen(1000)[1].name == "hi"
    assert board.screen.seen(2001)[1].name == "weather"
    assert board.call("clear", {"id": "weather"}, 0) == (True, "прибрано: weather")
    assert board.screen.seen(2002)[0] == "face"


def test_the_idle_screen_stands_in_for_the_face():
    board, _ = _device()
    board.call("screen", {"scene": {"id": "rest", "layout": {"widget": "clock"}}, "layer": "idle"}, 0)
    assert board.screen.seen(0)[0] == "idle"
    board.screen.word("ALLOW")
    assert board.screen.seen(0)[0] == "system"
    board.call("clear", {"layer": "idle"}, 0)
    board.screen.word("IDLE")
    assert board.screen.seen(0)[0] == "face"


def test_a_scene_that_cannot_be_shown_says_why():
    board, _ = _device()
    cases = [
        ({"id": "x", "layout": {"widget": "rocket"}}, "невідомий віджет"),
        ({"id": "x", "layout": {"widget": "state"}}, "невідомий віджет"),
        ({"id": "x", "layout": {"rows": [{"widget": "text"}] * 9}}, "забагато віджетів"),
        ({"id": "x", "layout": {"rows": [{"h": 99, "widget": "text"}]}}, "вага"),
        ({"id": "x", "layout": {"widget": "picture", "bits": "!!"}}, "розміру"),
        ({"id": "x", "layout": {"widget": "icon", "bits": "AAAA"}}, "розміру"),
        ("not a scene", "сцена"),
    ]
    for scene, why in cases:
        ok, said = board.call("screen", {"scene": scene}, 0)
        assert ok is False and why in said, (scene, said)
    assert board.call("screen", {"scene": WEATHER, "layer": "top"}, 0)[0] is False
    assert board.screen.seen(0)[0] == "face"


def test_screen_info_tells_the_hands_what_there_is():
    board, _ = _device()
    board.call("screen", {"scene": WEATHER, "layer": "pin"}, 0)
    ok, info = board.call("screen_info", {}, 0)
    assert ok and info["size"] == [72, 40] and info["layers"]["pinned"] == "weather"
    assert "number" in info["widgets"] and "state" not in info["widgets"]
    assert "sun" in info["icons"]


def test_the_shapes_a_model_guesses_are_read_as_the_one_shape():
    import base64

    board, _ = _device()
    guessed = {"id": "w", "layout": {"cols": [
        {"w": 1, "icon": {"name": "sun"}},
        {"w": 2, "widget": {"type": "number", "value": 21, "unit": "°"}},
        {"type": "picture", "bits": base64.b64encode(bytes(360)).decode()}]}}
    assert board.call("screen", {"scene": guessed}, 0)[0] is True
    scene = board.screen.seen(0)[1]
    kinds = [w["widget"] for _b, w in scene.placed(72, 40)]
    assert kinds == ["icon", "number", "picture"]
    assert scene.placed(72, 40)[2][1]["bits"] == bytes(360)  # decoded once, kept


def test_a_shape_that_cannot_be_read_says_the_right_one():
    board, _ = _device()
    ok, said = board.call("screen", {"scene": {"id": "x", "layout": {"cols": [{"what": 1}]}}}, 0)
    assert ok is False and '{"widget": "icon", "name": "sun"}' in said
