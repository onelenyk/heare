"""Which verb the real voice model reaches for — against the real prompt.

The scripted room (tests/e2e/test_the_verbs.py) proves the path once the
model has chosen. This proves the choice, which is where the screen went
wrong on 9 September: asked what was on the screen, the model answered
«не бачу» because nothing in its world was a screen. With the screen
moved back behind `delegate`, the risk is the same one, so it is asked
of the real model.

Nothing is carried out. The toolbox is replaced by a recorder that
answers like the real one would and runs nothing — no screenshot, no
canvas cleared, no worker started — so this costs one voice-model call
per case and changes nothing on the machine.

    uv run pytest -m spine_live tests/test_spine_verbs_live.py
"""

from __future__ import annotations

import pytest

from src import keys as _keys
from tests.test_spine_golden import _build, _copy_live_db, live_keys_required

pytestmark = [pytest.mark.spine_live, live_keys_required, pytest.mark.asyncio]

CASES = [
    ("Дока, що в мене зараз на екрані?", {"delegate"}),
    ("Дока, очисти канвас.", {"delegate"}),
    ("Дока, глянь, що зараз жере ресурси на компі.", {"delegate"}),
    ("Дока, відкрий мені браузер.", {"delegate"}),
    ("Дока, що я казав про rsync?", {"recall"}),
    ("Дока, нагадай мені за десять хвилин випити води.", {"remind"}),
    ("Дока, як справи?", set()),
]

# Read once, at import — before tests/conftest.py puts every test on the
# file key store, where a live run finds no key at all. Read-only, only in
# this opt-in module, handed to the process as environment for the length
# of one test, and never printed.
_LIVE_KEYS = {
    k: v
    for k in ("GROQ_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY")
    if (v := _keys.get(k))
}

_ANSWERS = {
    "delegate": "Прийнято, роблю.",
    "recall": "Про це в розмовах нічого не знайшлося.",
    "remind": "Добре, нагадаю за 10 хвилин.",
}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    return _copy_live_db(tmp_path_factory.mktemp("verbs_live") / "heare.db")


@pytest.fixture
async def voice(db, monkeypatch):
    for name, value in _LIVE_KEYS.items():
        monkeypatch.setenv(name, value)
    _settings, loop = await _build(db)
    called: list[str] = []

    async def record(name: str, arguments: dict) -> str:
        called.append(name)
        return _ANSWERS.get(name, "Готово.")

    loop.toolbox.execute = record
    try:
        yield loop, called
    finally:
        from src.spine.main import _close_loop

        await _close_loop(loop)


@pytest.mark.parametrize(("said", "expected"), CASES, ids=[c[0] for c in CASES])
async def test_the_model_reaches_for_the_right_verb(voice, said, expected) -> None:
    loop, called = voice

    reply = await loop.respond(said, speak=False)

    assert set(called) == expected, f"called {called}, said {reply!r}"
    assert "не бачу" not in reply.lower(), reply
