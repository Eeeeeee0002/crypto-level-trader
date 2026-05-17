"""CLI for the Polymarket arbitrage scanner."""

from __future__ import annotations

import argparse
import asyncio
import json

from rich.console import Console
from rich.table import Table

from weather_agent.arbitrage import ArbOpportunity, scan_arbitrage

console = Console(stderr=True)
stdout_console = Console()


def _render_table(opps: list[ArbOpportunity], stake: float) -> None:
    table = Table(
        title="💰  Polymarket Arbitrage Scanner",
        show_lines=True,
        title_style="bold cyan",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Event", style="bold", max_width=45)
    table.add_column("Buckets", justify="right")
    table.add_column("Direction", style="bold")
    table.add_column("Sum(YES)", justify="right")
    table.add_column("Gap", justify="right")
    table.add_column(f"Profit (${stake:.0f})", justify="right")
    table.add_column("ROI", justify="right")

    for i, opp in enumerate(opps, 1):
        trade = opp.calc_trade(stake)
        gap_color = "green" if opp.gap_pct > 10 else ("yellow" if opp.gap_pct > 3 else "white")
        dir_color = "green" if opp.direction == "BUY ALL YES" else "cyan"

        table.add_row(
            str(i),
            opp.event_title[:45],
            str(opp.num_buckets),
            f"[{dir_color}]{opp.direction}[/]",
            f"{opp.sum_yes:.4f}",
            f"[{gap_color}]{opp.gap_pct:+.1f}%[/]",
            f"[{gap_color}]${trade['guaranteed_profit']:+.2f}[/]",
            f"[{gap_color}]{trade['profit_pct']:+.1f}%[/]",
        )

    stdout_console.print(table)


def _render_json(opps: list[ArbOpportunity], stake: float) -> None:
    out = []
    for opp in opps:
        trade = opp.calc_trade(stake)
        out.append({
            "event": opp.event_title,
            "event_id": opp.event_id,
            "slug": opp.slug,
            "num_buckets": opp.num_buckets,
            "direction": opp.direction,
            "sum_yes": opp.sum_yes,
            "gap_pct": opp.gap_pct,
            "trade": trade,
            "polymarket_url": f"https://polymarket.com/event/{opp.slug}",
        })
    print(json.dumps(out, indent=2))


async def _main(args: argparse.Namespace) -> int:
    console.print("[bold cyan]Scanning Polymarket for arbitrage...[/]")

    tag = "daily-temperature" if args.temp_only else None
    opps = await scan_arbitrage(
        tag_slug=tag,
        min_gap_pct=args.min_gap,
        limit=args.limit,
    )

    console.print(f"\n[dim]Opportunities found:[/] {len(opps)}")

    if not opps:
        console.print("[yellow]No arbitrage opportunities found.[/]")
        return 0

    if args.format == "json":
        _render_json(opps, stake=args.stake)
    else:
        _render_table(opps, stake=args.stake)

        # Show top opportunity detail
        if opps:
            best = opps[0]
            trade = best.calc_trade(args.stake)
            console.print(f"\n[bold green]Best opportunity:[/] {best.event_title}")
            console.print(
                f"  Invest ${trade['total_stake']:.2f} → "
                f"guaranteed ${trade['guaranteed_payout']:.2f} back → "
                f"[bold green]${trade['guaranteed_profit']:.2f} profit ({trade['profit_pct']:.1f}%)[/]"
            )
            console.print(f"  https://polymarket.com/event/{best.slug}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="weather-arb",
        description="Scan Polymarket for arbitrage on multi-outcome events.",
    )
    parser.add_argument(
        "--stake", type=float, default=200.0,
        help="Total stake in USD for profit calculation (default: 200)",
    )
    parser.add_argument(
        "--min-gap", type=float, default=1.0,
        help="Minimum gap in %% to show (default: 1.0)",
    )
    parser.add_argument(
        "--limit", type=int, default=200,
        help="Max events to fetch per page (default: 200)",
    )
    parser.add_argument(
        "--temp-only", action="store_true",
        help="Only scan temperature markets (guaranteed mutually exclusive)",
    )
    parser.add_argument(
        "--format", choices=["table", "json"], default="table",
        help="Output format (default: table)",
    )
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
