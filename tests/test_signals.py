import pandas as pd

from level_trader.config import LevelsConfig, SignalsConfig
from level_trader.levels.detector import detect_levels, ohlcv_to_df
from level_trader.signals.engine import generate_signal


def _rows(bars: list[tuple[float, float, float, float, float]]) -> list[list[float]]:
    # bars are (o, h, low, c, v); we fabricate timestamps.
    return [[i * 3600_000, o, h, low, c, v] for i, (o, h, low, c, v) in enumerate(bars)]


def test_bounce_off_support_generates_long_signal():
    # Build a market that bounces off 100 three times, then approaches 100 again with a bullish pin.
    base = []
    for _ in range(3):
        base += [
            (101, 103, 100.2, 102, 1000),  # rally
            (102, 104, 101, 103, 1000),    # continuation up
            (103, 103, 100.5, 101, 1000),  # pullback
            (101, 101, 99.9, 100.1, 1000), # tag of support
            (100.1, 102, 100, 101.5, 1000),  # bounce up
            (101.5, 103, 101, 102.8, 1000),  # continuation
        ]
    # Current bar: wick below 100 but closes above -> bullish pin on support.
    base += [
        (102.8, 103, 101.5, 102, 1000),    # pullback
        (102, 102.2, 99.9, 101.8, 2000),   # pin bar tagging 100
    ]
    rows = _rows(base)
    exec_df = ohlcv_to_df(rows)
    # Use the same data as "context" for test purposes.
    ctx_df = exec_df.copy()
    lv_cfg = LevelsConfig(pivot_lookback=2, cluster_pct=0.01, min_touches=2, max_age_bars=500)
    levels = detect_levels(exec_df, lv_cfg)
    assert levels, "expected detected levels in fixture"
    sig_cfg = SignalsConfig(
        approach_pct=0.005,
        require_confirmation=True,
        use_trend_filter=False,
        min_rr=0.5,  # synthetic fixture with tight levels; we only check that a signal fires
        cooldown_bars=0,
    )
    sig = generate_signal("TEST", exec_df, ctx_df, levels, sig_cfg)
    assert sig is not None, "expected long signal on support bounce"
    assert sig.side == "long"
    assert sig.stop < sig.entry < sig.take_profit


def test_no_signal_when_price_far_from_levels():
    rows = _rows([(100, 101, 99, 100, 1000)] * 50)
    df = ohlcv_to_df(rows)
    # Break the flatness so some levels exist.
    df.loc[10, "high"] = 110
    df.loc[20, "low"] = 90
    lv_cfg = LevelsConfig(pivot_lookback=2, cluster_pct=0.005, min_touches=1)
    levels = detect_levels(df, lv_cfg)
    sig_cfg = SignalsConfig(use_trend_filter=False, min_rr=1.5, cooldown_bars=0)
    sig = generate_signal("TEST", df, df, levels, sig_cfg)
    # Price is at 100 with detected levels far away; bounces shouldn't fire.
    assert sig is None, f"unexpected signal: {sig}"


# silence pandas unused import in isolation
_ = pd
