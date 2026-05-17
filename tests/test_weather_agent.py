"""Tests for the weather agent modules."""

from __future__ import annotations

from datetime import date

from weather_agent.cities import find_city
from weather_agent.forecast import DailyForecast
from weather_agent.polymarket import TempBucket, WeatherEvent, _parse_bucket, _parse_title
from weather_agent.strategy import _bucket_probability, analyze_event


class TestCities:
    def test_find_city_exact(self):
        city = find_city("London")
        assert city is not None
        assert city.name == "London"
        assert city.unit == "celsius"

    def test_find_city_case_insensitive(self):
        city = find_city("london")
        assert city is not None
        assert city.name == "London"

    def test_find_city_partial(self):
        city = find_city("Hong")
        assert city is not None
        assert city.name == "Hong Kong"

    def test_find_city_not_found(self):
        assert find_city("Atlantis") is None

    def test_fahrenheit_cities(self):
        for name in ["NYC", "Miami", "Seattle", "Los Angeles", "Atlanta"]:
            city = find_city(name)
            assert city is not None
            assert city.unit == "fahrenheit", f"{name} should use fahrenheit"


class TestParseTitle:
    def test_highest_temp(self):
        result = _parse_title("Highest temperature in London on May 14?", year=2026)
        assert result is not None
        measure, city, d = result
        assert measure == "highest"
        assert city == "London"
        assert d == date(2026, 5, 14)

    def test_lowest_temp(self):
        result = _parse_title("Lowest temperature in NYC on May 15?", year=2026)
        assert result is not None
        measure, city, d = result
        assert measure == "lowest"
        assert city == "NYC"
        assert d == date(2026, 5, 15)

    def test_multi_word_city(self):
        result = _parse_title("Highest temperature in Hong Kong on May 14?", year=2026)
        assert result is not None
        _, city, _ = result
        assert city == "Hong Kong"

    def test_invalid_title(self):
        assert _parse_title("Will Bitcoin go up?") is None


class TestParseBucket:
    def test_exact_celsius(self):
        result = _parse_bucket("Will the highest temperature in London be 12°C on May 14?")
        assert result is not None
        low, high, unit = result
        assert low == 12.0
        assert high == 12.0
        assert unit == "°C"

    def test_or_below(self):
        result = _parse_bucket("Will the highest temperature in London be 7°C or below on May 14?")
        assert result is not None
        low, high, unit = result
        assert low is None
        assert high == 7.0

    def test_or_higher(self):
        result = _parse_bucket("Will the highest temperature in London be 17°C or higher on May 14?")
        assert result is not None
        low, high, unit = result
        assert low == 17.0
        assert high is None

    def test_fahrenheit(self):
        result = _parse_bucket("Will the highest temperature in NYC be 76°F on May 14?")
        assert result is not None
        low, high, unit = result
        assert low == 76.0
        assert high == 76.0
        assert unit == "°F"


class TestBucketProbability:
    def test_center_bucket_high_prob(self):
        prob = _bucket_probability(12.0, 12.0, forecast_temp=12.0, sigma=2.0)
        assert prob > 0.15

    def test_far_bucket_low_prob(self):
        prob = _bucket_probability(20.0, 20.0, forecast_temp=12.0, sigma=2.0)
        assert prob < 0.01

    def test_below_bucket(self):
        prob = _bucket_probability(None, 7.0, forecast_temp=12.0, sigma=2.0)
        assert prob < 0.05

    def test_above_bucket(self):
        prob = _bucket_probability(17.0, None, forecast_temp=12.0, sigma=2.0)
        assert prob < 0.05

    def test_probabilities_roughly_sum_to_one(self):
        """When we cover the full range the probs should sum to ~1."""
        total = 0.0
        total += _bucket_probability(None, 7.0, 12.0, 2.0)
        for t in range(8, 17):
            total += _bucket_probability(float(t), float(t), 12.0, 2.0)
        total += _bucket_probability(17.0, None, 12.0, 2.0)
        assert 0.95 < total < 1.05


class TestAnalyzeEvent:
    def _make_event(self) -> WeatherEvent:
        buckets = [
            TempBucket("≤7°C", None, 7.0, 0.01, 0.99, "m1", False),
            TempBucket("8°C", 8.0, 8.0, 0.02, 0.98, "m2", False),
            TempBucket("9°C", 9.0, 9.0, 0.05, 0.95, "m3", False),
            TempBucket("10°C", 10.0, 10.0, 0.10, 0.90, "m4", False),
            TempBucket("11°C", 11.0, 11.0, 0.20, 0.80, "m5", False),
            TempBucket("12°C", 12.0, 12.0, 0.30, 0.70, "m6", False),
            TempBucket("13°C", 13.0, 13.0, 0.20, 0.80, "m7", False),
            TempBucket("14°C", 14.0, 14.0, 0.07, 0.93, "m8", False),
            TempBucket("15°C", 15.0, 15.0, 0.03, 0.97, "m9", False),
            TempBucket("≥16°C", 16.0, None, 0.02, 0.98, "m10", False),
        ]
        return WeatherEvent(
            event_id="1",
            title="Highest temperature in London on May 14?",
            city="London",
            event_date=date(2026, 5, 14),
            measure="highest",
            unit="°C",
            buckets=buckets,
        )

    def _make_forecast(self) -> DailyForecast:
        return DailyForecast(
            city_name="London",
            forecast_date=date(2026, 5, 14),
            temp_max_c=13.0,
            temp_min_c=7.0,
            temp_max_f=55.4,
            temp_min_f=44.6,
            precipitation_probability_max=20,
            weather_code=3,
        )

    def test_produces_signals(self):
        event = self._make_event()
        forecast = self._make_forecast()
        signals = analyze_event(event, forecast, sigma=2.0, min_edge=0.03)
        assert len(signals) > 0

    def test_signal_has_positive_edge(self):
        event = self._make_event()
        forecast = self._make_forecast()
        signals = analyze_event(event, forecast, sigma=2.0, min_edge=0.03)
        for sig in signals:
            assert sig.edge > 0

    def test_closed_buckets_skipped(self):
        event = self._make_event()
        event.buckets[0].closed = True
        forecast = self._make_forecast()
        signals = analyze_event(event, forecast, sigma=2.0, min_edge=0.0)
        market_ids = {s.market_id for s in signals}
        assert "m1" not in market_ids
