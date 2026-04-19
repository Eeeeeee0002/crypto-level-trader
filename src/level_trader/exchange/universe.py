"""Pick which symbols to trade."""

from __future__ import annotations

import logging

from ..config import UniverseConfig
from .base import ExchangeClient

log = logging.getLogger(__name__)


def select_universe(exchange: ExchangeClient, cfg: UniverseConfig) -> list[str]:
    """Return a list of symbols to trade according to `cfg`."""
    markets = exchange.load_markets()
    available = list(markets.keys())

    if cfg.mode == "fixed":
        missing = [s for s in cfg.symbols if s not in markets]
        if missing:
            log.warning("Configured symbols not available on exchange: %s", missing)
        return [s for s in cfg.symbols if s in markets]

    tickers = exchange.fetch_tickers(available)

    def is_allowed(sym: str) -> bool:
        return not any(bad in sym for bad in cfg.exclude_contains)

    ranked = sorted(
        (t for t in tickers.values() if is_allowed(t.symbol)),
        key=lambda t: t.quote_volume_24h,
        reverse=True,
    )
    chosen = [t.symbol for t in ranked[: cfg.size]]
    log.info("Selected universe (%d symbols): %s", len(chosen), chosen)
    return chosen
