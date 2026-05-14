"""CLI entry-point for the live weather dashboard."""

from __future__ import annotations

import argparse

from weather_agent.dashboard import serve


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="weather-dashboard",
        description="Live dashboard for Polymarket weather betting signals. Auto-refreshes every N minutes.",
    )
    parser.add_argument("--port", type=int, default=8050, help="HTTP port (default: 8050)")
    parser.add_argument("--sigma", type=float, default=2.0, help="Forecast uncertainty in °C (default: 2.0)")
    parser.add_argument("--min-edge", type=float, default=5.0, help="Minimum edge %% (default: 5.0)")
    parser.add_argument("--top", type=int, default=50, help="Show top N signals (default: 50)")
    parser.add_argument("--limit", type=int, default=200, help="Max events to fetch (default: 200)")
    parser.add_argument("--interval", type=int, default=300, help="Refresh interval in seconds (default: 300)")

    args = parser.parse_args()

    print(f"Starting weather dashboard on port {args.port}...")
    print(f"Settings: sigma={args.sigma}, min-edge={args.min_edge}%, top={args.top}, refresh={args.interval}s")

    serve(
        port=args.port,
        sigma=args.sigma,
        min_edge=args.min_edge / 100.0,
        top_n=args.top,
        limit=args.limit,
        interval=args.interval,
    )


if __name__ == "__main__":
    main()
