"""CLI entry-point for the paper trading runner."""

from __future__ import annotations

import argparse
import asyncio

from weather_agent.paper_trader import run_paper_trading


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="weather-paper-trade",
        description="Paper trading bot for Polymarket weather markets. "
        "Runs for N hours, auto-scanning and placing virtual bets.",
    )
    parser.add_argument(
        "--hours", type=float, default=24.0,
        help="How long to run (default: 24 hours)",
    )
    parser.add_argument(
        "--interval", type=float, default=30.0,
        help="Minutes between scans (default: 30)",
    )
    parser.add_argument(
        "--balance", type=float, default=200.0,
        help="Starting virtual balance in USD (default: 200)",
    )
    parser.add_argument(
        "--sigma", type=float, default=2.0,
        help="Forecast uncertainty in °C (default: 2.0)",
    )
    parser.add_argument(
        "--min-edge", type=float, default=5.0,
        help="Minimum edge %% (default: 5.0)",
    )
    parser.add_argument(
        "--min-prob", type=float, default=0.70,
        help="Minimum estimated probability (default: 0.70)",
    )
    parser.add_argument(
        "--all-bets", action="store_true",
        help="Include all bet types, not just safe range bets",
    )

    args = parser.parse_args()

    print("=" * 50)
    print("POLYMARKET WEATHER PAPER TRADER")
    print("=" * 50)
    print(f"Balance:  ${args.balance}")
    print(f"Duration: {args.hours}h")
    print(f"Interval: {args.interval} min")
    print(f"Strategy: {'ALL bets' if args.all_bets else 'SAFE range bets only'}")
    print(f"Min prob: {args.min_prob:.0%}")
    print(f"Min edge: {args.min_edge}%")
    print("=" * 50)
    print()

    asyncio.run(run_paper_trading(
        duration_hours=args.hours,
        scan_interval_min=args.interval,
        initial_balance=args.balance,
        sigma=args.sigma,
        min_edge_pct=args.min_edge,
        safe_only=not args.all_bets,
        min_prob=args.min_prob,
    ))


if __name__ == "__main__":
    main()
