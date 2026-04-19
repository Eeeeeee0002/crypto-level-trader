"""Turn OHLCV + detected levels into trade signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from ..config import SignalsConfig
from ..levels.detector import Level, nearest_levels

Side = Literal["long", "short"]
SignalKind = Literal["reversal", "breakout_retest"]


@dataclass
class Signal:
    symbol: str
    side: Side
    kind: SignalKind
    entry: float
    stop: float
    take_profit: float
    level_price: float
    rr: float
    reason: str


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _is_bullish_confirmation(df: pd.DataFrame) -> bool:
    """Last closed candle shows bullish rejection (pin bar or engulfing).

    Accepts any pin with a long lower wick and a close in the upper half of the
    range, regardless of whether the body itself is green or red.
    """
    if len(df) < 2:
        return False
    last = df.iloc[-1]
    prev = df.iloc[-2]
    body = abs(last["close"] - last["open"])
    rng = last["high"] - last["low"]
    if rng <= 0:
        return False
    lower_wick = min(last["open"], last["close"]) - last["low"]
    close_in_upper_half = last["close"] >= last["low"] + rng * 0.5
    if lower_wick >= 2 * body and close_in_upper_half:
        return True
    # Bullish engulfing.
    if (
        prev["close"] < prev["open"]
        and last["close"] > last["open"]
        and last["close"] >= prev["open"]
        and last["open"] <= prev["close"]
    ):
        return True
    return False


def _is_bearish_confirmation(df: pd.DataFrame) -> bool:
    if len(df) < 2:
        return False
    last = df.iloc[-1]
    prev = df.iloc[-2]
    body = abs(last["close"] - last["open"])
    rng = last["high"] - last["low"]
    if rng <= 0:
        return False
    upper_wick = last["high"] - max(last["open"], last["close"])
    close_in_lower_half = last["close"] <= last["high"] - rng * 0.5
    if upper_wick >= 2 * body and close_in_lower_half:
        return True
    if (
        prev["close"] > prev["open"]
        and last["close"] < last["open"]
        and last["close"] <= prev["open"]
        and last["open"] >= prev["close"]
    ):
        return True
    return False


def _trend_bias(context_df: pd.DataFrame, period: int) -> Side | None:
    if len(context_df) < period + 2:
        return None
    ema = _ema(context_df["close"], period)
    last_close = float(context_df["close"].iloc[-1])
    last_ema = float(ema.iloc[-1])
    if last_close > last_ema:
        return "long"
    if last_close < last_ema:
        return "short"
    return None


def _compute_tp(
    entry: float, stop: float, side: Side, levels: list[Level], cfg_tp_mode: str, cfg_tp_rr: float
) -> float:
    rr_target = entry + cfg_tp_rr * (entry - stop) if side == "long" else entry - cfg_tp_rr * (stop - entry)
    if cfg_tp_mode == "rr":
        return rr_target

    # next opposing level
    if side == "long":
        candidates = [lv.price for lv in levels if lv.price > entry]
        next_level = min(candidates) if candidates else rr_target
    else:
        candidates = [lv.price for lv in levels if lv.price < entry]
        next_level = max(candidates) if candidates else rr_target

    if cfg_tp_mode == "next_level":
        return next_level
    # rr_or_next_level -> the closer of the two (more conservative)
    if side == "long":
        return min(rr_target, next_level)
    return max(rr_target, next_level)


def generate_signal(
    symbol: str,
    exec_df: pd.DataFrame,
    context_df: pd.DataFrame,
    levels: list[Level],
    cfg: SignalsConfig,
) -> Signal | None:
    """Return a `Signal` if current market state matches a level-trading setup, else None.

    Called once per completed bar of the execution timeframe.
    """
    if len(exec_df) < 3 or not levels:
        return None

    last = exec_df.iloc[-1]
    prev = exec_df.iloc[-2]
    close = float(last["close"])
    high = float(last["high"])
    low = float(last["low"])

    trend: Side | None = None
    if cfg.use_trend_filter:
        trend = _trend_bias(context_df, cfg.trend_ema)

    support, resistance = nearest_levels(close, levels)

    # ---------- Reversal from support (long) ----------
    if support is not None:
        approached = low <= support.price * (1 + cfg.approach_pct) and low >= support.price * (
            1 - cfg.approach_pct * 2
        )
        held = close > support.price
        confirmed = (not cfg.require_confirmation) or _is_bullish_confirmation(exec_df)
        trend_ok = trend != "short"
        if approached and held and confirmed and trend_ok:
            stop = support.price * (1 - max(cfg.approach_pct, 0.001))
            tp = _compute_tp(close, stop, "long", levels, "rr_or_next_level", cfg.min_rr * 1.25)
            rr = (tp - close) / max(close - stop, 1e-9)
            if rr >= cfg.min_rr:
                return Signal(
                    symbol=symbol,
                    side="long",
                    kind="reversal",
                    entry=close,
                    stop=stop,
                    take_profit=tp,
                    level_price=support.price,
                    rr=rr,
                    reason=f"bounce off support {support.price:.6g} (touches={support.touches},htf={support.htf_confirmed})",
                )

    # ---------- Reversal from resistance (short) ----------
    if resistance is not None:
        approached = high >= resistance.price * (1 - cfg.approach_pct) and high <= resistance.price * (
            1 + cfg.approach_pct * 2
        )
        held = close < resistance.price
        confirmed = (not cfg.require_confirmation) or _is_bearish_confirmation(exec_df)
        trend_ok = trend != "long"
        if approached and held and confirmed and trend_ok:
            stop = resistance.price * (1 + max(cfg.approach_pct, 0.001))
            tp = _compute_tp(close, stop, "short", levels, "rr_or_next_level", cfg.min_rr * 1.25)
            rr = (close - tp) / max(stop - close, 1e-9)
            if rr >= cfg.min_rr:
                return Signal(
                    symbol=symbol,
                    side="short",
                    kind="reversal",
                    entry=close,
                    stop=stop,
                    take_profit=tp,
                    level_price=resistance.price,
                    rr=rr,
                    reason=f"rejection off resistance {resistance.price:.6g} (touches={resistance.touches},htf={resistance.htf_confirmed})",
                )

    # ---------- Breakout + retest ----------
    # We look for: prior bar broke above a resistance by breakout_pct, current bar retested it from above.
    if resistance is not None and prev["close"] > resistance.price * (1 + cfg.breakout_pct):
        retested = low <= resistance.price * (1 + cfg.approach_pct) and close > resistance.price
        confirmed = (not cfg.require_confirmation) or _is_bullish_confirmation(exec_df)
        if retested and confirmed and trend != "short":
            stop = resistance.price * (1 - cfg.approach_pct)
            tp = _compute_tp(close, stop, "long", levels, "rr_or_next_level", cfg.min_rr * 1.25)
            rr = (tp - close) / max(close - stop, 1e-9)
            if rr >= cfg.min_rr:
                return Signal(
                    symbol=symbol,
                    side="long",
                    kind="breakout_retest",
                    entry=close,
                    stop=stop,
                    take_profit=tp,
                    level_price=resistance.price,
                    rr=rr,
                    reason=f"breakout+retest of {resistance.price:.6g}",
                )

    if support is not None and prev["close"] < support.price * (1 - cfg.breakout_pct):
        retested = high >= support.price * (1 - cfg.approach_pct) and close < support.price
        confirmed = (not cfg.require_confirmation) or _is_bearish_confirmation(exec_df)
        if retested and confirmed and trend != "long":
            stop = support.price * (1 + cfg.approach_pct)
            tp = _compute_tp(close, stop, "short", levels, "rr_or_next_level", cfg.min_rr * 1.25)
            rr = (close - tp) / max(stop - close, 1e-9)
            if rr >= cfg.min_rr:
                return Signal(
                    symbol=symbol,
                    side="short",
                    kind="breakout_retest",
                    entry=close,
                    stop=stop,
                    take_profit=tp,
                    level_price=support.price,
                    rr=rr,
                    reason=f"breakdown+retest of {support.price:.6g}",
                )

    return None


# Exported helpers for tests
__all__ = [
    "Signal",
    "generate_signal",
    "_is_bullish_confirmation",
    "_is_bearish_confirmation",
    "_trend_bias",
]

# Silence unused numpy import if the file is linted in isolation.
_ = np
