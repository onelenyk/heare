"""Weather from Open-Meteo, said in one English line for the model.

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
SKY = {0: "clear", 1: "clear", 2: "cloudy", 3: "cloudy"}
FOG = {45: "fog", 48: "fog"}
DRIZZLE = {51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "drizzle", 57: "drizzle"}
RAIN = {61: "rain", 63: "rain", 65: "downpour", 66: "rain", 67: "rain"}
SNOW = {71: "snow", 73: "snow", 75: "snow", 77: "snow", 85: "snow", 86: "snow"}
SHOWERS = {80: "downpour", 81: "downpour", 82: "downpour"}
STORM = {95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm"}
WORDS = {**SKY, **FOG, **DRIZZLE, **RAIN, **SNOW, **SHOWERS, **STORM}
# The words that already say it is falling: no need to add «no precipitation».
FALLING = {"drizzle", "rain", "downpour", "snow", "thunderstorm"}


def _degrees(value: float) -> str:
    """A whole degree with its sign: +3°, −1°, 0°."""
    whole = round(value)
    if whole > 0:
        return f"+{whole}°"
    if whole < 0:
        return f"−{-whole}°"
    return "0°"


def describe(current: dict, hourly: dict) -> str:
    """The weather now and the next hours, as one line of English text.

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
        parts.append(f"feels like {_degrees(feels)}")
    parts.append(f"wind {wind} m/s")
    if word in FALLING:
        parts.append(word)
    elif precipitation > 0:
        falling = f"precipitation {precipitation:g} mm"
        parts.append(f"{word}, {falling}" if word else falling)
    else:
        parts.append(f"{word}, no precipitation" if word else "no precipitation")
    now = ", ".join(parts)

    temps = [float(t) for t in (hourly.get("temperature_2m") or [])[:HOURS]]
    chances = [int(p) for p in (hourly.get("precipitation_probability") or [])[:HOURS]]
    if not temps:
        return now
    later = f"down to {_degrees(min(temps))} in the next {HOURS} h"
    if any(chance >= RAIN_PERCENT for chance in chances):
        later += ", rain likely"
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
    return f"weather ({place.get('name') or city})", text[:200]
