"""Fetch and parse Polymarket weather / daily-temperature markets."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import aiohttp

GAMMA_BASE = "https://gamma-api.polymarket.com"


@dataclass
class TempBucket:
    """One temperature bucket inside a Polymarket temperature event."""

    question: str
    temp_low: float | None  # None means "≤ X" for lowest bucket
    temp_high: float | None  # None means "≥ X" for highest bucket
    yes_price: float
    no_price: float
    market_id: str
    closed: bool


@dataclass
class WeatherEvent:
    """A single Polymarket temperature event (one city + one date)."""

    event_id: str
    title: str
    city: str
    event_date: date
    measure: str  # "highest" or "lowest"
    unit: str  # "°C" or "°F"
    buckets: list[TempBucket] = field(default_factory=list)


_TITLE_RE = re.compile(
    r"(Highest|Lowest)\s+temperature\s+in\s+(.+?)\s+on\s+(\w+)\s+(\d+)\??",
    re.IGNORECASE,
)

_MONTH_MAP = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
    "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

_BUCKET_RE = re.compile(
    r"(?:Will the (?:highest|lowest) temperature in .+? be )"
    r"(\d+(?:\.\d+)?)(°[CF])(?:\s+or\s+(below|above|higher|lower))?"
    r"\s+on\s+",
    re.IGNORECASE,
)


def _parse_title(title: str, year: int | None = None) -> tuple[str, str, date] | None:
    m = _TITLE_RE.search(title)
    if not m:
        return None
    measure = m.group(1).lower()
    city = m.group(2).strip()
    month_str = m.group(3)
    day = int(m.group(4))
    month = _MONTH_MAP.get(month_str)
    if month is None:
        return None
    if year is None:
        from datetime import datetime
        year = datetime.utcnow().year
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    return measure, city, d


def _parse_bucket(question: str) -> tuple[float | None, float | None, str] | None:
    """Parse the temperature value and boundary from a market question.

    Returns (temp_low, temp_high, unit) or None.
    """
    m = _BUCKET_RE.search(question)
    if not m:
        return None
    temp = float(m.group(1))
    unit = m.group(2)
    modifier = (m.group(3) or "").lower()
    if modifier in ("below", "lower"):
        return None, temp, unit
    if modifier in ("above", "higher"):
        return temp, None, unit
    return temp, temp, unit


async def fetch_temperature_events(
    *,
    tag_slug: str = "daily-temperature",
    closed: bool = False,
    limit: int = 100,
) -> list[WeatherEvent]:
    """Fetch active temperature events from Polymarket Gamma API."""
    url = f"{GAMMA_BASE}/events"
    params = {
        "tag_slug": tag_slug,
        "closed": str(closed).lower(),
        "limit": limit,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            raw_events: list[dict] = await resp.json()

    events: list[WeatherEvent] = []
    for raw in raw_events:
        title = raw.get("title", "")
        parsed = _parse_title(title)
        if parsed is None:
            continue
        measure, city, event_date = parsed

        buckets: list[TempBucket] = []
        unit = "°C"
        for mkt in raw.get("markets", []):
            question = mkt.get("question", "")
            parsed_bucket = _parse_bucket(question)
            if parsed_bucket is None:
                continue
            temp_low, temp_high, u = parsed_bucket
            unit = u

            prices_raw = mkt.get("outcomePrices", '["0.5","0.5"]')
            if isinstance(prices_raw, str):
                import json
                prices = json.loads(prices_raw)
            else:
                prices = prices_raw
            yes_price = float(prices[0]) if len(prices) > 0 else 0.5
            no_price = float(prices[1]) if len(prices) > 1 else 0.5

            buckets.append(TempBucket(
                question=question,
                temp_low=temp_low,
                temp_high=temp_high,
                yes_price=yes_price,
                no_price=no_price,
                market_id=str(mkt.get("id", "")),
                closed=bool(mkt.get("closed", False)),
            ))

        buckets.sort(key=lambda b: b.temp_high if b.temp_high is not None else (b.temp_low or -999))

        events.append(WeatherEvent(
            event_id=str(raw["id"]),
            title=title,
            city=city,
            event_date=event_date,
            measure=measure,
            unit=unit,
            buckets=buckets,
        ))

    return events
