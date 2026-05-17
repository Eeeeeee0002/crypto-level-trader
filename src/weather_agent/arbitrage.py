"""Polymarket arbitrage scanner.

Scans multi-outcome events where exactly one outcome wins.
If sum(YES prices) < 1.0, buying YES on every bucket guarantees a profit.
If sum(YES prices) > 1.0, buying NO on every bucket guarantees a profit.

Temperature markets are ideal: the temperature falls in exactly one bucket,
so these are truly mutually exclusive outcomes.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import aiohttp

GAMMA_BASE = "https://gamma-api.polymarket.com"


@dataclass
class ArbBucket:
    """One bucket in a multi-outcome event."""

    question: str
    market_id: str
    yes_price: float
    no_price: float


@dataclass
class ArbOpportunity:
    """An arbitrage opportunity on a multi-outcome event."""

    event_title: str
    event_id: str
    slug: str
    num_buckets: int
    sum_yes: float
    gap: float  # 1.0 - sum_yes (positive = buy all YES)
    gap_pct: float
    direction: str  # "BUY ALL YES" or "BUY ALL NO"
    cost_per_share: float  # total cost to buy 1 share of each outcome
    guaranteed_payout: float  # $1 for YES arb, $(N-1) for NO arb
    profit_per_dollar: float  # guaranteed_payout / cost - 1
    buckets: list[ArbBucket] = field(default_factory=list)

    def calc_trade(self, total_stake: float) -> dict:
        """Calculate exact trade: how much to spend on each bucket."""
        if self.direction == "BUY ALL YES":
            # Buy YES on every bucket, total cost = sum_yes per "set"
            sets = total_stake / self.sum_yes
            trades = []
            for b in self.buckets:
                spend = b.yes_price * sets
                shares = sets  # 1 share per set per bucket
                trades.append({
                    "question": b.question[:60],
                    "market_id": b.market_id,
                    "side": "YES",
                    "price": b.yes_price,
                    "spend": round(spend, 4),
                    "shares": round(shares, 4),
                })
            payout = sets * 1.0
            profit = payout - total_stake
            return {
                "total_stake": round(total_stake, 2),
                "sets": round(sets, 4),
                "guaranteed_payout": round(payout, 2),
                "guaranteed_profit": round(profit, 2),
                "profit_pct": round(profit / total_stake * 100, 2),
                "trades": trades,
            }
        else:
            # BUY ALL NO
            total_no = sum(b.no_price for b in self.buckets)
            sets = total_stake / total_no
            trades = []
            for b in self.buckets:
                spend = b.no_price * sets
                trades.append({
                    "question": b.question[:60],
                    "market_id": b.market_id,
                    "side": "NO",
                    "price": b.no_price,
                    "spend": round(spend, 4),
                    "shares": round(sets, 4),
                })
            payout = (len(self.buckets) - 1) * sets
            profit = payout - total_stake
            return {
                "total_stake": round(total_stake, 2),
                "sets": round(sets, 4),
                "guaranteed_payout": round(payout, 2),
                "guaranteed_profit": round(profit, 2),
                "profit_pct": round(profit / total_stake * 100, 2),
                "trades": trades,
            }


async def scan_arbitrage(
    *,
    tag_slug: str | None = None,
    min_gap_pct: float = 1.0,
    limit: int = 200,
) -> list[ArbOpportunity]:
    """Scan Polymarket for arbitrage opportunities.

    Parameters
    ----------
    tag_slug : str or None
        Filter to specific tag (e.g. "daily-temperature"). None = all events.
    min_gap_pct : float
        Minimum gap in percent to report (default 1%).
    limit : int
        Max events to fetch per API call.
    """
    url = f"{GAMMA_BASE}/events"
    params: dict[str, str | int] = {
        "closed": "false",
        "limit": limit,
    }
    if tag_slug:
        params["tag_slug"] = tag_slug

    all_events: list[dict] = []
    async with aiohttp.ClientSession() as session:
        # Fetch multiple pages
        for offset in range(0, 1000, limit):
            p = {**params, "offset": offset}
            async with session.get(url, params=p) as resp:
                if resp.status != 200:
                    break
                batch: list[dict] = await resp.json()
                if not batch:
                    break
                all_events.extend(batch)
            await asyncio.sleep(0.3)

    opportunities: list[ArbOpportunity] = []

    for ev in all_events:
        markets = ev.get("markets", [])
        active = [m for m in markets if not m.get("closed", False)]
        if len(active) < 3:
            continue

        buckets: list[ArbBucket] = []
        for m in active:
            prices_raw = m.get("outcomePrices", '["0.5","0.5"]')
            if isinstance(prices_raw, str):
                prices = json.loads(prices_raw)
            else:
                prices = prices_raw
            yes_p = float(prices[0]) if prices else 0.5
            no_p = float(prices[1]) if len(prices) > 1 else 0.5
            buckets.append(ArbBucket(
                question=m.get("question", ""),
                market_id=str(m.get("id", "")),
                yes_price=yes_p,
                no_price=no_p,
            ))

        sum_yes = sum(b.yes_price for b in buckets)
        sum_no = sum(b.no_price for b in buckets)

        # BUY ALL YES arb: cost = sum_yes, payout = $1
        if sum_yes < 1.0:
            gap = 1.0 - sum_yes
            gap_pct = gap * 100
            if gap_pct >= min_gap_pct:
                opportunities.append(ArbOpportunity(
                    event_title=ev.get("title", ""),
                    event_id=str(ev.get("id", "")),
                    slug=ev.get("slug", ""),
                    num_buckets=len(buckets),
                    sum_yes=round(sum_yes, 4),
                    gap=round(gap, 4),
                    gap_pct=round(gap_pct, 2),
                    direction="BUY ALL YES",
                    cost_per_share=round(sum_yes, 4),
                    guaranteed_payout=1.0,
                    profit_per_dollar=round(gap / sum_yes, 4) if sum_yes > 0 else 0,
                    buckets=buckets,
                ))

        # BUY ALL NO arb: cost = sum_no, payout = $(N-1)
        expected_no_payout = len(buckets) - 1
        if sum_no < expected_no_payout:
            gap = expected_no_payout - sum_no
            gap_pct = gap / sum_no * 100 if sum_no > 0 else 0
            if gap_pct >= min_gap_pct:
                opportunities.append(ArbOpportunity(
                    event_title=ev.get("title", ""),
                    event_id=str(ev.get("id", "")),
                    slug=ev.get("slug", ""),
                    num_buckets=len(buckets),
                    sum_yes=round(sum_yes, 4),
                    gap=round(gap, 4),
                    gap_pct=round(gap_pct, 2),
                    direction="BUY ALL NO",
                    cost_per_share=round(sum_no, 4),
                    guaranteed_payout=float(expected_no_payout),
                    profit_per_dollar=round(gap / sum_no, 4) if sum_no > 0 else 0,
                    buckets=buckets,
                ))

    opportunities.sort(key=lambda x: -x.gap_pct)
    return opportunities
