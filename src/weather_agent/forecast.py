"""Fetch real weather forecasts from Open-Meteo (free, no API key)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import aiohttp

from weather_agent.cities import City

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass
class DailyForecast:
    """Forecast for one day in one city."""

    city_name: str
    forecast_date: date
    temp_max_c: float
    temp_min_c: float
    temp_max_f: float
    temp_min_f: float
    precipitation_probability_max: int
    weather_code: int


def _c_to_f(c: float) -> float:
    return round(c * 9.0 / 5.0 + 32, 1)


async def fetch_forecast(city: City, days: int = 7) -> list[DailyForecast]:
    """Get up to *days* days of daily forecasts for *city* from Open-Meteo."""
    params = {
        "latitude": city.latitude,
        "longitude": city.longitude,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "timezone": city.timezone,
        "forecast_days": days,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(OPEN_METEO_URL, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

    daily = data.get("daily", {})
    times = daily.get("time", [])
    t_max = daily.get("temperature_2m_max", [])
    t_min = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_probability_max", [])
    codes = daily.get("weather_code", [])

    results: list[DailyForecast] = []
    for i, day_str in enumerate(times):
        d = date.fromisoformat(day_str)
        max_c = t_max[i] if i < len(t_max) else 0.0
        min_c = t_min[i] if i < len(t_min) else 0.0
        results.append(DailyForecast(
            city_name=city.name,
            forecast_date=d,
            temp_max_c=max_c,
            temp_min_c=min_c,
            temp_max_f=_c_to_f(max_c),
            temp_min_f=_c_to_f(min_c),
            precipitation_probability_max=precip[i] if i < len(precip) else 0,
            weather_code=codes[i] if i < len(codes) else 0,
        ))
    return results
