"""Main agent orchestrator: scan Polymarket → fetch forecasts → emit signals."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from weather_agent.cities import find_city
from weather_agent.forecast import DailyForecast, fetch_forecast
from weather_agent.polymarket import fetch_temperature_events
from weather_agent.strategy import BetSignal, analyze_event


@dataclass
class AgentResult:
    """Full result of one agent scan."""

    events_scanned: int = 0
    events_with_forecast: int = 0
    signals: list[BetSignal] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def run_scan(
    *,
    sigma: float = 2.0,
    min_edge: float = 0.05,
    limit: int = 200,
) -> AgentResult:
    """Run a full scan: fetch markets, fetch forecasts, produce signals."""
    result = AgentResult()

    events = await fetch_temperature_events(limit=limit)
    result.events_scanned = len(events)

    city_forecasts: dict[str, list[DailyForecast]] = {}

    async def _get_forecast(city_name: str) -> list[DailyForecast] | None:
        if city_name in city_forecasts:
            return city_forecasts[city_name]
        city = find_city(city_name)
        if city is None:
            return None
        try:
            forecasts = await fetch_forecast(city, days=16)
            city_forecasts[city_name] = forecasts
            return forecasts
        except Exception as exc:
            result.errors.append(f"Forecast error for {city_name}: {exc}")
            return None

    unique_cities = list({e.city for e in events})
    sem = asyncio.Semaphore(5)

    async def _rate_limited(city_name: str) -> list[DailyForecast] | None:
        async with sem:
            res = await _get_forecast(city_name)
            await asyncio.sleep(0.2)
            return res

    await asyncio.gather(*[_rate_limited(c) for c in unique_cities])

    for event in events:
        forecasts = city_forecasts.get(event.city)
        if not forecasts:
            continue

        day_forecast = next(
            (f for f in forecasts if f.forecast_date == event.event_date),
            None,
        )
        if day_forecast is None:
            continue

        result.events_with_forecast += 1
        signals = analyze_event(event, day_forecast, sigma=sigma, min_edge=min_edge)
        result.signals.extend(signals)

    result.signals.sort(key=lambda s: s.expected_value, reverse=True)
    return result
