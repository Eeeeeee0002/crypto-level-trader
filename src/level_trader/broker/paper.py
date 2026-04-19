"""Paper broker: executes against real-time prices with realistic fees + slippage.

The broker tracks open positions and closes them when the live price hits their
stop-loss or take-profit. It does NOT place orders on any exchange.
"""

from __future__ import annotations

import itertools
import logging
import time
import uuid

from ..config import BrokerConfig
from .base import Broker, Position, Side

log = logging.getLogger(__name__)


class PaperBroker(Broker):
    def __init__(self, starting_equity: float, cfg: BrokerConfig) -> None:
        self._equity = float(starting_equity)
        self._cfg = cfg
        self._positions: dict[str, Position] = {}
        self._history: list[Position] = []
        self._ids = (f"p{n:06d}" for n in itertools.count(1))

    # -- Reporting ----------------------------------------------------------

    def equity(self) -> float:
        return self._equity

    def open_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if not p.closed]

    def closed_positions(self) -> list[Position]:
        return list(self._history)

    # -- Order flow ---------------------------------------------------------

    def _fill_price(self, side: Side, ref: float, is_entry: bool) -> float:
        """Apply slippage.

        Entries: buy fills above ref (for longs), sell fills below (for shorts).
        Exits:   opposite.
        """
        slip = self._cfg.slippage_pct
        if is_entry:
            return ref * (1 + slip) if side == "long" else ref * (1 - slip)
        # exit
        return ref * (1 - slip) if side == "long" else ref * (1 + slip)

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
    ) -> Position:
        fill = self._fill_price(side, entry, is_entry=True)
        fee = fill * quantity * self._cfg.taker_fee
        self._equity -= fee
        pid = next(self._ids) + "-" + uuid.uuid4().hex[:6]
        pos = Position(
            id=pid,
            symbol=symbol,
            side=side,
            entry=fill,
            stop=stop,
            take_profit=take_profit,
            quantity=quantity,
            opened_at=time.time(),
            reason=reason,
            fees_paid=fee,
        )
        self._positions[pid] = pos
        log.info(
            "PAPER OPEN %s %s qty=%.6f entry=%.6g stop=%.6g tp=%.6g (%s)",
            symbol, side, quantity, fill, stop, take_profit, reason,
        )
        return pos

    def _close(self, pos: Position, price: float, reason: str) -> None:
        fill = self._fill_price(pos.side, price, is_entry=False)
        fee = fill * pos.quantity * self._cfg.taker_fee
        if pos.side == "long":
            gross = (fill - pos.entry) * pos.quantity
        else:
            gross = (pos.entry - fill) * pos.quantity
        pnl = gross - fee
        pos.closed = True
        pos.close_price = fill
        pos.close_reason = reason
        pos.closed_at = time.time()
        pos.realized_pnl = pnl
        pos.fees_paid += fee
        self._equity += pnl
        self._history.append(pos)
        log.info(
            "PAPER CLOSE %s %s qty=%.6f exit=%.6g pnl=%.4f reason=%s (equity=%.4f)",
            pos.symbol, pos.side, pos.quantity, fill, pnl, reason, self._equity,
        )

    # -- Price-driven state ------------------------------------------------

    def on_price(self, symbol: str, price: float, ts: float) -> list[Position]:
        closed: list[Position] = []
        for pos in list(self._positions.values()):
            if pos.closed or pos.symbol != symbol:
                continue
            if pos.side == "long":
                if price <= pos.stop:
                    self._close(pos, pos.stop, "stop_loss")
                    closed.append(pos)
                elif price >= pos.take_profit:
                    self._close(pos, pos.take_profit, "take_profit")
                    closed.append(pos)
            else:  # short
                if price >= pos.stop:
                    self._close(pos, pos.stop, "stop_loss")
                    closed.append(pos)
                elif price <= pos.take_profit:
                    self._close(pos, pos.take_profit, "take_profit")
                    closed.append(pos)
        return closed
