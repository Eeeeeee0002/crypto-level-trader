"""Position sizing with fixed fractional risk and leverage cap."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import RiskConfig


@dataclass(frozen=True)
class PositionPlan:
    side: str           # "long" | "short"
    entry: float
    stop: float
    take_profit: float
    quantity: float     # in base units (coins), already rounded elsewhere if needed
    notional: float     # entry * quantity
    risk_amount: float  # expected loss if stop hits, in quote currency
    leverage_used: float


def plan_position(
    *,
    equity: float,
    side: str,
    entry: float,
    stop: float,
    take_profit: float,
    cfg: RiskConfig,
) -> PositionPlan | None:
    """Return a sized position plan, or None if the trade cannot be sized sensibly."""
    if entry <= 0 or stop <= 0 or equity <= 0:
        return None

    per_unit_risk = abs(entry - stop)
    if per_unit_risk <= 0:
        return None

    risk_amount = equity * cfg.risk_per_trade
    qty = risk_amount / per_unit_risk
    if qty <= 0:
        return None

    notional = qty * entry
    max_notional = equity * cfg.leverage
    if notional > max_notional:
        # Cap at max leverage. Risk then becomes smaller than the target, which is fine.
        qty = max_notional / entry
        notional = qty * entry
        risk_amount = qty * per_unit_risk

    if side == "long" and not (stop < entry < take_profit):
        return None
    if side == "short" and not (take_profit < entry < stop):
        return None

    return PositionPlan(
        side=side,
        entry=entry,
        stop=stop,
        take_profit=take_profit,
        quantity=qty,
        notional=notional,
        risk_amount=risk_amount,
        leverage_used=notional / equity if equity > 0 else 0.0,
    )
