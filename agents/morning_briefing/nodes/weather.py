"""Weather node — today's forecast via Open-Meteo (no API key required)."""

from __future__ import annotations

import httpx

import config
from agents.morning_briefing.state import BriefingState

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo WMO weather codes -> short human label + emoji.
_WMO = {
    0: "☀️ Clear", 1: "🌤️ Mostly clear", 2: "⛅ Partly cloudy", 3: "☁️ Overcast",
    45: "🌫️ Fog", 48: "🌫️ Rime fog",
    51: "🌦️ Light drizzle", 53: "🌦️ Drizzle", 55: "🌧️ Heavy drizzle",
    61: "🌦️ Light rain", 63: "🌧️ Rain", 65: "🌧️ Heavy rain",
    71: "🌨️ Light snow", 73: "🌨️ Snow", 75: "❄️ Heavy snow",
    80: "🌦️ Rain showers", 81: "🌧️ Rain showers", 82: "⛈️ Violent showers",
    95: "⛈️ Thunderstorm", 96: "⛈️ Thunderstorm w/ hail", 99: "⛈️ Severe storm",
}


def _describe(code: int) -> str:
    return _WMO.get(code, f"Code {code}")


def fetch_weather() -> str:
    """Return a one-paragraph weather summary string for the configured location."""
    params = {
        "latitude": config.WEATHER_LATITUDE,
        "longitude": config.WEATHER_LONGITUDE,
        "timezone": config.WEATHER_TIMEZONE,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max",
        "temperature_unit": config.WEATHER_TEMP_UNIT,
        "forecast_days": 1,
    }
    resp = httpx.get(_OPEN_METEO_URL, params=params, timeout=15)
    resp.raise_for_status()
    daily = resp.json()["daily"]

    unit = "°C" if config.WEATHER_TEMP_UNIT == "celsius" else "°F"
    code = daily["weather_code"][0]
    hi = round(daily["temperature_2m_max"][0])
    lo = round(daily["temperature_2m_min"][0])
    rain = daily["precipitation_probability_max"][0]
    return f"{_describe(code)} — High {hi}{unit} / Low {lo}{unit}, {rain}% chance of precip."


def weather_node(state: BriefingState) -> BriefingState:
    try:
        return {"weather": fetch_weather()}
    except Exception as exc:  # node failures shouldn't kill the whole briefing
        return {"weather": f"⚠️ Weather unavailable ({exc})"}


if __name__ == "__main__":
    print(fetch_weather())
