"""What a hand puts in the chat: a card, a picture, a link — and refusals."""

from __future__ import annotations

import base64
from types import SimpleNamespace

from heare import native_hands
from heare.native_hands import NativeHand


class Desk:
    stopped = False
    job_id = 7

    def step(self, text: str) -> None:
        pass

    async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
        return True

    async def ask(self, text: str) -> str:
        return ""


class Shown:
    """The brain's answer, as the part sees it."""

    def __init__(self, ok: bool = True) -> None:
        self.bodies: list[tuple[int, dict]] = []
        self.ok = ok

    async def __call__(self, job: int, body: dict) -> dict:
        self.bodies.append((job, body))
        if not self.ok:
            raise RuntimeError("the page says no")
        return {"shown": f"m/{len(self.bodies)}"}


def hand(show=None) -> NativeHand:
    return NativeHand(None, None, SimpleNamespace(executor_mode="auto"), show=show)  # type: ignore[arg-type]


async def test_an_html_card_is_shown():
    shown = Shown()
    said = await hand(shown)._show({"kind": "html", "html": "<h3>Кіт</h3>"}, Desk())
    assert said.startswith("Shown:")
    job, body = shown.bodies[0]
    assert job == 7 and body["html"] == "<h3>Кіт</h3>"


async def test_a_page_on_this_machine_is_read_as_html(tmp_path):
    page = tmp_path / "cat.html"
    page.write_text("<h3>Кіт</h3>", encoding="utf-8")
    shown = Shown()
    await hand(shown)._show({"kind": "html", "file": str(page)}, Desk())
    assert shown.bodies[0][1]["html"] == "<h3>Кіт</h3>"


async def test_a_picture_is_sent_as_bytes(tmp_path):
    picture = tmp_path / "cat.png"
    picture.write_bytes(b"\x89PNG" + b"x" * 8)
    shown = Shown()
    await hand(shown)._show({"kind": "image", "file": str(picture)}, Desk())
    _job, body = shown.bodies[0]
    assert body["name"] == "cat.png"
    assert base64.b64decode(body["data"]) == picture.read_bytes()


async def test_a_file_that_is_not_there_is_said_so():
    said = await hand(Shown())._show({"kind": "image", "file": "/no/such.png"}, Desk())
    assert said.startswith("Failed: no such file")


async def test_too_large_a_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(native_hands, "SHOW_BYTES", 4)
    picture = tmp_path / "cat.png"
    picture.write_bytes(b"x" * 16)
    said = await hand(Shown())._show({"kind": "image", "file": str(picture)}, Desk())
    assert said.startswith("Failed:") and "larger" in said


async def test_a_link_goes_as_it_is():
    shown = Shown()
    await hand(shown)._show({"kind": "link", "url": "https://example.com"}, Desk())
    assert shown.bodies[0][1]["url"] == "https://example.com"


async def test_no_kind_and_no_page_are_refused():
    assert (await hand(Shown())._show({"kind": "smell"}, Desk())).startswith(
        "Failed: no such kind")
    assert (await hand(Shown())._show({"kind": "html"}, Desk())).startswith(
        "Failed: no html")


async def test_hands_with_nowhere_to_show_say_so():
    said = await hand()._show({"kind": "html", "html": "<p>x</p>"}, Desk())
    assert "cannot show" in said


async def test_the_page_s_no_is_the_hand_s_no():
    said = await hand(Shown(ok=False))._show({"kind": "html", "html": "<p>x</p>"}, Desk())
    assert said.startswith("Failed: the page says no")
