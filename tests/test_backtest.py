"""Determinism / behaviour tests for the backtester."""

from __future__ import annotations

import math

import numpy as np

from level_trader.backtest import backtest_symbol
from level_trader.config import (
    BrokerConfig,
    Config,
    LevelsConfig,
    RiskConfig,
    SignalsConfig,
    TimeframesConfig,
)


def _bar(ts_ms: int, o: float, h: float, low: float, c: float, v: float = 1000.0) -> list[float]:
    return [ts_ms, o, h, low, c, v]


def _build_range_market(bars: int = 400, support: float = 100.0, resistance: float = 110.0):
    """Synthetic range-bound market: price oscillates between `support` and `resistance`.

    Produces clean pivots at both levels so the detector has something to work with.
    """
    rows: list[list[float]] = []
    ts = 0
    step = 3_600_000  # 1h
    rng = np.random.default_rng(42)
    up = True
    level_hi, level_lo = resistance, support
    price = support
    for _ in range(bars):
        if up:
            target = level_hi + rng.uniform(-0.2, 0.2)
            o = price
            c = target
            h = max(o, c) + rng.uniform(0.1, 0.5)
            low = min(o, c) - rng.uniform(0.0, 0.2)
        else:
            target = level_lo + rng.uniform(-0.2, 0.2)
            o = price
            c = target
            low = min(o, c) - rng.uniform(0.1, 0.5)
            h = max(o, c) + rng.uniform(0.0, 0.2)
        rows.append(_bar(ts, float(o), float(h), float(low), float(c)))
        price = c
        ts += step
        up = not up
    return rows


def _config_for_tests() -> Config:
    return Config(
        timeframes=TimeframesConfig(execution="1h", context="1h", lookback_bars=300),
        levels=LevelsConfig(pivot_lookback=2, cluster_pct=0.02, min_touches=2, max_age_bars=400),
        signals=SignalsConfig(
            approach_pct=0.02,
            require_confirmation=False,
            use_trend_filter=False,
            min_rr=0.5,
            cooldown_bars=0,
        ),
        risk=RiskConfig(account_equity=10_000, risk_per_trade=0.01, leverage=3),
        broker=BrokerConfig(taker_fee=0.0, slippage_pct=0.0),
    )


def test_backtest_is_deterministic():
    rows = _build_range_market()
    cfg = _config_for_tests()
    r1 = backtest_symbol("TEST", rows, rows, cfg, warmup_bars=100)
    r2 = backtest_symbol("TEST", rows, rows, cfg, warmup_bars=100)
    assert r1.trades == r2.trades
    assert math.isclose(r1.total_pnl, r2.total_pnl)
    assert math.isclose(r1.final_equity, r2.final_equity)
    assert r1.equity_curve == r2.equity_curve


def test_backtest_produces_trades_and_curve():
    rows = _build_range_market()
    cfg = _config_for_tests()
    r = backtest_symbol("TEST", rows, rows, cfg, warmup_bars=100)
    assert r.bars > 0
    assert len(r.equity_curve) > 0
    # The equity curve ends at the same equity the broker reports.
    assert math.isclose(r.equity_curve[-1][1], r.final_equity)
    # Aggregate sanity: winrate within [0,1].
    assert 0.0 <= r.winrate <= 1.0
