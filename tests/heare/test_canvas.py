"""The canvas: drawn on by the model that talks, changed block by block."""

from __future__ import annotations

from heare.api.canvas import Canvas, clean
from tests.heare.fakes import Says, open_room


def _canvas(tmp_path, events=None):
    return Canvas(tmp_path / "canvas.json",
                  lambda kind, **data: (events if events is not None else []).append((kind, data)))


def test_a_block_is_added_then_replaced_in_place(tmp_path):
    events: list = []
    canvas = _canvas(tmp_path, events)
    canvas.put("title", "<h2>План</h2>")
    canvas.put("steps", "<ol><li>раз</li></ol>")
    canvas.put("title", "<h2>Новий план</h2>")
    assert list(canvas.blocks) == ["title", "steps"]
    assert canvas.blocks["title"] == "<h2>Новий план</h2>"
    assert [e[1]["id"] for e in events] == ["title", "steps", "title"]


def test_a_block_can_be_put_after_another_and_removed(tmp_path):
    canvas = _canvas(tmp_path)
    canvas.put("a", "<p>a</p>")
    canvas.put("c", "<p>c</p>")
    canvas.put("b", "<p>b</p>", after="a")
    assert list(canvas.blocks) == ["a", "b", "c"]
    assert canvas.remove("b") and not canvas.remove("b")
    canvas.clear()
    assert canvas.blocks == {}


def test_the_drawing_survives_a_restart(tmp_path):
    _canvas(tmp_path).put("note", "<p>пам'ятай</p>")
    assert _canvas(tmp_path).blocks == {"note": "<p>пам'ятай</p>"}


def test_what_could_reach_outside_the_frame_is_taken_out():
    dirty = ('<p onclick="steal()">привіт</p><a href="javascript:steal()">x</a>'
             '<iframe src="//evil"></iframe><link rel="stylesheet" href="//evil.css">'
             '<script>setInterval(tick, 1000)</script>')
    cleaned = clean(dirty)
    for bad in ("onclick", "javascript:", "iframe", "<link"):
        assert bad not in cleaned
    # A script stays: the page only ever runs it inside a sealed frame.
    assert "привіт" in cleaned and "setInterval" in cleaned


def test_widgets_stay_side_by_side_each_with_a_size(tmp_path):
    canvas = _canvas(tmp_path)
    canvas.put("weather", "<h3>Бурштин</h3><p class='big' id='t'>…</p><script>load()</script>")
    canvas.put("cpu", "<h3>Процесор</h3><div class='meter'></div>", size="m")
    canvas.put("cities", "<table><tr><td>Київ</td></tr></table>")
    assert [(b["id"], b["size"]) for b in canvas.view()["blocks"]] == [
        ("weather", "s"), ("cpu", "m"), ("cities", "m"),
    ]
    told = canvas.context()
    assert "weather [s] (живий): Бурштин" in told and "load()" not in told
    assert _canvas(tmp_path).sizes == {"weather": "s", "cpu": "m", "cities": "m"}


def test_the_model_is_told_what_is_drawn_as_an_outline_not_as_markup(tmp_path):
    canvas = _canvas(tmp_path)
    assert "порожній" in canvas.context()
    canvas.put("prices", "<table><tr><th>Модель</th></tr><tr><td>deepseek</td></tr></table>")
    told = canvas.context()
    assert "prices [m]: Модель deepseek" in told and "<table" not in told
    canvas.put("long", "<p>" + "слово " * 400 + "</p>")
    assert len(canvas.context()) < 1200


async def test_the_talking_model_draws_in_the_same_turn_and_says_only_its_words(tmp_path):
    room = await open_room()
    canvas = _canvas(tmp_path)
    room.core.add_output(canvas)
    room.model.will_say(Says("Ось три кроки.", calls=(
        ("canvas_put", {"id": "steps", "html": "<ol><li>раз</li><li>два</li></ol>"}),
    )))
    room.says("Як зварити каву?")
    await room.settled()
    assert {"canvas_put", "canvas_style", "canvas_arrange", "canvas_remove", "canvas_clear"} <= set(room.model.offered[0])
    assert canvas.blocks["steps"].startswith("<ol>")
    assert room.page.said == ["Ось три кроки. "]  # drawing adds nothing to the speech
    room.model.will_say("Гаразд.")
    room.says("Дякую")
    await room.settled()
    assert "steps [s]: раз два" in room.model.asked[1][0]["content"]
    await room.close()


