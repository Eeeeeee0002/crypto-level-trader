"""Historical backtester for the level-trading strategy.

Replays OHLCV bars bar-by-bar through the signal engine and the paper broker so
users can validate the strategy on past data before risking capital. The
simulation is deterministic and uses a pessimistic intrabar fill model:

- longs: check the bar's low (possible SL) before its high (possible TP)
- shorts: check the bar's high (possible SL) before its low (possible TP)

This mirrors a worst-case order-of-fills assumption when both SL and TP sit
inside the same bar's range.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pandas as pd

from .broker.base import Position
from .broker.paper import PaperBroker
from .config import Config
from .levels.detector import detect_levels
from .risk.sizing import plan_position
from .signals.engine import generate_signal

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    symbol: str
    bars: int
    trades: int
    wins: int
    losses: int
    winrate: float
    total_pnl: float
    profit_factor: float
    max_drawdown: float
    final_equity: float
    equity_curve: list[tuple[float, float]]  # (ts, equity)
    positions: list[Position]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "bars": self.bars,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "winrate": round(self.winrate, 4),
            "total_pnl": round(self.total_pnl, 4),
            "profit_factor": (
                round(self.profit_factor, 3)
                if math.isfinite(self.profit_factor)
                else self.profit_factor
            ),
            "max_drawdown": round(self.max_drawdown, 4),
            "final_equity": round(self.final_equity, 4),
        }


def _align_context_index(exec_ts: pd.Timestamp, ctx_df: pd.DataFrame) -> int:
    """Return the index (inclusive) of the last context bar that has already closed by `exec_ts`."""
    if ctx_df.empty:
        return -1
    # Compare as int nanoseconds so tz-naive/aware mismatches cannot bite us.
    exec_ns = int(pd.Timestamp(exec_ts).value)
    ctx_ns = ctx_df["ts"].astype("int64").to_numpy()
    idx = int((ctx_ns <= exec_ns).sum()) - 1
    return max(idx, -1)


def backtest_symbol(
    symbol: str,
    exec_rows: list[list[float]],
    ctx_rows: list[list[float]],
    cfg: Config,
    *,
    warmup_bars: int = 250,
) -> BacktestResult:
    """Backtest a single symbol.

    `exec_rows` and `ctx_rows` are lists of `[ts, open, high, low, close, volume]`
    (milliseconds for `ts`) on the execution and context timeframes respectively.
    """
    from .levels.detector import ohlcv_to_df

    if len(exec_rows) < warmup_bars + 5:
        raise ValueError(
            f"Need at least {warmup_bars + 5} exec bars; got {len(exec_rows)} for {symbol}"
        )

    exec_df_full = ohlcv_to_df(exec_rows)
    ctx_df_full = ohlcv_to_df(ctx_rows)

    broker = PaperBroker(
        starting_equity=cfg.risk.account_equity,
        cfg=cfg.broker,
        risk=cfg.risk,
    )
    equity_curve: list[tuple[float, float]] = []
    peak_equity = broker.equity()
    max_dd = 0.0
    kill_switch = False
    last_close_bar = -10**9
    counted_close_ids: set[str] = set()

    for i in range(warmup_bars, len(exec_df_full)):
        exec_slice = exec_df_full.iloc[: i + 1]
        exec_ts = exec_slice["ts"].iloc[-1]
        ctx_end = _align_context_index(exec_ts, ctx_df_full)
        if ctx_end < 0:
            continue
        ctx_slice = ctx_df_full.iloc[: ctx_end + 1]

        htf_levels = detect_levels(ctx_slice, cfg.levels)
        levels = detect_levels(exec_slice, cfg.levels, htf_levels=htf_levels)

        # 1) Simulate the next bar's price action against any currently-open positions.
        if i + 1 < len(exec_df_full):
            next_bar = exec_df_full.iloc[i + 1]
            ts_ms = float(next_bar["ts"].value // 10**6)
            # For longs we feed low first (pessimistic: SL hits before TP in the same bar);
            # for shorts we feed high first. We also feed `open`/`close` so trailing/BE
            # get the full intrabar movement.
            for pos in broker.open_positions():
                seq = [float(next_bar["open"])]
                if pos.side == "long":
                    seq += [float(next_bar["low"]), float(next_bar["high"]), float(next_bar["close"])]
                else:
                    seq += [float(next_bar["high"]), float(next_bar["low"]), float(next_bar["close"])]
                for p in seq:
                    broker.on_price(pos.symbol, p, ts_ms)
                    if pos.closed:
                        break

        # 2) Equity tracking + kill switch.
        eq = broker.equity()
        equity_curve.append((float(exec_ts.value // 10**6), eq))
        if eq > peak_equity:
            peak_equity = eq
        if peak_equity > 0:
            dd = (peak_equity - eq) / peak_equity
            if dd > max_dd:
                max_dd = dd
            if cfg.risk.max_drawdown_pct > 0 and dd >= cfg.risk.max_drawdown_pct:
                kill_switch = True

        # 3) Cooldown after trade close.
        bar_index = i
        if bar_index - last_close_bar < cfg.signals.cooldown_bars:
            continue

        # 4) Generate signal on this just-closed bar.
        signal = generate_signal(
            symbol, exec_slice, ctx_slice, levels, cfg.signals, risk=cfg.risk
        )
        if signal is None:
            continue

        # 5) Position management rules.
        if kill_switch:
            continue
        open_syms = {p.symbol for p in broker.open_positions()}
        if len(open_syms) >= cfg.risk.max_concurrent_positions:
            continue
        if signal.symbol in open_syms:
            continue

        plan = plan_position(
            equity=broker.equity(),
            side=signal.side,
            entry=signal.entry,
            stop=signal.stop,
            take_profit=signal.take_profit,
            cfg=cfg.risk,
        )
        if plan is None:
            continue

        broker.open(
            symbol=signal.symbol,
            side=plan.side,
            entry=plan.entry,
            stop=plan.stop,
            take_profit=plan.take_profit,
            quantity=plan.quantity,
            reason=f"{signal.kind}:{signal.reason}",
        )

        # Remember the bar a trade closed on so cooldown is measured from closure.
        for p in broker.closed_positions():
            if p.id in counted_close_ids:
                continue
            counted_close_ids.add(p.id)
            last_close_bar = bar_index

    closed = broker.closed_positions()
    wins = [p for p in closed if p.realized_pnl > 0]
    losses = [p for p in closed if p.realized_pnl <= 0]
    total_pnl = sum(p.realized_pnl for p in closed)
    gross_win = sum(p.realized_pnl for p in wins)
    gross_loss = -sum(p.realized_pnl for p in losses)
    pf = (
        gross_win / gross_loss
        if gross_loss > 0
        else float("inf") if gross_win > 0 else 0.0
    )
    winrate = (len(wins) / len(closed)) if closed else 0.0

    return BacktestResult(
        symbol=symbol,
        bars=len(exec_df_full) - warmup_bars,
        trades=len(closed),
        wins=len(wins),
        losses=len(losses),
        winrate=winrate,
        total_pnl=total_pnl,
        profit_factor=pf,
        max_drawdown=max_dd,
        final_equity=broker.equity(),
        equity_curve=equity_curve,
        positions=closed,
    )


def aggregate(results: list[BacktestResult]) -> dict:
    """Aggregate per-symbol backtests into a single summary."""
    if not results:
        return {"symbols": 0, "trades": 0}
    total_trades = sum(r.trades for r in results)
    total_wins = sum(r.wins for r in results)
    total_pnl = sum(r.total_pnl for r in results)
    avg_winrate = sum(r.winrate for r in results) / len(results)
    max_dd = max((r.max_drawdown for r in results), default=0.0)
    return {
        "symbols": len(results),
        "trades": total_trades,
        "wins": total_wins,
        "total_pnl": round(total_pnl, 4),
        "avg_winrate": round(avg_winrate, 4),
        "max_drawdown": round(max_dd, 4),
    }
