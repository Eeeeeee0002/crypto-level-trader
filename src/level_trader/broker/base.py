"""Broker interface: opens, tracks, and closes positions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

Side = Literal["long", "short"]


@dataclass
class Position:
    id: str
    symbol: str
    side: Side
    entry: float
    stop: float
    take_profit: float
    quantity: float
    opened_at: float     # unix timestamp
    reason: str = ""
    closed: bool = False
    close_price: float | None = None
    close_reason: str | None = None
    closed_at: float | None = None
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    meta: dict = field(default_factory=dict)


class Broker(Protocol):
    """Minimal interface used by the trader orchestrator."""

    def equity(self) -> float: ...

    def open_positions(self) -> list[Position]: ...

    def open(
        self,
        *,
        symbol: str,
        side: Side,
        entry: float,
        stop: float,
        take_profit: float,
        quantity: float,
        reason: str = "",
    ) -> Position: ...

    def on_price(self, symbol: str, price: float, ts: float) -> list[Position]:
        """Mark-to-market + check SL/TP. Returns list of positions closed on this tick."""
        ...
