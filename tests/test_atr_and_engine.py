"""Tests for ATR, ATR-based stops, and signal engine respecting risk config."""

import pandas as pd

from level_trader.config import LevelsConfig, RiskConfig, SignalsConfig
from level_trader.levels.detector import detect_levels, ohlcv_to_df
from level_trader.signals.engine import _stop_from_level, atr, generate_signal


def _rows(bars: list[tuple[float, float, float, float, float]]) -> list[list[float]]:
    return [[i * 3600_000, o, h, lo, c, v] for i, (o, h, lo, c, v) in enumerate(bars)]


def test_atr_is_zero_when_too_few_bars():
    df = ohlcv_to_df(_rows([(100, 101, 99, 100, 10)] * 5))
    assert atr(df, period=14) == 0.0


def test_atr_reflects_range():
    df = ohlcv_to_df(_rows([(100, 102, 98, 100, 10)] * 30))
    # Each bar's TR is 4 (high-low). ATR(14) should be ~4.
    assert abs(atr(df, period=14) - 4.0) < 1e-6


def test_stop_from_level_prefers_larger_of_pct_vs_atr():
    # ATR 2 on a 100 level with 1x ATR multiple -> 2% offset. pct 0.5%. ATR wins.
    stop_long = _stop_from_level(
        side="long",
        level_price=100.0,
        atr_value=2.0,
        approach_pct=0.002,
        buffer_pct=0.005,
        atr_stop_mult=1.0,
    )
    assert abs(stop_long - 98.0) < 1e-6
    stop_short = _stop_from_level(
        side="short",
        level_price=100.0,
        atr_value=2.0,
        approach_pct=0.002,
        buffer_pct=0.005,
        atr_stop_mult=1.0,
    )
    assert abs(stop_short - 102.0) < 1e-6


def test_stop_from_level_falls_back_to_pct_when_atr_disabled():
    stop = _stop_from_level(
        side="long",
        level_price=100.0,
        atr_value=5.0,
        approach_pct=0.001,
        buffer_pct=0.01,
        atr_stop_mult=0.0,
    )
    # ATR disabled -> use max(pct buffer, approach) = 1% -> stop 99.
    assert abs(stop - 99.0) < 1e-6


def test_vol_filter_blocks_signal_in_dead_market():
    base = []
    # Extremely flat market - ATR is essentially zero.
    for _ in range(6):
        base += [
            (101.0, 101.0, 100.0, 100.5, 1000),
            (100.5, 100.5, 99.5, 100.0, 1000),
        ]
    df = ohlcv_to_df(_rows(base))
    levels = detect_levels(df, LevelsConfig(pivot_lookback=2, cluster_pct=0.01, min_touches=2))
    cfg = SignalsConfig(
        approach_pct=0.01,
        require_confirmation=False,
        use_trend_filter=False,
        min_rr=0.1,
        cooldown_bars=0,
        atr_period=5,
        min_atr_pct=0.05,  # require >= 5% ATR/price, which never happens here
    )
    sig = generate_signal("TEST", df, df, levels, cfg, risk=RiskConfig())
    assert sig is None


# Silence unused pandas import when file is run alone.
_ = pd
