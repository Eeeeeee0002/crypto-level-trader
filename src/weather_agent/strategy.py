"""Compare Polymarket odds with real weather forecasts and produce betting signals."""

from __future__ import annotations

import math
from dataclasses import dataclass

from weather_agent.forecast import DailyForecast
from weather_agent.polymarket import WeatherEvent


@dataclass
class BetSignal:
    """A recommended bet on a Polymarket weather market."""

    event_title: str
    city: str
    event_date: str
    bucket_question: str
    market_id: str
    side: str  # "YES" or "NO"
    market_price: float
    estimated_probability: float
    edge: float  # estimated_prob - market_price (for YES) or (1-estimated_prob) - market_price (for NO)
    expected_value: float  # edge / market_price
    confidence: str  # "high", "medium", "low"
    forecast_temp: float
    bucket_temp_low: float | None
    bucket_temp_high: float | None


def _normal_pdf(x: float, mu: float, sigma: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2) / (sigma * math.sqrt(2 * math.pi))


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    """Standard normal CDF using the error function."""
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def _bucket_probability(
    temp_low: float | None,
    temp_high: float | None,
    forecast_temp: float,
    sigma: float = 2.0,
) -> float:
    """Estimate probability that the actual temperature falls in [temp_low, temp_high].

    Uses a normal distribution centered on *forecast_temp* with std-dev *sigma*
    (default 2°C / ~3.6°F to account for forecast uncertainty).

    For boundary buckets ("X or below" / "X or above"), we integrate to ±inf.
    For exact-degree buckets we integrate over a 1-unit window centered on the value.
    """
    if temp_low is None and temp_high is not None:
        return _normal_cdf(temp_high + 0.5, forecast_temp, sigma)
    if temp_high is None and temp_low is not None:
        return 1.0 - _normal_cdf(temp_low - 0.5, forecast_temp, sigma)
    if temp_low is not None and temp_high is not None:
        if temp_low == temp_high:
            return _normal_cdf(temp_high + 0.5, forecast_temp, sigma) - _normal_cdf(
                temp_low - 0.5, forecast_temp, sigma
            )
        return _normal_cdf(temp_high + 0.5, forecast_temp, sigma) - _normal_cdf(
            temp_low - 0.5, forecast_temp, sigma
        )
    return 0.0


def _c_to_f(c: float) -> float:
    return round(c * 9.0 / 5.0 + 32, 1)


def analyze_event(
    event: WeatherEvent,
    forecast: DailyForecast,
    sigma: float = 2.0,
    min_edge: float = 0.05,
) -> list[BetSignal]:
    """Analyze one weather event against a forecast, return actionable signals.

    Parameters
    ----------
    sigma : float
        Forecast uncertainty in the same unit as the market (°C or °F).
        Default 2.0 works well for Celsius; for Fahrenheit markets pass ~3.5.
    min_edge : float
        Minimum edge (estimated_prob - market_price) to emit a signal.
    """
    if event.unit == "°F":
        if event.measure == "highest":
            forecast_temp = forecast.temp_max_f
        else:
            forecast_temp = forecast.temp_min_f
        effective_sigma = sigma * 1.8
    else:
        if event.measure == "highest":
            forecast_temp = forecast.temp_max_c
        else:
            forecast_temp = forecast.temp_min_c
        effective_sigma = sigma

    signals: list[BetSignal] = []

    for bucket in event.buckets:
        if bucket.closed:
            continue

        est_prob = _bucket_probability(bucket.temp_low, bucket.temp_high, forecast_temp, effective_sigma)

        yes_edge = est_prob - bucket.yes_price
        no_edge = (1 - est_prob) - bucket.no_price

        if yes_edge >= min_edge and bucket.yes_price > 0.01:
            ev = yes_edge / bucket.yes_price
            confidence = "high" if yes_edge > 0.15 else ("medium" if yes_edge > 0.08 else "low")
            signals.append(BetSignal(
                event_title=event.title,
                city=event.city,
                event_date=str(event.event_date),
                bucket_question=bucket.question,
                market_id=bucket.market_id,
                side="YES",
                market_price=bucket.yes_price,
                estimated_probability=round(est_prob, 4),
                edge=round(yes_edge, 4),
                expected_value=round(ev, 4),
                confidence=confidence,
                forecast_temp=forecast_temp,
                bucket_temp_low=bucket.temp_low,
                bucket_temp_high=bucket.temp_high,
            ))

        if no_edge >= min_edge and bucket.no_price > 0.01:
            ev = no_edge / bucket.no_price
            confidence = "high" if no_edge > 0.15 else ("medium" if no_edge > 0.08 else "low")
            signals.append(BetSignal(
                event_title=event.title,
                city=event.city,
                event_date=str(event.event_date),
                bucket_question=bucket.question,
                market_id=bucket.market_id,
                side="NO",
                market_price=bucket.no_price,
                estimated_probability=round(1 - est_prob, 4),
                edge=round(no_edge, 4),
                expected_value=round(ev, 4),
                confidence=confidence,
                forecast_temp=forecast_temp,
                bucket_temp_low=bucket.temp_low,
                bucket_temp_high=bucket.temp_high,
            ))

    signals.sort(key=lambda s: s.expected_value, reverse=True)
    return signals
