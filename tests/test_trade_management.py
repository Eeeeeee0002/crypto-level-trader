"""Tests for partial TP, breakeven move, and trailing stop in the paper broker."""

from level_trader.broker.paper import PaperBroker
from level_trader.config import BrokerConfig, RiskConfig


def _broker(risk: RiskConfig) -> PaperBroker:
    # Zero fees/slippage so the maths is trivial.
    return PaperBroker(
        starting_equity=10_000,
        cfg=BrokerConfig(taker_fee=0.0, slippage_pct=0.0),
        risk=risk,
    )


def test_breakeven_move_long_upgrades_stop():
    risk = RiskConfig(breakeven_at_r=1.0)
    b = _broker(risk)
    pos = b.open(
        symbol="BTC/USDT:USDT", side="long",
        entry=100.0, stop=99.0, take_profit=120.0, quantity=1.0,
    )
    assert pos.initial_risk == 1.0
    # Move to +1R: 101.0. Stop should become 100 (breakeven).
    b.on_price("BTC/USDT:USDT", 101.0, ts=0)
    assert pos.be_moved is True
    assert pos.stop == 100.0


def test_trailing_stop_long_tracks_peak():
    risk = RiskConfig(breakeven_at_r=1.0, trail_r=1.0)
    b = _broker(risk)
    pos = b.open(
        symbol="ETH/USDT:USDT", side="long",
        entry=100.0, stop=99.0, take_profit=150.0, quantity=1.0,
    )
    # Trigger BE.
    b.on_price("ETH/USDT:USDT", 101.0, ts=0)
    # Push peak to 105; trailing stop = peak - 1*R = 104.
    b.on_price("ETH/USDT:USDT", 105.0, ts=1)
    assert pos.stop == 104.0
    # Another peak at 110 -> trail stop to 109.
    b.on_price("ETH/USDT:USDT", 110.0, ts=2)
    assert pos.stop == 109.0
    # Pullback below trailing stop (109) -> position closes at the stop price.
    closed = b.on_price("ETH/USDT:USDT", 108.5, ts=3)
    assert len(closed) == 1
    assert closed[0].close_reason == "stop_loss"
    assert closed[0].close_price == 109.0


def test_partial_take_profit_long_scales_out():
    risk = RiskConfig(partial_tp_frac=0.5, partial_tp_r=1.0)
    b = _broker(risk)
    pos = b.open(
        symbol="BTC/USDT:USDT", side="long",
        entry=100.0, stop=99.0, take_profit=105.0, quantity=2.0,
    )
    assert pos.initial_quantity == 2.0
    # Hit +1R -> 101. Partial close of 50% (1.0 unit) at 101.
    b.on_price("BTC/USDT:USDT", 101.5, ts=0)
    assert pos.partial_filled is True
    assert pos.quantity == 1.0
    # Half closed @ +1 price move on 1.0 unit = +1 pnl.
    assert abs(pos.realized_pnl - 1.0) < 1e-9
    # Remaining runs to TP.
    closed = b.on_price("BTC/USDT:USDT", 106.0, ts=1)
    assert closed and closed[0].id == pos.id
    # Total realized = partial (+1) + remaining (1 unit * 5) = 6.
    assert abs(pos.realized_pnl - 6.0) < 1e-9
    assert abs(b.equity() - 10_006.0) < 1e-9


def test_short_breakeven_and_trail():
    risk = RiskConfig(breakeven_at_r=1.0, trail_r=1.0)
    b = _broker(risk)
    pos = b.open(
        symbol="SOL/USDT:USDT", side="short",
        entry=100.0, stop=101.0, take_profit=90.0, quantity=1.0,
    )
    # Favourable move to 99 (+1R). Stop should move to 100.
    b.on_price("SOL/USDT:USDT", 99.0, ts=0)
    assert pos.be_moved is True
    assert pos.stop == 100.0
    # Price drops to 95: peak_favorable=95, trailing stop = 95 + 1 = 96.
    b.on_price("SOL/USDT:USDT", 95.0, ts=1)
    assert pos.stop == 96.0
    # Bounce up to 95.5 (still below 96): not stopped.
    closed = b.on_price("SOL/USDT:USDT", 95.5, ts=2)
    assert closed == []
    # Bounce up above trailing stop -> close at 96.
    closed = b.on_price("SOL/USDT:USDT", 96.5, ts=3)
    assert closed and closed[0].close_reason == "stop_loss"
