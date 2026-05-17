"""Arbitrage paper trading engine.

Scans ALL Polymarket for arb opportunities on mutually exclusive events.
Places virtual "buy all YES" trades when gap > threshold.
Tracks resolutions and produces a P&L report.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import aiohttp

from weather_agent.arbitrage import ArbOpportunity, scan_arbitrage

GAMMA_BASE = "https://gamma-api.polymarket.com"
LOG_PATH = Path.home() / "arb_trades.jsonl"
REPORT_PATH = Path.home() / "arb_report.json"


@dataclass
class ArbTrade:
    """One complete arb trade (buying YES on all buckets of an event)."""

    trade_id: int
    timestamp: str
    event_title: str
    event_id: str
    slug: str
    num_buckets: int
    sum_yes: float
    gap_pct: float
    total_stake: float
    guaranteed_payout: float
    guaranteed_profit: float
    expected_roi_pct: float
    bucket_prices: list[dict] = field(default_factory=list)
    resolved: bool = False
    actual_payout: float = 0.0
    actual_profit: float = 0.0
    resolution_time: str | None = None
    winning_bucket: str | None = None


@dataclass
class ArbWallet:
    """Virtual wallet for arb paper trading."""

    initial_balance: float = 100.0
    balance: float = 100.0
    total_wagered: float = 0.0
    total_payout: float = 0.0
    trades: list[ArbTrade] = field(default_factory=list)
    scan_count: int = 0
    start_time: str = ""
    max_trade_size: float = 20.0
    trade_fraction: float = 0.15

    def place_arb(self, opp: ArbOpportunity) -> ArbTrade | None:
        """Place a virtual arb trade. Returns trade or None if skipped."""
        # Skip if already have an active trade on this event
        for t in self.trades:
            if t.event_id == opp.event_id and not t.resolved:
                return None

        stake = min(self.balance * self.trade_fraction, self.max_trade_size)
        if stake < 1.0 or self.balance < stake:
            return None

        trade_info = opp.calc_trade(stake)
        self.balance -= stake
        self.total_wagered += stake

        trade = ArbTrade(
            trade_id=len(self.trades) + 1,
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            event_title=opp.event_title,
            event_id=opp.event_id,
            slug=opp.slug,
            num_buckets=opp.num_buckets,
            sum_yes=opp.sum_yes,
            gap_pct=opp.gap_pct,
            total_stake=round(stake, 2),
            guaranteed_payout=trade_info["guaranteed_payout"],
            guaranteed_profit=trade_info["guaranteed_profit"],
            expected_roi_pct=trade_info["profit_pct"],
            bucket_prices=[
                {"question": b.question[:70], "market_id": b.market_id, "yes": b.yes_price}
                for b in opp.buckets
            ],
        )
        self.trades.append(trade)
        return trade

    def resolve_trade(self, trade: ArbTrade, payout: float, winning_q: str) -> None:
        """Resolve a trade with actual outcome."""
        trade.resolved = True
        trade.resolution_time = datetime.datetime.now(datetime.UTC).isoformat()
        trade.actual_payout = round(payout, 2)
        trade.actual_profit = round(payout - trade.total_stake, 2)
        trade.winning_bucket = winning_q
        self.balance += payout
        self.total_payout += payout

    def stats(self) -> dict:
        """Generate summary statistics."""
        resolved = [t for t in self.trades if t.resolved]
        wins = [t for t in resolved if t.actual_profit > 0]
        losses = [t for t in resolved if t.actual_profit <= 0]
        pending = [t for t in self.trades if not t.resolved]

        total_profit = sum(t.actual_profit for t in resolved)
        roi = (total_profit / self.total_wagered * 100) if self.total_wagered > 0 else 0

        # Pending expected profit
        pending_expected = sum(t.guaranteed_profit for t in pending)

        return {
            "initial_balance": self.initial_balance,
            "current_balance": round(self.balance, 2),
            "total_wagered": round(self.total_wagered, 2),
            "total_payout": round(self.total_payout, 2),
            "total_profit": round(total_profit, 2),
            "pending_expected_profit": round(pending_expected, 2),
            "roi_percent": round(roi, 1),
            "total_trades": len(self.trades),
            "resolved": len(resolved),
            "wins": len(wins),
            "losses": len(losses),
            "pending": len(pending),
            "win_rate": round(len(wins) / len(resolved) * 100, 1) if resolved else 0,
            "scans_completed": self.scan_count,
            "start_time": self.start_time,
            "best_trade": max((t.actual_profit for t in resolved), default=0),
            "worst_trade": min((t.actual_profit for t in resolved), default=0),
        }


async def _check_arb_resolution(trade: ArbTrade) -> tuple[float, str] | None:
    """Check if an arb trade's event has resolved.

    Returns (payout, winning_question) or None if not resolved yet.
    For arb trades, we bought YES on all buckets. When the event resolves,
    exactly one bucket's YES = $1, all others = $0.
    Payout = (stake / sum_yes) * 1.0 = guaranteed_payout.
    """
    try:
        async with aiohttp.ClientSession() as session:
            for bp in trade.bucket_prices:
                url = f"{GAMMA_BASE}/markets/{bp['market_id']}"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

                if data.get("resolved", False):
                    prices_raw = data.get("outcomePrices", '["0.5","0.5"]')
                    if isinstance(prices_raw, str):
                        prices = json.loads(prices_raw)
                    else:
                        prices = prices_raw
                    yes_p = float(prices[0]) if prices else 0
                    if yes_p > 0.5:
                        # This bucket won — arb pays out
                        return (trade.guaranteed_payout, bp["question"])

                await asyncio.sleep(0.1)

            # Check if ANY bucket resolved to see if event is done
            # If some resolved but none won, event might still be pending
            any_resolved = False
            async with aiohttp.ClientSession() as session:
                for bp in trade.bucket_prices[:1]:
                    url = f"{GAMMA_BASE}/markets/{bp['market_id']}"
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        if data.get("resolved", False):
                            any_resolved = True

            if any_resolved:
                # Event resolved but no winner found among our buckets
                # This shouldn't happen for mutually exclusive events
                return (0.0, "NONE (unexpected)")

    except Exception:
        pass
    return None


def _log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _append_log(entry: dict) -> None:
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()


async def run_arb_paper_trading(
    *,
    duration_hours: float = 5.0,
    scan_interval_min: float = 15.0,
    initial_balance: float = 100.0,
    min_gap_pct: float = 2.0,
    temp_only: bool = False,
) -> dict:
    """Run the arb paper trading loop.

    Scans Polymarket for arbitrage, places virtual trades, tracks P&L.
    """
    wallet = ArbWallet(
        initial_balance=initial_balance,
        balance=initial_balance,
    )
    wallet.start_time = datetime.datetime.now(datetime.UTC).isoformat()

    end_time = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=duration_hours)
    scan_interval = scan_interval_min * 60

    LOG_PATH.write_text("")

    _log(f"ARB Paper Trading started — ${initial_balance} balance, {duration_hours}h")
    _log(f"Strategy: min_gap={min_gap_pct}%, temp_only={temp_only}")
    _log(f"Scan interval: {scan_interval_min} min")
    _log("")

    while datetime.datetime.now(datetime.UTC) < end_time:
        wallet.scan_count += 1
        _log(f"=== Scan #{wallet.scan_count} ===")

        # 1. Check resolutions
        pending = [t for t in wallet.trades if not t.resolved]
        for trade in pending:
            result = await _check_arb_resolution(trade)
            if result is not None:
                payout, winner = result
                wallet.resolve_trade(trade, payout, winner)
                _log(
                    f"  RESOLVED: {trade.event_title[:50]} → "
                    f"payout ${payout:.2f}, profit ${trade.actual_profit:+.2f}"
                )
                _append_log({
                    "type": "resolution",
                    "trade_id": trade.trade_id,
                    "event": trade.event_title,
                    "payout": payout,
                    "profit": trade.actual_profit,
                    "winner": winner,
                    "timestamp": trade.resolution_time,
                })

        # 2. Scan for arb opportunities
        try:
            tag = "daily-temperature" if temp_only else None
            opps = await scan_arbitrage(
                tag_slug=tag,
                min_gap_pct=min_gap_pct,
                limit=100,
                max_pages=20,
            )

            _log(f"  Found {len(opps)} arb opportunities")

            # 3. Place trades on best opportunities
            new_trades = 0
            for opp in opps[:8]:
                trade = wallet.place_arb(opp)
                if trade is not None:
                    new_trades += 1
                    _log(
                        f"  TRADE #{trade.trade_id}: {trade.event_title[:50]} "
                        f"({trade.num_buckets} buckets) — ${trade.total_stake} "
                        f"(gap={trade.gap_pct:+.1f}%, exp profit=${trade.guaranteed_profit:+.2f})"
                    )
                    _append_log({
                        "type": "trade",
                        "trade_id": trade.trade_id,
                        "event": trade.event_title,
                        "slug": trade.slug,
                        "num_buckets": trade.num_buckets,
                        "sum_yes": trade.sum_yes,
                        "gap_pct": trade.gap_pct,
                        "stake": trade.total_stake,
                        "guaranteed_payout": trade.guaranteed_payout,
                        "guaranteed_profit": trade.guaranteed_profit,
                        "timestamp": trade.timestamp,
                    })

            if new_trades == 0:
                _log("  No new trades placed")

        except Exception as exc:
            _log(f"  Scan error: {exc}")

        # 4. Print wallet status
        stats = wallet.stats()
        _log(
            f"  Balance: ${stats['current_balance']:.2f} | "
            f"Trades: {stats['total_trades']} "
            f"({stats['wins']}W/{stats['losses']}L/{stats['pending']}P) | "
            f"P&L: ${stats['total_profit']:+.2f} | "
            f"Pending exp: ${stats['pending_expected_profit']:+.2f}"
        )
        _log("")

        # Wait for next scan
        remaining = (end_time - datetime.datetime.now(datetime.UTC)).total_seconds()
        wait = min(scan_interval, max(remaining, 0))
        if wait > 0:
            _log(f"Next scan in {wait / 60:.0f} min...")
            await asyncio.sleep(wait)

    # Final resolution check
    _log("=== Final resolution check ===")
    pending = [t for t in wallet.trades if not t.resolved]
    for trade in pending:
        result = await _check_arb_resolution(trade)
        if result is not None:
            payout, winner = result
            wallet.resolve_trade(trade, payout, winner)
            _log(
                f"  RESOLVED: {trade.event_title[:50]} → "
                f"profit ${trade.actual_profit:+.2f}"
            )

    # Generate report
    stats = wallet.stats()
    stats["end_time"] = datetime.datetime.now(datetime.UTC).isoformat()
    stats["trades_detail"] = [asdict(t) for t in wallet.trades]

    REPORT_PATH.write_text(json.dumps(stats, indent=2))
    _log("")
    _log("=" * 60)
    _log("ARB PAPER TRADING COMPLETE")
    _log(f"  Duration: {duration_hours}h")
    _log(f"  Initial:  ${stats['initial_balance']:.2f}")
    _log(f"  Final:    ${stats['current_balance']:.2f}")
    _log(f"  P&L:      ${stats['total_profit']:+.2f} ({stats['roi_percent']:+.1f}%)")
    _log(f"  Record:   {stats['wins']}W / {stats['losses']}L / {stats['pending']}P")
    _log(f"  Pending expected profit: ${stats['pending_expected_profit']:+.2f}")
    _log(f"  Report:   {REPORT_PATH}")
    _log("=" * 60)

    return stats
