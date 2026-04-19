"""Detect horizontal support/resistance levels from OHLCV data.

The detector uses a classic swing-point approach:

1. Find pivot highs and lows (bars where the surrounding `lookback` bars on each
   side are lower/higher).
2. Cluster pivots whose prices are within `cluster_pct` of each other into a
   single level. Each cluster's price is the volume-weighted mean.
3. Score levels by number of touches, cumulative volume at touches, and
   optionally a bonus if the same level also exists on a higher timeframe.

The output is a list of `Level` objects sorted by score (strongest first).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import LevelsConfig


@dataclass
class Pivot:
    index: int        # bar index in the original OHLCV (0 = oldest)
    price: float
    volume: float
    kind: str         # "high" or "low"


@dataclass
class Level:
    price: float
    kind: str                  # "support" | "resistance" | "both"
    touches: int
    last_touch_index: int
    volume: float              # cumulative volume at touches
    score: float
    htf_confirmed: bool = False
    touch_indices: list[int] = field(default_factory=list)


def ohlcv_to_df(rows: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def find_pivots(df: pd.DataFrame, lookback: int) -> list[Pivot]:
    """Return pivot highs and lows. A pivot at i needs `lookback` bars on each side."""
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    vols = df["volume"].to_numpy()
    n = len(df)
    pivots: list[Pivot] = []
    for i in range(lookback, n - lookback):
        window_hi = highs[i - lookback : i + lookback + 1]
        window_lo = lows[i - lookback : i + lookback + 1]
        if highs[i] == window_hi.max() and np.argmax(window_hi) == lookback:
            pivots.append(Pivot(i, float(highs[i]), float(vols[i]), "high"))
        if lows[i] == window_lo.min() and np.argmin(window_lo) == lookback:
            pivots.append(Pivot(i, float(lows[i]), float(vols[i]), "low"))
    return pivots


def _cluster_pivots(pivots: list[Pivot], cluster_pct: float) -> list[list[Pivot]]:
    """Group pivots whose prices are within cluster_pct of each other."""
    if not pivots:
        return []
    sorted_pivots = sorted(pivots, key=lambda p: p.price)
    clusters: list[list[Pivot]] = [[sorted_pivots[0]]]
    for p in sorted_pivots[1:]:
        anchor = clusters[-1][0].price
        if abs(p.price - anchor) / anchor <= cluster_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return clusters


def detect_levels(
    df: pd.DataFrame,
    cfg: LevelsConfig,
    htf_levels: list[Level] | None = None,
) -> list[Level]:
    """Detect levels on a single timeframe dataframe.

    Pass `htf_levels` (from a higher timeframe) to enable the HTF confirmation bonus.
    """
    if len(df) < cfg.pivot_lookback * 2 + 5:
        return []

    pivots = find_pivots(df, cfg.pivot_lookback)
    clusters = _cluster_pivots(pivots, cfg.cluster_pct)

    n = len(df)
    levels: list[Level] = []
    for cluster in clusters:
        touches = len(cluster)
        if touches < cfg.min_touches:
            continue
        total_vol = sum(p.volume for p in cluster)
        # Volume-weighted price so busy touches pull the level toward them.
        vw_price = sum(p.price * p.volume for p in cluster) / total_vol if total_vol > 0 else (
            sum(p.price for p in cluster) / touches
        )
        last_idx = max(p.index for p in cluster)
        if (n - 1) - last_idx > cfg.max_age_bars:
            continue

        kinds = {p.kind for p in cluster}
        if kinds == {"high"}:
            kind = "resistance"
        elif kinds == {"low"}:
            kind = "support"
        else:
            kind = "both"

        htf_match = False
        if htf_levels:
            for h in htf_levels:
                if abs(h.price - vw_price) / vw_price <= cfg.cluster_pct:
                    htf_match = True
                    break

        score = (
            cfg.weight_touches * touches
            + cfg.weight_volume * (total_vol / max(df["volume"].mean(), 1e-9))
            + (cfg.weight_htf if htf_match else 0.0)
        )
        levels.append(
            Level(
                price=float(vw_price),
                kind=kind,
                touches=touches,
                last_touch_index=last_idx,
                volume=float(total_vol),
                score=float(score),
                htf_confirmed=htf_match,
                touch_indices=[p.index for p in cluster],
            )
        )

    levels.sort(key=lambda lv: lv.score, reverse=True)
    return levels


def nearest_levels(price: float, levels: list[Level]) -> tuple[Level | None, Level | None]:
    """Return the nearest (support_below, resistance_above) relative to `price`."""
    below = [lv for lv in levels if lv.price < price]
    above = [lv for lv in levels if lv.price > price]
    support = max(below, key=lambda lv: lv.price) if below else None
    resistance = min(above, key=lambda lv: lv.price) if above else None
    return support, resistance
