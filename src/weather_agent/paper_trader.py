"""Paper trading engine — simulates betting on Polymarket weather markets.

Scans markets periodically, places virtual bets on the best signals,
tracks resolved outcomes, and produces a P&L report.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import aiohttp

from weather_agent.agent import run_scan
from weather_agent.strategy import BetSignal, filter_safe

GAMMA_BASE = "https://gamma-api.polymarket.com"
LOG_PATH = Path.home() / "paper_trades.jsonl"
REPORT_PATH = Path.home() / "paper_report.json"


@dataclass
class PaperBet:
    """One virtual bet placed by the paper trader."""

    bet_id: int
    timestamp: str  # ISO
    city: str
    event_date: str
    bucket_question: str
    market_id: str
    side: str  # YES or NO
    entry_price: float  # price paid per share (0-1)
    stake: float  # USD wagered
    shares: float  # stake / entry_price
    estimated_probability: float
    edge: float
    forecast_temp: float
    bucket_temp_low: float | None
    bucket_temp_high: float | None
    event_title: str
    # filled after resolution
    resolved: bool = False
    won: bool | None = None
    payout: float = 0.0  # USD received back
    profit: float = 0.0  # payout - stake
    resolution_time: str | None = None


@dataclass
class PaperWallet:
    """Virtual wallet state."""

    initial_balance: float = 200.0
    balance: float = 200.0
    total_wagered: float = 0.0
    total_payout: float = 0.0
    bets: list[PaperBet] = field(default_factory=list)
    scan_count: int = 0
    start_time: str = ""
    max_bet_size: float = 10.0  # max USD per bet
    bet_fraction: float = 0.05  # 5% of balance per bet

    def place_bet(self, signal: BetSignal) -> PaperBet | None:
        """Place a virtual bet on a signal. Returns the bet or None if skipped."""
        # Skip if we already have an active bet on this market+side
        for b in self.bets:
            if b.market_id == signal.market_id and b.side == signal.side and not b.resolved:
                return None

        stake = min(self.balance * self.bet_fraction, self.max_bet_size)
        if stake < 0.50 or self.balance < stake:
            return None

        price = signal.market_price
        if price <= 0.01 or price >= 0.99:
            return None

        shares = stake / price
        self.balance -= stake
        self.total_wagered += stake

        bet = PaperBet(
            bet_id=len(self.bets) + 1,
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            city=signal.city,
            event_date=signal.event_date,
            bucket_question=signal.bucket_question,
            market_id=signal.market_id,
            side=signal.side,
            entry_price=price,
            stake=round(stake, 2),
            shares=round(shares, 4),
            estimated_probability=signal.estimated_probability,
            edge=signal.edge,
            forecast_temp=signal.forecast_temp,
            bucket_temp_low=signal.bucket_temp_low,
            bucket_temp_high=signal.bucket_temp_high,
            event_title=signal.event_title,
        )
        self.bets.append(bet)
        return bet

    def resolve_bet(self, bet: PaperBet, won: bool) -> None:
        """Resolve a bet with outcome."""
        bet.resolved = True
        bet.won = won
        bet.resolution_time = datetime.datetime.now(datetime.UTC).isoformat()
        if won:
            bet.payout = round(bet.shares * 1.0, 2)  # $1 per share if won
        else:
            bet.payout = 0.0
        bet.profit = round(bet.payout - bet.stake, 2)
        self.balance += bet.payout
        self.total_payout += bet.payout

    def stats(self) -> dict:
        """Generate summary statistics."""
        resolved = [b for b in self.bets if b.resolved]
        wins = [b for b in resolved if b.won]
        losses = [b for b in resolved if not b.won]
        pending = [b for b in self.bets if not b.resolved]

        total_profit = sum(b.profit for b in resolved)
        roi = (total_profit / self.total_wagered * 100) if self.total_wagered > 0 else 0

        return {
            "initial_balance": self.initial_balance,
            "current_balance": round(self.balance, 2),
            "total_wagered": round(self.total_wagered, 2),
            "total_payout": round(self.total_payout, 2),
            "total_profit": round(total_profit, 2),
            "roi_percent": round(roi, 1),
            "total_bets": len(self.bets),
            "resolved": len(resolved),
            "wins": len(wins),
            "losses": len(losses),
            "pending": len(pending),
            "win_rate": round(len(wins) / len(resolved) * 100, 1) if resolved else 0,
            "scans_completed": self.scan_count,
            "start_time": self.start_time,
            "best_bet": max((b.profit for b in resolved), default=0),
            "worst_bet": min((b.profit for b in resolved), default=0),
        }


async def _check_resolution(bet: PaperBet) -> bool | None:
    """Check if a market has resolved. Returns True/False/None (not yet)."""
    url = f"{GAMMA_BASE}/markets/{bet.market_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        if not data.get("resolved", False):
            return None

        prices_raw = data.get("outcomePrices", '["0.5","0.5"]')
        if isinstance(prices_raw, str):
            prices = json.loads(prices_raw)
        else:
            prices = prices_raw

        yes_price = float(prices[0]) if prices else 0.5

        if bet.side == "YES":
            return yes_price > 0.5
        else:
            return yes_price < 0.5
    except Exception:
        return None


def _log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _append_log(entry: dict) -> None:
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


async def run_paper_trading(
    *,
    duration_hours: float = 24.0,
    scan_interval_min: float = 30.0,
    initial_balance: float = 200.0,
    sigma: float = 2.0,
    min_edge_pct: float = 5.0,
    safe_only: bool = True,
    min_prob: float = 0.70,
) -> dict:
    """Run the paper trading loop for the specified duration.

    Returns the final statistics dict.
    """
    wallet = PaperWallet(
        initial_balance=initial_balance,
        balance=initial_balance,
    )
    wallet.start_time = datetime.datetime.now(datetime.UTC).isoformat()

    end_time = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=duration_hours)
    scan_interval = scan_interval_min * 60

    # Clear previous log
    LOG_PATH.write_text("")

    _log(f"Paper trading started — ${initial_balance} balance, {duration_hours}h duration")
    _log(f"Strategy: safe_only={safe_only}, min_prob={min_prob}, sigma={sigma}")
    _log(f"Scan interval: {scan_interval_min} min")
    _log("")

    while datetime.datetime.now(datetime.UTC) < end_time:
        wallet.scan_count += 1
        _log(f"=== Scan #{wallet.scan_count} ===")

        # 1. Check resolutions on pending bets
        pending = [b for b in wallet.bets if not b.resolved]
        for bet in pending:
            result = await _check_resolution(bet)
            if result is not None:
                wallet.resolve_bet(bet, won=result)
                outcome = "WON" if result else "LOST"
                _log(f"  RESOLVED: {bet.city} {bet.side} → {outcome} (profit: ${bet.profit:+.2f})")
                _append_log({
                    "type": "resolution",
                    "bet_id": bet.bet_id,
                    "city": bet.city,
                    "side": bet.side,
                    "won": result,
                    "profit": bet.profit,
                    "timestamp": bet.resolution_time,
                })

        # 2. Scan for new signals
        try:
            result = await run_scan(sigma=sigma, min_edge=min_edge_pct / 100.0, limit=200)
            signals = result.signals

            if safe_only:
                signals = filter_safe(signals, min_prob=min_prob, min_edge=min_edge_pct / 100.0)
            else:
                signals = [s for s in signals if s.is_range_bet and s.estimated_probability >= min_prob]

            _log(f"  Found {len(signals)} actionable signals (from {result.events_scanned} events)")

            # 3. Place bets on top signals
            new_bets = 0
            for sig in signals[:5]:  # max 5 new bets per scan
                bet = wallet.place_bet(sig)
                if bet is not None:
                    new_bets += 1
                    low = sig.bucket_temp_low
                    high = sig.bucket_temp_high
                    bucket = f"≤{high}" if low is None else f"≥{low}"
                    _log(
                        f"  BET #{bet.bet_id}: {bet.city} ({bet.event_date}) "
                        f"{bucket} {bet.side} @ {bet.entry_price:.1%} — "
                        f"${bet.stake} (prob={bet.estimated_probability:.0%}, edge={bet.edge:+.1%})"
                    )
                    _append_log({
                        "type": "bet",
                        "bet_id": bet.bet_id,
                        "city": bet.city,
                        "event_date": bet.event_date,
                        "side": bet.side,
                        "entry_price": bet.entry_price,
                        "stake": bet.stake,
                        "estimated_probability": bet.estimated_probability,
                        "edge": bet.edge,
                        "timestamp": bet.timestamp,
                    })

            if new_bets == 0:
                _log("  No new bets placed")

        except Exception as exc:
            _log(f"  Scan error: {exc}")

        # 4. Print wallet status
        stats = wallet.stats()
        _log(
            f"  Balance: ${stats['current_balance']:.2f} | "
            f"Bets: {stats['total_bets']} ({stats['wins']}W/{stats['losses']}L/{stats['pending']}P) | "
            f"P&L: ${stats['total_profit']:+.2f}"
        )
        _log("")

        # Wait for next scan
        remaining = (end_time - datetime.datetime.now(datetime.UTC)).total_seconds()
        wait = min(scan_interval, max(remaining, 0))
        if wait > 0:
            _log(f"Next scan in {wait/60:.0f} min...")
            await asyncio.sleep(wait)

    # Final resolution check
    _log("=== Final resolution check ===")
    pending = [b for b in wallet.bets if not b.resolved]
    for bet in pending:
        result = await _check_resolution(bet)
        if result is not None:
            wallet.resolve_bet(bet, won=result)
            outcome = "WON" if result else "LOST"
            _log(f"  RESOLVED: {bet.city} {bet.side} → {outcome} (profit: ${bet.profit:+.2f})")

    # Generate final report
    stats = wallet.stats()
    stats["end_time"] = datetime.datetime.now(datetime.UTC).isoformat()
    stats["bets_detail"] = [asdict(b) for b in wallet.bets]

    REPORT_PATH.write_text(json.dumps(stats, indent=2))
    _log("")
    _log("=" * 50)
    _log("PAPER TRADING COMPLETE")
    _log(f"  Duration: {duration_hours}h")
    _log(f"  Initial: ${stats['initial_balance']:.2f}")
    _log(f"  Final:   ${stats['current_balance']:.2f}")
    _log(f"  P&L:     ${stats['total_profit']:+.2f} ({stats['roi_percent']:+.1f}%)")
    _log(f"  Record:  {stats['wins']}W / {stats['losses']}L / {stats['pending']}P")
    _log(f"  Win Rate: {stats['win_rate']:.0f}%")
    _log(f"  Report:  {REPORT_PATH}")
    _log("=" * 50)

    return stats
