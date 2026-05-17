# crypto-level-trader

A disciplined crypto trading agent that trades off horizontal support/resistance levels on **Gate.io USDT perpetual futures**. It runs in **paper mode against the real live market** by default; switching to a live Gate account is a single flag away once API keys are provided.

## What it actually does

1. **Universe selection** — auto-picks the top-N (default 10) Gate USDT swap pairs by 24h quote volume.
2. **Level detection** — on each symbol, finds pivot highs/lows on the execution timeframe (default 1h) and the context timeframe (default 4h), clusters close swings into a single volume-weighted level, scores each level by number of touches + volume + HTF confirmation, and drops stale ones.
3. **Signal generation** — on every newly-closed bar, checks for:
   - **Reversal** from support/resistance (wick tags the level and closes back inside) with a pin-bar or engulfing confirmation and an EMA-200 HTF trend filter.
   - **Breakout + retest** of a level with confirmation.
   Only signals meeting the minimum reward:risk ratio (default 2:1) are accepted.
4. **Risk sizing** — fixed fractional risk per trade (default 1% of equity) with a leverage cap (default 3x). Stop-loss sits a small buffer beyond the level; take-profit is the closer of a fixed RR target or the next opposing level.
5. **Execution** — a paper broker tracks open positions, applies slippage + taker fees, and auto-closes when live price hits SL/TP. A `LiveGateBroker` is available for real orders (requires API keys and `--live`).
6. **Journaling** — every trade is appended to `runs/trades.jsonl`; equity is sampled into `runs/equity.jsonl`. `level-trader report` prints winrate, profit factor, avg win/loss, etc.

## Quickstart (paper mode, real market)

```bash
uv sync
uv run level-trader --config config.yaml universe         # show auto-picked top-10
uv run level-trader --config config.yaml levels           # detected levels per symbol
uv run level-trader --config config.yaml run              # start paper trading loop
uv run level-trader --config config.yaml report           # summarize runs/trades.jsonl
```

To stop early for a bounded test run, use `--iterations N`.

## Going live (when you're ready)

1. Create API keys on Gate with futures trading permission.
2. Export them:
   ```bash
   export GATE_API_KEY=...
   export GATE_API_SECRET=...
   ```
3. In `config.yaml`, set `broker.mode: live`.
4. Run with `--live`:
   ```bash
   uv run level-trader --config config.yaml run --live
   ```

The live broker places market entries and reduce-only trigger orders for SL and TP.

## Configuration

All parameters live in `config.yaml` and are documented inline. Key knobs:

- `universe.size` — how many top-volume pairs to trade.
- `timeframes.execution` / `timeframes.context` — entry and HTF timeframes.
- `levels.pivot_lookback`, `levels.min_touches`, `levels.cluster_pct` — how picky the level detector is.
- `signals.min_rr` — minimum reward:risk to take a trade.
- `risk.risk_per_trade`, `risk.leverage`, `risk.max_concurrent_positions` — risk caps.
- `broker.taker_fee`, `broker.slippage_pct` — paper-fill realism.

## Project layout

```
src/level_trader/
  config.py              # typed config (pydantic)
  exchange/
    base.py, gate.py     # ccxt Gate futures adapter
    universe.py          # top-N by 24h volume
  levels/detector.py     # pivot + clustering + MTF levels
  signals/engine.py      # reversal + breakout-retest signals
  risk/sizing.py         # fixed fractional risk with leverage cap
  broker/
    paper.py             # paper broker with fees/slippage
    live.py              # live Gate broker (guarded by --live)
  journal.py             # trade / equity log + metrics
  trader.py              # orchestrator
  cli.py                 # CLI entrypoint
```

---

## Weather Betting Agent (Polymarket)

A separate agent that tracks **daily temperature prediction markets** on [Polymarket](https://polymarket.com/predictions/daily-temperature), compares market odds with real weather forecasts from [Open-Meteo](https://open-meteo.com), and recommends bets where the market misprices the actual weather probability.

### How it works

1. **Market scan** — fetches active "Highest/Lowest temperature in [City] on [Date]?" events from the Polymarket Gamma API. Each event has ~11 temperature buckets (e.g. "Will the highest temperature in London be 12°C on May 14?").
2. **Forecast fetch** — for each city, gets the real forecast (max/min daily temperature) from Open-Meteo's free API (no API key needed).
3. **Probability estimation** — models the actual temperature as a normal distribution centered on the forecast with configurable uncertainty (σ). Computes the probability of each bucket.
4. **Edge detection** — compares estimated probability with market price. Signals where `estimated_prob - market_price > min_edge` are flagged as betting opportunities.
5. **Output** — a ranked table (or JSON) of recommended bets sorted by expected value, with confidence levels (high/medium/low).

### Quickstart

```bash
uv sync
uv run weather-agent                    # scan and show top-30 signals
uv run weather-agent --format json      # JSON output
uv run weather-agent --min-edge 10      # only show signals with 10%+ edge
uv run weather-agent --sigma 1.5        # tighter forecast confidence
uv run weather-agent --top 50           # show top-50
```

### CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--sigma` | 2.0 | Forecast uncertainty in °C. Lower = more confident in forecast. |
| `--min-edge` | 5.0 | Minimum edge (%) to show a signal. |
| `--top` | 30 | Number of top signals to display. |
| `--limit` | 200 | Max Polymarket events to fetch. |
| `--format` | table | Output format: `table` or `json`. |

### Supported cities

London, Paris, NYC, Miami, Tokyo, Hong Kong, Shanghai, Seoul, Moscow, Istanbul, Jakarta, Madrid, Amsterdam, Sao Paulo, Buenos Aires, Seattle, Los Angeles, Atlanta, Wellington, Chicago, Dallas, Denver, San Francisco, Phoenix, Sydney, Melbourne, Singapore, Dubai, Bangkok, Mumbai, Berlin, Rome.

### Project layout

```
src/weather_agent/
  cities.py         # city definitions (coords, timezone, unit)
  polymarket.py     # Polymarket Gamma API client (fetch & parse weather events)
  forecast.py       # Open-Meteo weather forecast client
  strategy.py       # probability model & edge detection
  agent.py          # orchestrator (scan → forecast → signals)
  cli.py            # CLI entry-point
```

---

## Tests

```bash
uv run pytest -q
uv run ruff check src tests
```

## Disclaimer

No strategy prints money. These agents are transparent, testable and risk-capped; treat results as a sanity check, not a guarantee, and keep stops respected before going live.
