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

### Web dashboard

To run the trader and the FastAPI dashboard together (paper mode, real market data):

```bash
uv run level-trader --config config.yaml start --host 0.0.0.0 --port 8787
```

Then open http://localhost:8787 to watch open positions, live unrealized PnL,
realized PnL, equity, and the trade log in real time. The dashboard reads a
JSON snapshot the trader writes every loop (`runs/state.json`).

If you want the dashboard in a separate process, use `level-trader web` (serves
the UI only and reads the same state file).

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

## Tests

```bash
uv run pytest -q
uv run ruff check src tests
```

## Disclaimer

No strategy prints money. This agent is transparent, testable and risk-capped; treat paper results as a sanity check, not a guarantee, and keep stops respected before going live.
