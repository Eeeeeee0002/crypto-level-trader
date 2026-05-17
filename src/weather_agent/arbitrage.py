"""Polymarket arbitrage scanner.

Scans ALL multi-outcome events for mispricing.
For truly mutually exclusive events (exactly one outcome wins),
if sum(YES prices) < 1.0, buying YES on every bucket guarantees profit.

IMPORTANT: Only works on mutually exclusive events. The scanner
auto-detects exclusivity by checking question patterns:
  ✅ Temperature buckets, exact count ranges, "or below"/"or above"
  ❌ Cumulative ("reach at least X"), multi-winner ("which clubs")
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field

import aiohttp

GAMMA_BASE = "https://gamma-api.polymarket.com"

# Patterns in questions that indicate CUMULATIVE (not exclusive) outcomes
_CUMULATIVE_Q = re.compile(
    r"reach|at least|hit \d|go above|go below|"
    r"fdv above|cap above|above ___",
    re.IGNORECASE,
)

# Patterns in event titles that indicate multi-winner (not exclusive)
_MULTI_WINNER_TITLE = re.compile(
    r"which clubs|which ceo|who will attend|which artist|"
    r"who visited|who will testify|which compan|which maps|"
    r"who will .* purge|which country will join|"
    r"will .* launch a token by|by \.\.\.|by ___",
    re.IGNORECASE,
)

# Patterns in questions that indicate exclusive range buckets
_EXCLUSIVE_Q = re.compile(
    r"between|less than|or more|or higher|or fewer|"
    r"or lower|or below|or above|will .* be \d",
    re.IGNORECASE,
)


def _is_mutually_exclusive(title: str, questions: list[str]) -> bool:
    """Heuristic: is this event truly mutually exclusive (exactly 1 wins)?"""
    if _MULTI_WINNER_TITLE.search(title):
        return False

    # Check if questions have cumulative patterns
    cumulative_count = sum(1 for q in questions if _CUMULATIVE_Q.search(q))
    if cumulative_count > len(questions) * 0.3:
        return False

    # Temperature markets are always exclusive
    if "temperature" in title.lower():
        return True

    # Range/bucket patterns indicate exclusivity
    range_count = sum(1 for q in questions if _EXCLUSIVE_Q.search(q))
    if range_count >= len(questions) * 0.5:
        return True

    # "How many X" with count buckets
    if re.search(r"how many", title, re.IGNORECASE):
        return True

    # Single-winner events: "winner", "MVP", "nominee"
    if re.search(r"winner|mvp|nominee|next .* (actor|director)", title, re.IGNORECASE):
        return True

    # IPO closing market cap (range buckets)
    if re.search(r"closing market cap|ipo.*cap", title, re.IGNORECASE):
        return True

    # Turnout percentages (range buckets)
    if "turnout" in title.lower():
        return True

    # Exchange rate (range buckets)
    if "exchange rate" in title.lower():
        return True

    # Earthquake counts
    if "earthquake" in title.lower():
        return True

    return False


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
    direction: str  # "BUY ALL YES"
    cost_per_share: float
    guaranteed_payout: float  # $1 for YES arb
    profit_per_dollar: float
    buckets: list[ArbBucket] = field(default_factory=list)

    def calc_trade(self, total_stake: float) -> dict:
        """Calculate exact trade: how much to spend on each bucket."""
        sets = total_stake / self.sum_yes
        trades = []
        for b in self.buckets:
            spend = b.yes_price * sets
            trades.append({
                "question": b.question[:70],
                "market_id": b.market_id,
                "side": "YES",
                "price": b.yes_price,
                "spend": round(spend, 4),
                "shares": round(sets, 4),
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


async def scan_arbitrage(
    *,
    tag_slug: str | None = None,
    min_gap_pct: float = 1.0,
    limit: int = 100,
    max_pages: int = 20,
) -> list[ArbOpportunity]:
    """Scan Polymarket for arbitrage on mutually exclusive events.

    Only returns BUY ALL YES opportunities where sum(YES) < 1.0
    on verified mutually exclusive multi-outcome events.
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
        for page in range(max_pages):
            p = {**params, "offset": page * limit}
            async with session.get(url, params=p) as resp:
                if resp.status != 200:
                    break
                batch: list[dict] = await resp.json()
                if not batch:
                    break
                all_events.extend(batch)
            await asyncio.sleep(0.2)

    opportunities: list[ArbOpportunity] = []

    for ev in all_events:
        markets = ev.get("markets", [])
        active = [m for m in markets if not m.get("closed", False)]
        if len(active) < 3:
            continue

        title = ev.get("title", "")
        questions = [m.get("question", "") for m in active]

        if not _is_mutually_exclusive(title, questions):
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

        if sum_yes >= 1.0 or sum_yes < 0.3:
            continue

        gap = 1.0 - sum_yes
        gap_pct = gap * 100

        if gap_pct < min_gap_pct:
            continue

        opportunities.append(ArbOpportunity(
            event_title=title,
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

    opportunities.sort(key=lambda x: -x.gap_pct)
    return opportunities
