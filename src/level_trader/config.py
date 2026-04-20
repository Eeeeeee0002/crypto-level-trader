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


class RiskConfig(BaseModel):
    account_equity: float = 10000.0
    risk_per_trade: float = 0.01
    leverage: int = 3
    max_concurrent_positions: int = 4
    sl_buffer_pct: float = 0.001
    tp_mode: Literal["rr", "next_level", "rr_or_next_level"] = "rr_or_next_level"
    tp_rr: float = 2.5


class BrokerConfig(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    taker_fee: float = 0.0005
    slippage_pct: float = 0.0005
    poll_seconds: int = 30


class LoggingConfig(BaseModel):
    level: str = "INFO"
    trade_log: str = "runs/trades.jsonl"
    equity_log: str = "runs/equity.jsonl"
    state_file: str = "runs/state.json"


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
