"""Paper broker: executes against real-time prices with realistic fees + slippage.

The broker tracks open positions and closes them when the live price hits their
stop-loss or take-profit. It does NOT place orders on any exchange.

The broker also applies optional trade management (partial take-profit,
breakeven move, and trailing stop) when a `RiskConfig` is supplied. These are
all expressed in R-multiples of the initial `|entry - stop|` distance captured
at open time.
"""

from __future__ import annotations

import itertools
import logging
import time
import uuid

from ..config import BrokerConfig, RiskConfig
from .base import Broker, Position, Side

log = logging.getLogger(__name__)


class PaperBroker(Broker):
    def __init__(
        self,
        starting_equity: float,
        cfg: BrokerConfig,
        risk: RiskConfig | None = None,
    ) -> None:
        self._equity = float(starting_equity)
        self._cfg = cfg
        self._risk = risk
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
            initial_risk=abs(fill - stop),
            initial_quantity=quantity,
            peak_favorable=fill,
        )
        self._positions[pid] = pos
        log.info(
            "PAPER OPEN %s %s qty=%.6f entry=%.6g stop=%.6g tp=%.6g (%s)",
            symbol, side, quantity, fill, stop, take_profit, reason,
        )
        return pos

    def _close(self, pos: Position, price: float, reason: str, *, quantity: float | None = None) -> float:
        """Close `quantity` of `pos` at `price`. Returns the realized pnl of the closed slice."""
        qty = pos.quantity if quantity is None else min(quantity, pos.quantity)
        if qty <= 0:
            return 0.0
        fill = self._fill_price(pos.side, price, is_entry=False)
        fee = fill * qty * self._cfg.taker_fee
        if pos.side == "long":
            gross = (fill - pos.entry) * qty
        else:
            gross = (pos.entry - fill) * qty
        pnl = gross - fee
        pos.fees_paid += fee
        pos.realized_pnl += pnl
        self._equity += pnl
        pos.quantity -= qty
        full_close = pos.quantity <= 1e-12 or quantity is None
        if full_close:
            pos.closed = True
            pos.close_price = fill
            pos.close_reason = reason
            pos.closed_at = time.time()
            pos.quantity = 0.0
            self._history.append(pos)
            log.info(
                "PAPER CLOSE %s %s qty=%.6f exit=%.6g pnl=%.4f reason=%s (equity=%.4f)",
                pos.symbol, pos.side, qty, fill, pnl, reason, self._equity,
            )
        else:
            pos.partial_filled = True
            log.info(
                "PAPER PARTIAL %s %s qty=%.6f exit=%.6g pnl=%.4f reason=%s (remaining=%.6f)",
                pos.symbol, pos.side, qty, fill, pnl, reason, pos.quantity,
            )
        return pnl

    # -- Trade management ---------------------------------------------------

    def _apply_management(self, pos: Position, price: float) -> None:
        """Update `pos` in-place for partial-TP, breakeven move, and trailing stop."""
        risk = self._risk
        if risk is None or pos.initial_risk <= 0:
            return

        # Track peak favourable excursion.
        if pos.side == "long":
            pos.peak_favorable = max(pos.peak_favorable, price)
        else:
            pos.peak_favorable = min(pos.peak_favorable or price, price)

        # Partial take-profit at `partial_tp_r` R.
        if (
            risk.partial_tp_frac > 0
            and not pos.partial_filled
            and pos.initial_quantity > 0
        ):
            target = (
                pos.entry + risk.partial_tp_r * pos.initial_risk
                if pos.side == "long"
                else pos.entry - risk.partial_tp_r * pos.initial_risk
            )
            hit = price >= target if pos.side == "long" else price <= target
            if hit:
                qty_to_close = pos.initial_quantity * risk.partial_tp_frac
                self._close(pos, target, "partial_tp", quantity=qty_to_close)

        # Move stop to breakeven once price has moved `breakeven_at_r` R in our favour.
        if risk.breakeven_at_r > 0 and not pos.be_moved:
            threshold = (
                pos.entry + risk.breakeven_at_r * pos.initial_risk
                if pos.side == "long"
                else pos.entry - risk.breakeven_at_r * pos.initial_risk
            )
            reached = price >= threshold if pos.side == "long" else price <= threshold
            if reached:
                if pos.side == "long" and pos.entry > pos.stop:
                    pos.stop = pos.entry
                    pos.be_moved = True
                elif pos.side == "short" and pos.entry < pos.stop:
                    pos.stop = pos.entry
                    pos.be_moved = True

        # Trail stop at `trail_r` R behind the peak favourable price, once BE is done.
        if risk.trail_r > 0 and pos.be_moved:
            if pos.side == "long":
                new_stop = pos.peak_favorable - risk.trail_r * pos.initial_risk
                if new_stop > pos.stop:
                    pos.stop = new_stop
            else:
                new_stop = pos.peak_favorable + risk.trail_r * pos.initial_risk
                if new_stop < pos.stop:
                    pos.stop = new_stop

    # -- Price-driven state ------------------------------------------------

    def on_price(self, symbol: str, price: float, ts: float) -> list[Position]:
        closed: list[Position] = []
        for pos in list(self._positions.values()):
            if pos.closed or pos.symbol != symbol:
                continue
            self._apply_management(pos, price)
            if pos.closed:
                closed.append(pos)
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
