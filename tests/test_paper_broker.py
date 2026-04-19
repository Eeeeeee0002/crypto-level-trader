from level_trader.broker.paper import PaperBroker
from level_trader.config import BrokerConfig


def _broker(equity: float = 10_000) -> PaperBroker:
    # Zero fees/slippage so PnL math is easy.
    return PaperBroker(starting_equity=equity, cfg=BrokerConfig(taker_fee=0.0, slippage_pct=0.0))


def test_long_take_profit_realizes_expected_pnl():
    b = _broker()
    pos = b.open(symbol="BTC/USDT:USDT", side="long", entry=100.0, stop=99.0, take_profit=102.0, quantity=10.0)
    assert pos.entry == 100.0
    closed = b.on_price("BTC/USDT:USDT", 102.5, ts=0)
    assert len(closed) == 1
    assert closed[0].close_reason == "take_profit"
    assert b.equity() == 10_000 + (102.0 - 100.0) * 10.0


def test_short_stop_loss_realizes_expected_loss():
    b = _broker()
    b.open(symbol="ETH/USDT:USDT", side="short", entry=1000.0, stop=1010.0, take_profit=980.0, quantity=1.0)
    closed = b.on_price("ETH/USDT:USDT", 1015.0, ts=0)
    assert len(closed) == 1
    assert closed[0].close_reason == "stop_loss"
    assert b.equity() == 10_000 + (1000.0 - 1010.0) * 1.0  # -10


def test_does_not_trigger_on_other_symbol():
    b = _broker()
    b.open(symbol="BTC/USDT:USDT", side="long", entry=100.0, stop=99.0, take_profit=102.0, quantity=1.0)
    closed = b.on_price("ETH/USDT:USDT", 50.0, ts=0)
    assert closed == []
    assert len(b.open_positions()) == 1
