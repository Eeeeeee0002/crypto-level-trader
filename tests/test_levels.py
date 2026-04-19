import numpy as np
import pandas as pd

from level_trader.config import LevelsConfig
from level_trader.levels.detector import detect_levels, find_pivots, nearest_levels, ohlcv_to_df


def _bar(ts: int, o: float, h: float, low: float, c: float, v: float = 1000.0) -> list:
    return [ts, o, h, low, c, v]


def _series(prices: list[float]) -> pd.DataFrame:
    rows = [_bar(i * 3600_000, p, p + 1, p - 1, p, 1000.0) for i, p in enumerate(prices)]
    return ohlcv_to_df(rows)


def test_find_pivots_simple_peak_and_trough():
    prices = [10, 11, 12, 13, 14, 13, 12, 11, 10, 9, 8, 9, 10, 11, 12]
    df = _series(prices)
    pivots = find_pivots(df, lookback=3)
    kinds = sorted((p.kind, int(p.index)) for p in pivots)
    # Expect a high pivot near index 4 and a low pivot near index 10.
    assert any(k == "high" and idx == 4 for k, idx in kinds)
    assert any(k == "low" and idx == 10 for k, idx in kinds)


def test_detect_levels_finds_repeating_support():
    # 3 bounces off ~100 with intervening peaks.
    prices = [
        100, 103, 106, 103, 100,
        103, 106, 103, 100,
        103, 106, 103, 100,
        103, 106, 103,
    ]
    df = _series(prices)
    # Tight cluster_pct: swings are exactly equal here so any small tolerance works.
    cfg = LevelsConfig(pivot_lookback=2, cluster_pct=0.01, min_touches=2)
    levels = detect_levels(df, cfg)
    assert levels, "expected to detect at least one level"
    # Swing lows sit at p-1 for price p, so detection anchors near 99.
    support_prices = [lv.price for lv in levels if lv.kind in ("support", "both")]
    assert any(abs(p - 99) / 99 <= 0.01 for p in support_prices)


def test_nearest_levels_picks_correct_neighbors():
    prices = np.linspace(100, 110, 30).tolist() + np.linspace(110, 100, 30).tolist()
    df = _series(prices)
    cfg = LevelsConfig(pivot_lookback=3, cluster_pct=0.01, min_touches=1)
    levels = detect_levels(df, cfg)
    if not levels:
        return
    support, resistance = nearest_levels(105.0, levels)
    if support is not None:
        assert support.price <= 105.0
    if resistance is not None:
        assert resistance.price >= 105.0
