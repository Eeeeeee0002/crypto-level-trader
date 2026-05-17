"""CLI for the arbitrage paper trader."""

from __future__ import annotations

import argparse
import asyncio

from weather_agent.arb_paper_trader import run_arb_paper_trading


async def _main(args: argparse.Namespace) -> None:
    await run_arb_paper_trading(
        duration_hours=args.hours,
        scan_interval_min=args.interval,
        initial_balance=args.balance,
        min_gap_pct=args.min_gap,
        temp_only=args.temp_only,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="weather-arb-trade",
        description="Run arb paper trading on Polymarket.",
    )
    parser.add_argument("--hours", type=float, default=5.0, help="Duration in hours")
    parser.add_argument("--interval", type=float, default=15.0, help="Scan interval in min")
    parser.add_argument("--balance", type=float, default=100.0, help="Starting balance USD")
    parser.add_argument("--min-gap", type=float, default=2.0, help="Minimum gap %% to trade")
    parser.add_argument("--temp-only", action="store_true", help="Only temperature markets")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
