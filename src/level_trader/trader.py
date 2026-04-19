"""Main orchestrator: ties data feed, levels, signals, risk, and broker together."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .broker.base import Broker, Position
from .config import Config
from .exchange.base import ExchangeClient
from .exchange.universe import select_universe
from .journal import Journal, summarize
from .levels.detector import Level, detect_levels, ohlcv_to_df
from .risk.sizing import plan_position
from .signals.engine import Signal, generate_signal

log = logging.getLogger(__name__)


@dataclass
class SymbolState:
    symbol: str
    last_bar_ts: int = 0
    last_trade_close_bar: int = -10**9
    levels_exec: list[Level] = None  # type: ignore[assignment]
    levels_context: list[Level] = None  # type: ignore[assignment]


class Trader:
    def __init__(self, exchange: ExchangeClient, broker: Broker, config: Config) -> None:
        self.exchange = exchange
        self.broker = broker
        self.config = config
        self.journal = Journal(config.logging.trade_log, config.logging.equity_log)
        self.state: dict[str, SymbolState] = {}
        self.symbols: list[str] = []
        self._peak_equity: float = float(broker.equity())
        self._kill_switch: bool = False

    # -- Bootstrapping -----------------------------------------------------

    def setup(self) -> None:
        self.symbols = select_universe(self.exchange, self.config.universe)
        for s in self.symbols:
            self.state[s] = SymbolState(symbol=s)
        self.refresh_levels(force=True)

    def refresh_levels(self, force: bool = False) -> None:
        tf = self.config.timeframes
        lv_cfg = self.config.levels
        for s in self.symbols:
            try:
                ctx = self.exchange.fetch_ohlcv(s, tf.context, limit=tf.lookback_bars)
                exec_rows = self.exchange.fetch_ohlcv(s, tf.execution, limit=tf.lookback_bars)
            except Exception as e:  # noqa: BLE001
                log.warning("OHLCV fetch failed for %s: %s", s, e)
                continue
            ctx_df = ohlcv_to_df(ctx)
            exec_df = ohlcv_to_df(exec_rows)
            htf_levels = detect_levels(ctx_df, lv_cfg)
            exec_levels = detect_levels(exec_df, lv_cfg, htf_levels=htf_levels)
            st = self.state[s]
            st.levels_context = htf_levels
            st.levels_exec = exec_levels
            if force:
                log.info(
                    "%s: %d HTF levels, %d exec levels (strongest=%.6g)",
                    s,
                    len(htf_levels),
                    len(exec_levels),
                    exec_levels[0].price if exec_levels else float("nan"),
                )

    # -- Per-iteration work -----------------------------------------------

    def _process_symbol(self, symbol: str) -> Signal | None:
        tf = self.config.timeframes
        try:
            exec_rows = self.exchange.fetch_ohlcv(symbol, tf.execution, limit=max(tf.lookback_bars, 300))
            ctx_rows = self.exchange.fetch_ohlcv(symbol, tf.context, limit=max(tf.lookback_bars, 300))
        except Exception as e:  # noqa: BLE001
            log.warning("OHLCV fetch failed for %s: %s", symbol, e)
            return None

        exec_df = ohlcv_to_df(exec_rows)
        ctx_df = ohlcv_to_df(ctx_rows)
        if exec_df.empty:
            return None

        last_bar_ts = int(exec_rows[-1][0])
        st = self.state[symbol]
        # Only act on a newly closed bar.
        if st.last_bar_ts == last_bar_ts:
            return None
        st.last_bar_ts = last_bar_ts

        # Refresh levels on each closed bar (cheap; keeps things adaptive).
        htf = detect_levels(ctx_df, self.config.levels)
        lv = detect_levels(exec_df, self.config.levels, htf_levels=htf)
        st.levels_context = htf
        st.levels_exec = lv

        bar_index = len(exec_df) - 1
        if bar_index - st.last_trade_close_bar < self.config.signals.cooldown_bars:
            return None

        return generate_signal(
            symbol, exec_df, ctx_df, lv, self.config.signals, risk=self.config.risk
        )

    def _maybe_open(self, signal: Signal) -> None:
        # Max drawdown kill switch.
        if self._kill_switch:
            log.info("Kill switch engaged (max drawdown); ignoring %s", signal.symbol)
            return
        # Capacity check.
        if len(self.broker.open_positions()) >= self.config.risk.max_concurrent_positions:
            log.info("Max concurrent positions reached; skipping signal on %s", signal.symbol)
            return
        # One position per symbol.
        if any(p.symbol == signal.symbol for p in self.broker.open_positions()):
            return

        plan = plan_position(
            equity=self.broker.equity(),
            side=signal.side,
            entry=signal.entry,
            stop=signal.stop,
            take_profit=signal.take_profit,
            cfg=self.config.risk,
        )
        if plan is None:
            log.info("Could not size position for %s: %s", signal.symbol, signal.reason)
            return

        pos = self.broker.open(
            symbol=signal.symbol,
            side=plan.side,
            entry=plan.entry,
            stop=plan.stop,
            take_profit=plan.take_profit,
            quantity=plan.quantity,
            reason=f"{signal.kind}: {signal.reason} | rr={signal.rr:.2f} lev={plan.leverage_used:.2f}x",
        )
        self.journal.record_equity(time.time(), self.broker.equity())
        # pos is recorded to trade log only when it closes.
        _ = pos

    def _price_tick(self) -> None:
        """Mark-to-market all open positions using latest tickers."""
        positions = self.broker.open_positions()
        if not positions:
            return
        syms = list({p.symbol for p in positions})
        try:
            tickers = self.exchange.fetch_tickers(syms)
        except Exception as e:  # noqa: BLE001
            log.warning("fetch_tickers failed: %s", e)
            return
        for p in positions:
            t = tickers.get(p.symbol)
            if t is None:
                continue
            closed = self.broker.on_price(p.symbol, t.last, time.time())
            for cp in closed:
                self.journal.record_trade(cp)
                st = self.state.get(cp.symbol)
                if st is not None:
                    st.last_trade_close_bar = 10**9  # force cooldown reset on next bar tick

    # -- Main loop ---------------------------------------------------------

    def step(self) -> None:
        self._price_tick()
        # Track peak equity and engage the kill switch once configured drawdown is hit.
        eq = self.broker.equity()
        if eq > self._peak_equity:
            self._peak_equity = eq
        max_dd = self.config.risk.max_drawdown_pct
        if max_dd > 0 and self._peak_equity > 0:
            dd = (self._peak_equity - eq) / self._peak_equity
            if dd >= max_dd and not self._kill_switch:
                self._kill_switch = True
                log.warning(
                    "KILL SWITCH engaged: drawdown %.2f%% >= %.2f%% (peak=%.2f, now=%.2f)",
                    dd * 100,
                    max_dd * 100,
                    self._peak_equity,
                    eq,
                )
        for s in self.symbols:
            sig = self._process_symbol(s)
            if sig is not None:
                log.info("SIGNAL %s %s %s @ %.6g rr=%.2f — %s",
                         sig.symbol, sig.kind, sig.side, sig.entry, sig.rr, sig.reason)
                self._maybe_open(sig)
        self.journal.record_equity(time.time(), self.broker.equity())

    def run(self, max_iterations: int | None = None) -> dict:
        """Run the agent until stopped (or `max_iterations` steps have passed)."""
        self.setup()
        iteration = 0
        poll = self.config.broker.poll_seconds
        log.info("Trader started with %d symbols, poll=%ss", len(self.symbols), poll)
        try:
            while True:
                self.step()
                iteration += 1
                if max_iterations is not None and iteration >= max_iterations:
                    break
                time.sleep(poll)
        except KeyboardInterrupt:
            log.info("Interrupted by user")

        positions: list[Position] = []
        if hasattr(self.broker, "closed_positions"):
            positions.extend(self.broker.closed_positions())  # type: ignore[attr-defined]
        positions.extend(self.broker.open_positions())
        return {
            "iterations": iteration,
            "equity": self.broker.equity(),
            "open_positions": len(self.broker.open_positions()),
            "summary": summarize(positions),
        }
