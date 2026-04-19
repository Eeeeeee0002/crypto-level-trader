"""Live Gate futures broker. Not used unless --live is passed and API keys are provided.

This module is intentionally conservative: it wraps ccxt's `create_order` and
reconciles open positions from the exchange. It is gated behind an explicit flag
so an accidental run can never send real orders.
"""

from __future__ import annotations

import itertools
import logging
import time
import uuid
from typing import Any

from ..config import BrokerConfig
from ..exchange.gate import GateFutures
from .base import Broker, Position, Side

log = logging.getLogger(__name__)


class LiveGateBroker(Broker):
    def __init__(self, exchange: GateFutures, cfg: BrokerConfig, leverage: int) -> None:
        self._ex = exchange
        self._cfg = cfg
        self._leverage = leverage
        self._positions: dict[str, Position] = {}
        self._ids = (f"l{n:06d}" for n in itertools.count(1))

    def equity(self) -> float:
        try:
            bal = self._ex.ccxt.fetch_balance(params={"type": "swap"})
            usdt = bal.get("USDT") or {}
            return float(usdt.get("total") or 0.0)
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to fetch equity: %s", e)
            return 0.0

    def open_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if not p.closed]

    def _ensure_leverage(self, symbol: str) -> None:
        try:
            self._ex.ccxt.set_leverage(self._leverage, symbol)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not set leverage for %s: %s", symbol, e)

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
        self._ensure_leverage(symbol)
        ccxt_side = "buy" if side == "long" else "sell"
        params: dict[str, Any] = {"reduceOnly": False}
        order = self._ex.ccxt.create_order(symbol, type="market", side=ccxt_side, amount=quantity, params=params)
        fill = float(order.get("average") or order.get("price") or entry)
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
            meta={"entry_order_id": order.get("id")},
        )
        # Attach stop-loss and take-profit as reduce-only trigger orders.
        try:
            close_side = "sell" if side == "long" else "buy"
            sl_params = {"stopPrice": stop, "reduceOnly": True, "triggerPrice": stop}
            tp_params = {"stopPrice": take_profit, "reduceOnly": True, "triggerPrice": take_profit}
            sl = self._ex.ccxt.create_order(symbol, "market", close_side, quantity, params=sl_params)
            tp = self._ex.ccxt.create_order(symbol, "market", close_side, quantity, params=tp_params)
            pos.meta["sl_order_id"] = sl.get("id")
            pos.meta["tp_order_id"] = tp.get("id")
        except Exception as e:  # noqa: BLE001
            log.error("Failed to place SL/TP for %s: %s", symbol, e)
        self._positions[pid] = pos
        log.info("LIVE OPEN %s %s qty=%.6f @ %.6g (%s)", symbol, side, quantity, fill, reason)
        return pos

    def on_price(self, symbol: str, price: float, ts: float) -> list[Position]:
        """Poll exchange to see if SL/TP filled. Simplified for the paper-first phase."""
        closed: list[Position] = []
        for pos in list(self._positions.values()):
            if pos.closed or pos.symbol != symbol:
                continue
            sl_id = pos.meta.get("sl_order_id")
            tp_id = pos.meta.get("tp_order_id")
            for oid, tag in ((sl_id, "stop_loss"), (tp_id, "take_profit")):
                if not oid:
                    continue
                try:
                    od = self._ex.ccxt.fetch_order(oid, symbol)
                except Exception:  # noqa: BLE001
                    continue
                if od.get("status") == "closed":
                    fill_price = float(od.get("average") or od.get("price") or price)
                    pos.closed = True
                    pos.close_price = fill_price
                    pos.close_reason = tag
                    pos.closed_at = time.time()
                    if pos.side == "long":
                        pos.realized_pnl = (fill_price - pos.entry) * pos.quantity
                    else:
                        pos.realized_pnl = (pos.entry - fill_price) * pos.quantity
                    closed.append(pos)
                    # Cancel the sibling order.
                    other = tp_id if tag == "stop_loss" else sl_id
                    if other:
                        try:
                            self._ex.ccxt.cancel_order(other, symbol)
                        except Exception:  # noqa: BLE001
                            pass
                    break
        return closed
