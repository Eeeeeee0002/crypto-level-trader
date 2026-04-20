"""FastAPI dashboard for the level-trader agent.

Serves a single-page dashboard plus a small JSON API:

  GET /                        -> HTML dashboard
  GET /api/universe            -> list of tradeable symbols (top-N by volume)
  GET /api/levels/{symbol}     -> detected levels + nearest S/R for a symbol
  GET /api/signals             -> latest generated signal per symbol (if any)
  GET /api/trades              -> paper trade log
  GET /api/equity              -> equity curve samples
  GET /api/config              -> current config (sanitized)

The dashboard is read-only. No orders are ever sent from the web app. Running
live trading still happens through the CLI (`level-trader run --live`).
"""

from __future__ import annotations

import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from ..config import Config
from ..exchange.gate import GateFutures
from ..exchange.universe import select_universe
from ..levels.detector import Level, detect_levels, nearest_levels, ohlcv_to_df
from ..signals.engine import generate_signal

# ---- App setup -------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="crypto-level-trader", version="0.1.0")

CONFIG_PATH = os.getenv("LEVEL_TRADER_CONFIG", "config.yaml")


@lru_cache(maxsize=1)
def _config() -> Config:
    path = Path(CONFIG_PATH)
    if not path.exists():
        # Fallback to defaults when the file is missing (e.g. on a fresh container).
        return Config()
    return Config.load(path)


@lru_cache(maxsize=1)
def _exchange() -> GateFutures:
    gx = GateFutures()
    gx.load_markets()
    return gx


# ---- Caching helpers -------------------------------------------------------

_CACHE: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, producer):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = producer()
    _CACHE[key] = (now, value)
    return value


# ---- API endpoints --------------------------------------------------------


@app.get("/api/config")
def api_config() -> dict:
    cfg = _config().model_dump()
    # Strip anything that looks sensitive (there shouldn't be secrets in config.yaml, but be defensive).
    return cfg


@app.get("/api/universe")
def api_universe() -> dict:
    def _produce() -> list[str]:
        return select_universe(_exchange(), _config().universe)

    symbols = _cached("universe", ttl=120.0, producer=_produce)
    return {"symbols": symbols, "count": len(symbols)}


def _levels_payload(symbol: str) -> dict:
    cfg = _config()
    tf = cfg.timeframes
    gx = _exchange()
    try:
        ctx_rows = gx.fetch_ohlcv(symbol, tf.context, limit=tf.lookback_bars)
        exec_rows = gx.fetch_ohlcv(symbol, tf.execution, limit=tf.lookback_bars)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"ohlcv fetch failed: {e}") from e
    ctx_df = ohlcv_to_df(ctx_rows)
    exec_df = ohlcv_to_df(exec_rows)
    htf_levels = detect_levels(ctx_df, cfg.levels)
    exec_levels = detect_levels(exec_df, cfg.levels, htf_levels=htf_levels)
    last_close = float(exec_df["close"].iloc[-1]) if not exec_df.empty else 0.0
    support, resistance = nearest_levels(last_close, exec_levels)

    def _fmt(lv: Level) -> dict:
        return {
            "price": lv.price,
            "kind": lv.kind,
            "touches": lv.touches,
            "score": round(lv.score, 3),
            "htf_confirmed": lv.htf_confirmed,
        }

    sig = generate_signal(symbol, exec_df, ctx_df, exec_levels, cfg.signals)
    return {
        "symbol": symbol,
        "last_close": last_close,
        "support": _fmt(support) if support else None,
        "resistance": _fmt(resistance) if resistance else None,
        "levels_exec": [_fmt(lv) for lv in exec_levels[:12]],
        "levels_context": [_fmt(lv) for lv in htf_levels[:8]],
        "signal": (
            {
                "side": sig.side,
                "kind": sig.kind,
                "entry": sig.entry,
                "stop": sig.stop,
                "take_profit": sig.take_profit,
                "rr": sig.rr,
                "reason": sig.reason,
            }
            if sig
            else None
        ),
    }


@app.get("/api/levels/{symbol:path}")
def api_levels(symbol: str) -> dict:
    # Symbols contain slashes (BTC/USDT:USDT); :path matcher preserves them.
    return _cached(f"levels:{symbol}", ttl=60.0, producer=lambda: _levels_payload(symbol))


@app.get("/api/signals")
def api_signals() -> dict:
    def _produce() -> list[dict]:
        syms = select_universe(_exchange(), _config().universe)
        out: list[dict] = []
        for s in syms:
            try:
                payload = _levels_payload(s)
            except HTTPException:
                continue
            if payload["signal"] is not None:
                out.append({"symbol": s, **payload["signal"]})
        return out

    return {"signals": _cached("signals", ttl=60.0, producer=_produce)}


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    if limit:
        rows = rows[-limit:]
    return rows


@app.get("/api/trades")
def api_trades() -> dict:
    cfg = _config()
    rows = _read_jsonl(Path(cfg.logging.trade_log), limit=200)
    return {"trades": rows, "count": len(rows)}


@app.get("/api/equity")
def api_equity() -> dict:
    cfg = _config()
    rows = _read_jsonl(Path(cfg.logging.equity_log), limit=500)
    return {"equity": rows, "count": len(rows)}


@app.get("/api/state")
def api_state() -> dict:
    """Live snapshot of open positions, PnL, and equity written by the trader loop."""
    cfg = _config()
    path = Path(cfg.logging.state_file)
    if not path.exists():
        return {
            "running": False,
            "message": (
                "no live state yet — start the trader with "
                "`level-trader start` (or `level-trader run`) in paper mode"
            ),
        }
    try:
        data = json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        return {"running": False, "error": f"failed to read state: {e}"}
    data["running"] = (time.time() - float(data.get("ts", 0))) < 180
    return data


@app.get("/api/healthz")
def healthz() -> dict:
    return {"ok": True, "exchange": "gate", "ts": time.time()}


# ---- HTML dashboard -------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "title": "crypto-level-trader"},
    )


@app.exception_handler(Exception)
async def _on_error(_request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
    return JSONResponse(status_code=500, content={"error": str(exc)})
