"""Gate.io futures (USDT-M swap) adapter backed by ccxt."""

from __future__ import annotations

import logging
from typing import Any

import ccxt

from .base import ExchangeClient, Market, Ticker

log = logging.getLogger(__name__)


class GateFutures(ExchangeClient):
    """Thin wrapper around ccxt.gate configured for USDT perpetuals."""

    def __init__(self, api_key: str | None = None, secret: str | None = None) -> None:
        params: dict[str, Any] = {
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
        if api_key and secret:
            params["apiKey"] = api_key
            params["secret"] = secret
        self._ccxt = ccxt.gate(params)
        self._markets: dict[str, Market] = {}

    @property
    def ccxt(self) -> ccxt.gate:
        return self._ccxt

    def load_markets(self) -> dict[str, Market]:
        raw = self._ccxt.load_markets()
        out: dict[str, Market] = {}
        for sym, m in raw.items():
            if not m.get("swap"):
                continue
            if m.get("quote") != "USDT":
                continue
            if not m.get("active", True):
                continue
            precision = m.get("precision") or {}
            limits = (m.get("limits") or {}).get("amount") or {}
            out[sym] = Market(
                symbol=sym,
                base=m.get("base") or "",
                quote=m.get("quote") or "",
                contract_size=float(m.get("contractSize") or 1.0),
                price_precision=int(precision.get("price") or 8),
                amount_precision=int(precision.get("amount") or 0),
                min_amount=float(limits.get("min") or 1.0),
            )
        self._markets = out
        log.info("Loaded %d Gate USDT swap markets", len(out))
        return out

    def markets(self) -> dict[str, Market]:
        if not self._markets:
            self.load_markets()
        return self._markets

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> list[list[float]]:
        return self._ccxt.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    @staticmethod
    def _to_ticker(t: dict[str, Any]) -> Ticker:
        return Ticker(
            symbol=t["symbol"],
            bid=float(t.get("bid") or t.get("last") or 0.0),
            ask=float(t.get("ask") or t.get("last") or 0.0),
            last=float(t.get("last") or 0.0),
            quote_volume_24h=float(t.get("quoteVolume") or 0.0),
        )

    def fetch_ticker(self, symbol: str) -> Ticker:
        return self._to_ticker(self._ccxt.fetch_ticker(symbol))

    def fetch_tickers(self, symbols: list[str] | None = None) -> dict[str, Ticker]:
        raw = self._ccxt.fetch_tickers(symbols)
        return {s: self._to_ticker(t) for s, t in raw.items()}
