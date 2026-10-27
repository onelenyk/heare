"""The sources part: what is true now, said in words and posted as a note.

No network: Open-Meteo and the brain are both ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from heare import machine, part
from heare.feeds import weather
from heare.sources import Sources, load

HOURLY_TEMPERATURES = [2, 1, 0, -2, -1, 0, 1, 2, 3, 4, 5, 6]


def _current(**fields) -> dict:
    current = {
        "temperature_2m": 3.0,
        "apparent_temperature": -1.0,
        "precipitation": 0.0,
        "weather_code": 3,
        "wind_speed_10m": 8.0,
    }
    current.update(fields)
    return current


def _hourly(temperatures=HOURLY_TEMPERATURES, chances=None) -> dict:
    return {
        "temperature_2m": temperatures,
        "precipitation_probability": chances or [10] * 12,
    }


def _open_meteo(request: httpx.Request) -> httpx.Response:
    if request.url.host == "geocoding-api.open-meteo.com":
        if request.url.params["name"] == "Нікуди":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={"results": [{"name": "Київ", "latitude": 50.45, "longitude": 30.52}]},
        )
    return httpx.Response(200, json={"current": _current(), "hourly": _hourly()})


def _brain(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


def _notes(seen: list[httpx.Request]) -> list[dict]:
    notes = []
    for request in seen:
        body = json.loads(request.content)
        if body.get("kind") == "note":
            notes.append(body)
    return notes


# describe: the words for what the weather is now -------------------------


def test_a_cold_windy_evening_is_said_in_one_line():
    text = weather.describe(_current(), _hourly())

    assert text == (
        "+3°, feels like −1°, wind 8 m/s, cloudy, no precipitation; "
        "down to −2° in the next 12 h"
    )


def test_the_feels_like_figure_is_shown_only_when_it_differs_by_two():
    close = weather.describe(_current(apparent_temperature=4.0), _hourly())
    far = weather.describe(_current(apparent_temperature=1.0), _hourly())

    assert "feels like" not in close
    assert "feels like +1°" in far


def test_a_likely_rain_in_the_next_hours_is_said():
    rainy = weather.describe(_current(), _hourly(chances=[10, 60] + [10] * 10))
    dry = weather.describe(_current(), _hourly(chances=[40] * 12))

    assert rainy.endswith("down to −2° in the next 12 h, rain likely")
    assert "rain likely" not in dry


def test_falling_precipitation_names_itself_and_no_dry_sky():
    text = weather.describe(_current(weather_code=63, precipitation=1.2), _hourly())

    assert text.startswith("+3°, feels like −1°, wind 8 m/s, rain; ")
    assert "no precipitation" not in text


@pytest.mark.parametrize(
    ("code", "word"),
    [
        (0, "clear"),
        (1, "clear"),
        (2, "cloudy"),
        (45, "fog"),
        (48, "fog"),
        (53, "drizzle"),
        (57, "drizzle"),
        (63, "rain"),
        (67, "rain"),
        (75, "snow"),
        (82, "downpour"),
        (95, "thunderstorm"),
        (99, "thunderstorm"),
    ],
)
def test_wmo_codes_are_grouped_into_the_words_a_person_uses(code, word):
    text = weather.describe(_current(weather_code=code), _hourly())

    assert word in text


def test_an_unknown_code_says_nothing_about_the_sky():
    text = weather.describe(_current(weather_code=999), _hourly())

    assert text.startswith("+3°, feels like −1°, wind 8 m/s, no precipitation; ")


def test_without_an_hourly_forecast_only_the_present_is_said():
    text = weather.describe(_current(), {})

    assert text == "+3°, feels like −1°, wind 8 m/s, cloudy, no precipitation"


# fetch: Open-Meteo, mocked --------------------------------------------------


async def test_fetch_gives_the_label_and_the_text_for_the_city():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_open_meteo)) as client:
        found = await weather.fetch(client, "Київ")

    assert found is not None
    label, text = found
    assert label == "weather (Київ)"
    assert text.startswith("+3°, feels like −1°")
    assert text.endswith("down to −2° in the next 12 h")


async def test_an_unknown_city_is_none_not_an_error():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_open_meteo)) as client:
        assert await weather.fetch(client, "Нікуди") is None


async def test_a_network_error_is_raised_for_the_caller_to_log():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
        with pytest.raises(httpx.ConnectError):
            await weather.fetch(client, "Київ")


# the part: the note it posts and what it keeps ------------------------------


async def test_the_weather_note_is_posted_with_its_expiry(tmp_path):
    brain_seen: list[httpx.Request] = []
    web = httpx.AsyncClient(transport=httpx.MockTransport(_open_meteo))
    async with httpx.AsyncClient(transport=_brain(brain_seen)) as client:
        wire = part.Wire(client, "http://127.0.0.1:9790", "sources", "pid", "secret")
        sources = Sources(web, tmp_path / "sources.json")
        sources.wire = wire
        before = time.time()
        await sources.look()
        await wire.settle()
    await web.aclose()

    (note,) = _notes(brain_seen)
    assert note["channel"] == "sources"
    assert note["kind"] == "note"
    assert note["key"] == "weather"
    assert note["value"]["label"] == "weather (Київ)"
    assert note["value"]["text"].startswith("+3°")
    assert before + 90 * 60 - 1 <= note["value"]["until"] <= time.time() + 90 * 60 + 1


async def test_nothing_is_posted_while_the_weather_is_off(tmp_path):
    brain_seen: list[httpx.Request] = []
    web = httpx.AsyncClient(transport=httpx.MockTransport(_open_meteo))
    async with httpx.AsyncClient(transport=_brain(brain_seen)) as client:
        wire = part.Wire(client, "http://127.0.0.1:9790", "sources", "pid", "secret")
        sources = Sources(web, tmp_path / "sources.json")
        sources.wire = wire
        sources.event({"kind": "control", "name": "weather", "value": False})
        await sources.look()
        await wire.settle()
    await web.aclose()

    assert _notes(brain_seen) == []
    assert sources.sees == "off"


async def test_a_failed_fetch_is_shown_and_does_not_end_the_part(tmp_path):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    brain_seen: list[httpx.Request] = []
    web = httpx.AsyncClient(transport=httpx.MockTransport(down))
    async with httpx.AsyncClient(transport=_brain(brain_seen)) as client:
        wire = part.Wire(client, "http://127.0.0.1:9790", "sources", "pid", "secret")
        sources = Sources(web, tmp_path / "sources.json")
        sources.wire = wire
        await sources.look()
        await wire.settle()
    await web.aclose()

    assert _notes(brain_seen) == []
    assert sources.sees == "no connection to the weather, will try again"


async def test_an_unknown_city_is_shown_on_the_card(tmp_path):
    brain_seen: list[httpx.Request] = []
    web = httpx.AsyncClient(transport=httpx.MockTransport(_open_meteo))
    async with httpx.AsyncClient(transport=_brain(brain_seen)) as client:
        wire = part.Wire(client, "http://127.0.0.1:9790", "sources", "pid", "secret")
        sources = Sources(web, tmp_path / "sources.json")
        sources.wire = wire
        sources.event({"kind": "control", "name": "city", "value": "Нікуди"})
        await sources.look()
        await wire.settle()
    await web.aclose()

    assert _notes(brain_seen) == []
    assert sources.sees == "city not found"


def test_the_city_and_the_toggle_survive_a_restart(tmp_path):
    path = tmp_path / "sources.json"
    web = httpx.AsyncClient(transport=httpx.MockTransport(_open_meteo))
    first = Sources(web, path)
    first.event({"kind": "control", "name": "city", "value": "Львів"})
    first.event({"kind": "control", "name": "weather", "value": False})

    again = Sources(web, path)

    assert again.city == "Львів"
    assert again.weather is False


def test_a_missing_or_broken_settings_file_gives_the_defaults(tmp_path):
    broken = tmp_path / "sources.json"
    broken.write_text("{not json", encoding="utf-8")

    assert load(tmp_path / "missing.json") == {"weather": True, "city": "Київ"}
    assert load(broken) == {"weather": True, "city": "Київ"}


def test_the_keeper_knows_the_sources_part():
    assert "sources" in machine.PARTS
    assert machine.PARTS["sources"].channel == "sources"
    assert machine.PARTS["sources"].forms == ("input",)
