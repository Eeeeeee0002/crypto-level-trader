"""CLI entry-point for the Polymarket weather betting agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from rich.console import Console
from rich.table import Table

from weather_agent.agent import run_scan
from weather_agent.strategy import BetSignal

console = Console(stderr=True)
stdout_console = Console()


def _render_table(signals: list[BetSignal], top_n: int = 30) -> None:
    table = Table(
        title="🌤  Polymarket Weather Betting Signals",
        show_lines=True,
        title_style="bold cyan",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("City", style="bold")
    table.add_column("Date")
    table.add_column("Type")
    table.add_column("Bucket")
    table.add_column("Side", style="bold")
    table.add_column("Mkt Price", justify="right")
    table.add_column("Est. Prob", justify="right")
    table.add_column("Edge", justify="right")
    table.add_column("EV%", justify="right")
    table.add_column("Conf.")
    table.add_column("Forecast", justify="right")

    for i, sig in enumerate(signals[:top_n], 1):
        edge_color = "green" if sig.edge > 0.10 else ("yellow" if sig.edge > 0.05 else "white")
        conf_color = {"high": "green", "medium": "yellow", "low": "dim"}.get(sig.confidence, "white")

        low = sig.bucket_temp_low
        high = sig.bucket_temp_high
        if low is None and high is not None:
            bucket_str = f"≤{high:.0f}"
        elif high is None and low is not None:
            bucket_str = f"≥{low:.0f}"
        elif low is not None and high is not None:
            bucket_str = f"{low:.0f}" if low == high else f"{low:.0f}-{high:.0f}"
        else:
            bucket_str = "?"

        measure = "highest" if "ighest" in sig.event_title.lower() else "lowest"

        table.add_row(
            str(i),
            sig.city,
            sig.event_date,
            measure,
            bucket_str,
            f"[bold {'green' if sig.side == 'YES' else 'red'}]{sig.side}[/]",
            f"{sig.market_price:.1%}",
            f"{sig.estimated_probability:.1%}",
            f"[{edge_color}]{sig.edge:+.1%}[/]",
            f"[{edge_color}]{sig.expected_value:+.0%}[/]",
            f"[{conf_color}]{sig.confidence}[/]",
            f"{sig.forecast_temp:.1f}",
        )

    stdout_console.print(table)


def _render_json(signals: list[BetSignal], top_n: int = 30) -> None:
    out = []
    for sig in signals[:top_n]:
        out.append({
            "city": sig.city,
            "date": sig.event_date,
            "event": sig.event_title,
            "bucket_question": sig.bucket_question,
            "market_id": sig.market_id,
            "side": sig.side,
            "market_price": sig.market_price,
            "estimated_probability": sig.estimated_probability,
            "edge": sig.edge,
            "expected_value": sig.expected_value,
            "confidence": sig.confidence,
            "forecast_temp": sig.forecast_temp,
            "bucket_temp_low": sig.bucket_temp_low,
            "bucket_temp_high": sig.bucket_temp_high,
        })
    print(json.dumps(out, indent=2))


async def _main(args: argparse.Namespace) -> int:
    console.print("[bold cyan]Scanning Polymarket weather markets...[/]")

    result = await run_scan(
        sigma=args.sigma,
        min_edge=args.min_edge / 100.0,
        limit=args.limit,
    )

    console.print(
        f"\n[dim]Events scanned:[/] {result.events_scanned}"
        f"  [dim]With forecast:[/] {result.events_with_forecast}"
        f"  [dim]Signals:[/] {len(result.signals)}"
    )

    if result.errors:
        console.print(f"\n[yellow]Warnings ({len(result.errors)}):[/]")
        for err in result.errors[:5]:
            console.print(f"  [dim]• {err}[/]")

    if not result.signals:
        console.print("\n[yellow]No betting opportunities found with current edge threshold.[/]")
        return 0

    high = [s for s in result.signals if s.confidence == "high"]
    med = [s for s in result.signals if s.confidence == "medium"]
    console.print(
        f"\n[green]High confidence:[/] {len(high)}"
        f"  [yellow]Medium:[/] {len(med)}"
        f"  [dim]Low:[/] {len(result.signals) - len(high) - len(med)}"
    )

    if args.format == "json":
        _render_json(result.signals, top_n=args.top)
    else:
        _render_table(result.signals, top_n=args.top)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="weather-agent",
        description="Polymarket weather betting agent — compares market odds with real forecasts",
    )
    parser.add_argument(
        "--sigma", type=float, default=2.0,
        help="Forecast uncertainty in °C (default: 2.0). Controls how spread-out the probability distribution is.",
    )
    parser.add_argument(
        "--min-edge", type=float, default=5.0,
        help="Minimum edge in %% to show a signal (default: 5.0).",
    )
    parser.add_argument(
        "--top", type=int, default=30,
        help="Show top N signals (default: 30).",
    )
    parser.add_argument(
        "--limit", type=int, default=200,
        help="Max Polymarket events to fetch (default: 200).",
    )
    parser.add_argument(
        "--format", choices=["table", "json"], default="table",
        help="Output format (default: table).",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