async def test_changing_the_drawing_touches_one_block(tmp_path):
    room = await open_room()
    events: list = []
    canvas = _canvas(tmp_path, events)
    room.core.add_output(canvas)
    canvas.put("title", "<h2>Кава</h2>")
    canvas.put("steps", "<ol><li>раз</li></ol>")
    events.clear()
    room.model.will_say(Says("Додав.", calls=(
        ("canvas_put", {"id": "steps", "html": "<ol><li>раз</li><li>два</li></ol>"}),
    )))
    room.says("Додай другий крок")
    await room.settled()
    assert [(e[1]["op"], e[1]["id"]) for e in events] == [("put", "steps")]
    assert canvas.blocks["title"] == "<h2>Кава</h2>"
    await room.close()


async def test_drawing_without_a_word_still_ends_in_words(tmp_path):
    room = await open_room()
    canvas = _canvas(tmp_path)
    room.core.add_output(canvas)
    room.model.will_say(Says(calls=(
        ("canvas_put", {"id": "a", "html": "<p>a</p>", "say": "Ось коротко."}),
        ("canvas_put", {"id": "b", "html": "<p>b</p>"}),
    )))
    room.says("Покажи щось")
    await room.settled()
    assert list(canvas.blocks) == ["a", "b"]
    # The sentence the model gave with the first drawing, once.
    assert room.page.said == ["Ось коротко."]
    assert room.memory.kept[-1][:2] == ("assistant", "Ось коротко.")
    await room.close()


async def test_saying_it_is_drawn_without_drawing_is_asked_for_again(tmp_path):
    room = await open_room()
    canvas = _canvas(tmp_path)
    room.core.add_output(canvas)
    canvas.put("weather", "<h3>Погода</h3>")
    room.model.will_say("Оновив погоду — тепер там і вологість.", Says(calls=(
        ("canvas_put", {"id": "weather", "html": "<h3>Погода</h3><p>вологість 70%</p>",
                        "say": "Оновив."}),
    )))
    room.says("Додай у погоду вологість")
    await room.settled()
    assert "вологість 70%" in canvas.blocks["weather"]
    assert room.page.said == ["Оновив погоду — тепер там і вологість. "]  # said once
    await room.close()


async def test_a_claim_that_stays_empty_is_never_sent_to_an_executor(tmp_path):
    from tests.heare.fakes import Worker

    room = await open_room()
    worker = Worker()
    room.core.add_executor(worker)
    room.core.add_output(_canvas(tmp_path))
    room.model.will_say("Готово, додав.", "Нічого не викликаю.")
    room.says("Додай рядок у таблицю")
    await room.settled()
    assert worker.tasks == []
    await room.close()


def test_widgets_are_moved_and_resized_without_being_redrawn(tmp_path):
    events: list = []
    canvas = _canvas(tmp_path, events)
    for name in ("clock", "weather", "plan", "cpu"):
        canvas.put(name, f"<h3>{name}</h3>")
    events.clear()
    canvas.arrange(["plan", "nope", "cpu"], {"plan": "l", "cpu": "m", "weather": "xxl"})
    assert list(canvas.blocks) == ["plan", "cpu", "clock", "weather"]
    assert canvas.sizes["plan"] == "l" and canvas.sizes["cpu"] == "m"
    assert canvas.sizes["weather"] == "s"
    assert [e[1]["op"] for e in events] == ["arrange"]
    assert canvas.blocks["plan"] == "<h3>plan</h3>"
    assert "- plan [l]" in canvas.context()
    assert list(_canvas(tmp_path).blocks) == ["plan", "cpu", "clock", "weather"]


def test_the_look_of_the_canvas_is_its_own_stylesheet(tmp_path):
    events: list = []
    canvas = _canvas(tmp_path, events)
    canvas.put("clock", "<h3>Час</h3>")
    canvas.style("@import url(//evil.css); #live { grid-template-columns: 1fr 2fr } "
                 "#blk-clock { grid-column: 1 / -1 } #stage { --accent: #e0a030 }")
    assert "@import" not in canvas.css and "grid-template-columns: 1fr 2fr" in canvas.css
    assert events[-1][1] == {"op": "style", "css": canvas.css}
    assert "--accent: #e0a030" in canvas.context()
    again = _canvas(tmp_path)
    assert again.css == canvas.css and list(again.blocks) == ["clock"]
    again.clear()
    assert again.css == ""
