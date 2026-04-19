"""Typed configuration loaded from a YAML file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class ExchangeConfig(BaseModel):
    name: str = "gate"
    market: str = "swap"
    quote: str = "USDT"


class UniverseConfig(BaseModel):
    mode: Literal["top_volume", "fixed"] = "top_volume"
    size: int = 10
    symbols: list[str] = Field(default_factory=list)
    exclude_contains: list[str] = Field(default_factory=list)


class TimeframesConfig(BaseModel):
    execution: str = "1h"
    context: str = "4h"
    lookback_bars: int = 500


class LevelsConfig(BaseModel):
    pivot_lookback: int = 5
    cluster_pct: float = 0.004
    min_touches: int = 2
    weight_touches: float = 1.0
    weight_volume: float = 0.5
    weight_htf: float = 1.5
    max_age_bars: int = 300


class SignalsConfig(BaseModel):
    approach_pct: float = 0.0025
    breakout_pct: float = 0.0015
    require_confirmation: bool = True
    use_trend_filter: bool = True
    trend_ema: int = 200
    min_rr: float = 2.0
    cooldown_bars: int = 3
    # ATR-based stop placement. When > 0, SL is placed `atr_stop_mult * ATR`
    # beyond the level instead of a flat percentage. Falls back to pct when
    # ATR is unavailable.
    atr_period: int = 14
    atr_stop_mult: float = 0.0
    # Volatility gate: skip signals when ATR/price falls outside this band.
    min_atr_pct: float = 0.0   # 0 disables lower bound
    max_atr_pct: float = 0.0   # 0 disables upper bound


class RiskConfig(BaseModel):
    account_equity: float = 10000.0
    risk_per_trade: float = 0.01
    leverage: int = 3
    max_concurrent_positions: int = 4
    sl_buffer_pct: float = 0.001
    tp_mode: Literal["rr", "next_level", "rr_or_next_level"] = "rr_or_next_level"
    tp_rr: float = 2.5
    # Trade management: move stop to breakeven once price has travelled
    # `breakeven_at_r` multiples of the initial risk in our favour.
    breakeven_at_r: float = 0.0      # 0 disables
    # Trail stop at `trail_r` R behind the highest favourable price after
    # move-to-breakeven has triggered.
    trail_r: float = 0.0             # 0 disables trailing
    # Partial take-profit: close `partial_tp_frac` of the position at
    # `partial_tp_r` R. 0 disables.
    partial_tp_frac: float = 0.0
    partial_tp_r: float = 1.0
    # Max drawdown kill-switch: halt new entries once equity drops this
    # fraction from the peak observed equity.
    max_drawdown_pct: float = 0.0    # 0 disables


class BrokerConfig(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    taker_fee: float = 0.0005
    slippage_pct: float = 0.0005
    poll_seconds: int = 30


class LoggingConfig(BaseModel):
    level: str = "INFO"
    trade_log: str = "runs/trades.jsonl"
    equity_log: str = "runs/equity.jsonl"


class Config(BaseModel):
    exchange: ExchangeConfig = ExchangeConfig()
    universe: UniverseConfig = UniverseConfig()
    timeframes: TimeframesConfig = TimeframesConfig()
    levels: LevelsConfig = LevelsConfig()
    signals: SignalsConfig = SignalsConfig()
    risk: RiskConfig = RiskConfig()
    broker: BrokerConfig = BrokerConfig()
    logging: LoggingConfig = LoggingConfig()

    @classmethod
    def load(cls, path: str | Path) -> Config:
        data = yaml.safe_load(Path(path).read_text()) or {}
        return cls.model_validate(data)
