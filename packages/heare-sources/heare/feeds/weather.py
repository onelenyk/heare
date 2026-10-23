"""Weather from Open-Meteo, said in one Ukrainian line for the model.

Free and keyless. ``describe`` is pure so the words can be checked without
the network; ``fetch`` is the only part that talks to Open-Meteo.
"""

from __future__ import annotations

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT = (
    "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"
)
HOURLY = "temperature_2m,precipitation_probability"
HOURS = 12
# A feels-like figure this close to the thermometer is noise, not news.
FEELS_S = 2.0
RAIN_PERCENT = 50

# WMO weather codes, grouped into the words a person would use.
SKY = {0: "ясно", 1: "ясно", 2: "хмарно", 3: "хмарно"}
FOG = {45: "туман", 48: "туман"}
DRIZZLE = {51: "мряка", 53: "мряка", 55: "мряка", 56: "мряка", 57: "мряка"}
RAIN = {61: "дощ", 63: "дощ", 65: "злива", 66: "дощ", 67: "дощ"}
SNOW = {71: "сніг", 73: "сніг", 75: "сніг", 77: "сніг", 85: "сніг", 86: "сніг"}
SHOWERS = {80: "злива", 81: "злива", 82: "злива"}
STORM = {95: "гроза", 96: "гроза", 99: "гроза"}
WORDS = {**SKY, **FOG, **DRIZZLE, **RAIN, **SNOW, **SHOWERS, **STORM}
# The words that already say it is falling: no need to add «без опадів».
FALLING = {"мряка", "дощ", "злива", "сніг", "гроза"}


def _degrees(value: float) -> str:
    """A whole degree with its sign: +3°, −1°, 0°."""
    whole = round(value)
    if whole > 0:
        return f"+{whole}°"
    if whole < 0:
        return f"−{-whole}°"
    return "0°"


def describe(current: dict, hourly: dict) -> str:
    """The weather now and the next hours, as one line of Ukrainian text.

    *current* is Open-Meteo's ``current`` block, *hourly* its ``hourly``
    block; the hourly lists start at this hour.
    """
    temperature = float(current["temperature_2m"])
    feels = float(current["apparent_temperature"])
    precipitation = float(current.get("precipitation") or 0.0)
    wind = round(float(current["wind_speed_10m"]))
    word = WORDS.get(int(current.get("weather_code") or 0), "")

    parts = [_degrees(temperature)]
    if abs(feels - temperature) >= FEELS_S:
        parts.append(f"відчувається як {_degrees(feels)}")
    parts.append(f"вітер {wind} м/с")
    if word in FALLING:
        parts.append(word)
    elif precipitation > 0:
        falling = f"опади {precipitation:g} мм"
        parts.append(f"{word}, {falling}" if word else falling)
    else:
        parts.append(f"{word}, без опадів" if word else "без опадів")
    now = ", ".join(parts)

    temps = [float(t) for t in (hourly.get("temperature_2m") or [])[:HOURS]]
    chances = [int(p) for p in (hourly.get("precipitation_probability") or [])[:HOURS]]
    if not temps:
        return now
    later = f"до {_degrees(min(temps))} найближчі {HOURS} год"
    if any(chance >= RAIN_PERCENT for chance in chances):
        later += ", можливий дощ"
    return f"{now}; {later}"


async def fetch(client: httpx.AsyncClient, city: str) -> tuple[str, str] | None:
    """The label and the text for *city*, or None when no place has that name.

    Network and HTTP errors are raised: the caller logs them and tries again
    in the next round.
    """
    found = await client.get(
        GEOCODE_URL, params={"name": city, "count": 1, "language": "uk"}
    )
    found.raise_for_status()
    places = found.json().get("results") or []
    if not places:
        return None
    place = places[0]
    forecast = await client.get(
        FORECAST_URL,
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": CURRENT,
            "hourly": HOURLY,
            "forecast_hours": HOURS,
            "wind_speed_unit": "ms",
            "timezone": "auto",
        },
    )
    forecast.raise_for_status()
    body = forecast.json()
    text = describe(body["current"], body.get("hourly") or {})
    return f"погода ({place.get('name') or city})", text[:200]
