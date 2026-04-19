"""Minimal exchange abstraction so the rest of the system doesn't care about ccxt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    quote_volume_24h: float


@dataclass(frozen=True)
class Market:
    symbol: str
    base: str
    quote: str
    contract_size: float
    price_precision: int
    amount_precision: int
    min_amount: float


class ExchangeClient(Protocol):
    """Read-only data surface used by the strategy."""

    def load_markets(self) -> dict[str, Market]: ...

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 500
    ) -> list[list[float]]: ...

    def fetch_ticker(self, symbol: str) -> Ticker: ...

    def fetch_tickers(self, symbols: list[str] | None = None) -> dict[str, Ticker]: ...
