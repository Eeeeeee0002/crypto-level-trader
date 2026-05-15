"""Live dashboard server for the weather betting agent."""

from __future__ import annotations

import asyncio
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from threading import Thread

from weather_agent.agent import run_scan
from weather_agent.strategy import BetSignal, filter_safe

_cached_html: str = ""
_last_update: float = 0.0
_update_interval: int = 300  # 5 minutes
_sigma: float = 2.0
_min_edge: float = 0.05
_top_n: int = 50
_limit: int = 200
_safe_only: bool = False


def _signal_to_dict(sig: BetSignal) -> dict:
    return {
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
    }


def _build_html(signals: list[BetSignal], scan_time: str, events_scanned: int, events_with_forecast: int) -> str:
    high = sum(1 for s in signals if s.confidence == "high")
    med = sum(1 for s in signals if s.confidence == "medium")
    low = len(signals) - high - med

    rows = ""
    for i, s in enumerate(signals, 1):
        edge_pct = s.edge * 100
        ev_pct = s.expected_value * 100
        mp_pct = s.market_price * 100
        ep_pct = s.estimated_probability * 100

        if edge_pct >= 50:
            edge_cls = "edge-high"
        elif edge_pct >= 15:
            edge_cls = "edge-med"
        else:
            edge_cls = "edge-low"

        conf_cls = f"conf-{s.confidence}"
        side_cls = f"side-{s.side.lower()}"
        measure = "highest" if "ighest" in s.event_title.lower() else "lowest"

        low_t = s.bucket_temp_low
        high_t = s.bucket_temp_high
        if low_t is None and high_t is not None:
            bucket_str = f"≤{high_t:.0f}"
        elif high_t is None and low_t is not None:
            bucket_str = f"≥{low_t:.0f}"
        elif low_t is not None and high_t is not None:
            bucket_str = f"{low_t:.0f}" if low_t == high_t else f"{low_t:.0f}–{high_t:.0f}"
        else:
            bucket_str = "?"

        rows += f"""<tr>
  <td>{i}</td>
  <td class="city">{s.city}</td>
  <td>{s.event_date}</td>
  <td>{measure}</td>
  <td>{bucket_str}</td>
  <td class="{side_cls}">{s.side}</td>
  <td>{mp_pct:.1f}%</td>
  <td>{ep_pct:.1f}%</td>
  <td class="{edge_cls}">+{edge_pct:.1f}%</td>
  <td>+{ev_pct:.0f}%</td>
  <td><span class="{conf_cls}">{s.confidence}</span></td>
  <td>{s.forecast_temp:.1f}°</td>
</tr>"""

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Weather Agent — Прогнозы ставок (LIVE)</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e0e0e0; padding: 20px; }}
h1 {{ text-align: center; margin-bottom: 4px; font-size: 28px; color: #fff; }}
.live {{ display: inline-block; background: #ef4444; color: #fff; font-size: 12px; padding: 2px 8px; border-radius: 4px; animation: pulse 2s infinite; vertical-align: middle; margin-left: 8px; }}
@keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}
.subtitle {{ text-align: center; color: #888; margin-bottom: 4px; font-size: 14px; }}
.update-info {{ text-align: center; color: #666; margin-bottom: 20px; font-size: 12px; }}
.update-info #countdown {{ color: #4ade80; }}
.stats {{ display: flex; justify-content: center; gap: 20px; margin-bottom: 24px; flex-wrap: wrap; }}
.stat {{ background: #1a1d27; border-radius: 12px; padding: 14px 20px; text-align: center; min-width: 100px; }}
.stat .num {{ font-size: 26px; font-weight: bold; color: #4ade80; }}
.stat .label {{ font-size: 11px; color: #888; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; background: #1a1d27; border-radius: 12px; overflow: hidden; }}
thead th {{ background: #252836; color: #fff; padding: 12px 10px; font-size: 13px; text-align: left; white-space: nowrap; position: sticky; top: 0; z-index: 1; }}
tbody td {{ padding: 10px; border-bottom: 1px solid #2a2d3a; font-size: 13px; }}
tbody tr:hover {{ background: #252836; }}
.edge-high {{ color: #4ade80; font-weight: bold; }}
.edge-med {{ color: #facc15; font-weight: bold; }}
.edge-low {{ color: #fb923c; }}
.conf-high {{ background: #166534; color: #4ade80; padding: 3px 8px; border-radius: 6px; font-size: 11px; }}
.conf-medium {{ background: #713f12; color: #facc15; padding: 3px 8px; border-radius: 6px; font-size: 11px; }}
.conf-low {{ background: #7c2d12; color: #fb923c; padding: 3px 8px; border-radius: 6px; font-size: 11px; }}
.side-yes {{ color: #4ade80; font-weight: 600; }}
.side-no {{ color: #f87171; font-weight: 600; }}
.city {{ font-weight: 600; color: #fff; }}
footer {{ text-align: center; color: #555; margin-top: 16px; font-size: 12px; }}
</style>
</head>
<body>
<h1>🌤 Weather Agent <span class="live">LIVE</span></h1>
<p class="subtitle">Прогнозы ставок на температуру — Polymarket</p>
<p class="update-info">
  Обновлено: {scan_time} UTC &nbsp;|&nbsp;
  Событий: {events_scanned} (с прогнозом: {events_with_forecast}) &nbsp;|&nbsp;
  Следующее обновление: <span id="countdown">--:--</span>
</p>

<div class="stats">
  <div class="stat"><div class="num">{len(signals)}</div><div class="label">Сигналов</div></div>
  <div class="stat"><div class="num">{high}</div><div class="label">High</div></div>
  <div class="stat"><div class="num">{med}</div><div class="label">Medium</div></div>
  <div class="stat"><div class="num">{low}</div><div class="label">Low</div></div>
</div>

<table>
<thead>
<tr>
  <th>#</th><th>Город</th><th>Дата</th><th>Тип</th><th>Бакет</th>
  <th>Ставка</th><th>Цена рынка</th><th>Наша оценка</th>
  <th>Edge</th><th>EV%</th><th>Уверенность</th><th>Прогноз</th>
</tr>
</thead>
<tbody>
{rows}
</tbody>
</table>

<footer>
  Данные: Polymarket Gamma API + Open-Meteo | Модель: нормальное распределение (σ={_sigma})
  | Мин. edge: {_min_edge*100:.0f}%
</footer>

<script>
const INTERVAL = {_update_interval};
let remaining = INTERVAL;
const el = document.getElementById('countdown');

setInterval(() => {{
  remaining--;
  if (remaining <= 0) {{
    location.reload();
    remaining = INTERVAL;
  }}
  const m = Math.floor(remaining / 60);
  const s = remaining % 60;
  el.textContent = m + ':' + String(s).padStart(2, '0');
}}, 1000);
</script>
</body>
</html>"""


async def _refresh() -> None:
    global _cached_html, _last_update
    import datetime

    result = await run_scan(sigma=_sigma, min_edge=_min_edge, limit=_limit)
    signals = result.signals
    if _safe_only:
        signals = filter_safe(signals, min_prob=0.85, min_edge=0.10)
    signals = signals[:_top_n]
    scan_time = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    _cached_html = _build_html(signals, scan_time, result.events_scanned, result.events_with_forecast)
    _last_update = time.time()


class _Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/signals":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(_cached_html.encode() if False else b"[]")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_cached_html.encode())

    def log_message(self, format: str, *args: object) -> None:
        pass  # suppress logs


async def _update_loop() -> None:
    while True:
        try:
            print("[dashboard] Refreshing data...")
            await _refresh()
            print(f"[dashboard] Updated — {len(_cached_html)} bytes")
        except Exception as exc:
            print(f"[dashboard] Error refreshing: {exc}")
        await asyncio.sleep(_update_interval)


def serve(
    port: int = 8050,
    sigma: float = 2.0,
    min_edge: float = 0.05,
    top_n: int = 50,
    limit: int = 200,
    interval: int = 300,
    safe_only: bool = False,
) -> None:
    global _sigma, _min_edge, _top_n, _limit, _update_interval, _safe_only
    _sigma = sigma
    _min_edge = min_edge
    _top_n = top_n
    _limit = limit
    _update_interval = interval
    _safe_only = safe_only

    asyncio.run(_refresh())

    server = HTTPServer(("0.0.0.0", port), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[dashboard] Live at http://localhost:{port}")

    asyncio.run(_update_loop())
