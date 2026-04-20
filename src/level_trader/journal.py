"""Persistent trade + equity journal with simple performance metrics."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .broker.base import Position

log = logging.getLogger(__name__)


def _to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return obj


class Journal:
    def __init__(self, trade_log: str, equity_log: str, state_file: str | None = None) -> None:
        self.trade_path = Path(trade_log)
        self.equity_path = Path(equity_log)
        # Default the live-state snapshot next to the equity log.
        self.state_path = Path(state_file) if state_file else self.equity_path.parent / "state.json"
        for p in (self.trade_path, self.equity_path, self.state_path):
            p.parent.mkdir(parents=True, exist_ok=True)

    def record_trade(self, pos: Position) -> None:
        with self.trade_path.open("a") as f:
            f.write(json.dumps(_to_dict(pos), default=str) + "\n")

    def record_equity(self, ts: float, equity: float, unrealized: float = 0.0) -> None:
        with self.equity_path.open("a") as f:
            f.write(json.dumps({"ts": ts, "equity": equity, "unrealized": unrealized}) + "\n")

    def record_state(self, state: dict[str, Any]) -> None:
        """Atomically snapshot the current trader state for the dashboard."""
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, default=str, indent=2))
        tmp.replace(self.state_path)


def summarize(positions: list[Position]) -> dict[str, float]:
    closed = [p for p in positions if p.closed]
    if not closed:
        return {"trades": 0}
    pnls = [p.realized_pnl for p in closed]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)  # positive number
    total = sum(pnls)
    winrate = len(wins) / len(closed)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    return {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "winrate": winrate,
        "total_pnl": total,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "profit_factor": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
    }
