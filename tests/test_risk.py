from level_trader.config import RiskConfig
from level_trader.risk.sizing import plan_position


def test_long_position_risks_exactly_configured_fraction():
    cfg = RiskConfig(account_equity=10_000, risk_per_trade=0.01, leverage=3)
    plan = plan_position(
        equity=10_000, side="long", entry=100.0, stop=99.0, take_profit=103.0, cfg=cfg
    )
    assert plan is not None
    # 1% of 10k = 100 risk, per-unit risk = 1 -> qty=100, notional=10000 -> 1x leverage used.
    assert abs(plan.risk_amount - 100.0) < 1e-6
    assert abs(plan.quantity - 100.0) < 1e-6
    assert abs(plan.leverage_used - 1.0) < 1e-6


def test_leverage_cap_clamps_quantity():
    cfg = RiskConfig(account_equity=10_000, risk_per_trade=0.01, leverage=3)
    # Very tight stop -> raw qty would exceed 3x leverage.
    plan = plan_position(
        equity=10_000, side="long", entry=100.0, stop=99.99, take_profit=101.0, cfg=cfg
    )
    assert plan is not None
    assert plan.notional <= 10_000 * cfg.leverage + 1e-6
    assert plan.risk_amount <= 100.0 + 1e-6  # risk never exceeds configured fraction


def test_rejects_invalid_geometry():
    cfg = RiskConfig(account_equity=10_000, risk_per_trade=0.01, leverage=3)
    # Long with stop above entry is invalid.
    assert plan_position(
        equity=10_000, side="long", entry=100.0, stop=101.0, take_profit=105.0, cfg=cfg
    ) is None
