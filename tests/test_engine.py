from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import make_frame
from usquant.backtest import BacktestConfig, Backtester, CommissionModel, SlippageModel
from usquant.data import MarketData
from usquant.portfolio import signals_to_weights
from usquant.risk import RiskLimits, apply_weight_limits
from usquant.strategies import MACrossover


def _weights(md: MarketData, values: dict[str, list[float]]) -> pd.DataFrame:
    return pd.DataFrame(values, index=md.dates)


def test_signal_at_close_t_fills_at_open_t_plus_1(frictionless):
    close = [100, 101, 102, 103, 104, 105]
    open_ = [99, 100.5, 101.5, 110, 103.5, 104.5]  # distinctive open on bar 3
    md = MarketData.from_frames({"X": make_frame(close, open_)})
    w = _weights(md, {"X": [0, 0, 1, 1, 1, 1]})  # decided at close of bar 2
    res = Backtester(frictionless).run(md, w)

    assert len(res.fills) == 1
    fill = res.fills.iloc[0]
    assert fill["date"] == md.dates[3]
    assert fill["fill_price"] == pytest.approx(110.0)
    shares = 100_000 / 110.0
    assert res.positions["X"].iloc[2] == 0
    assert res.positions["X"].iloc[3] == pytest.approx(shares)
    np.testing.assert_allclose(res.equity_curve.iloc[:3], 100_000)
    np.testing.assert_allclose(res.equity_curve.iloc[3:], shares * np.array(close[3:]))


def test_execution_at_next_close_option(frictionless):
    close = [100, 101, 102, 103]
    md = MarketData.from_frames({"X": make_frame(close, [99, 98, 97, 96])})
    frictionless.execution_price = "close"
    res = Backtester(frictionless).run(md, _weights(md, {"X": [1, 1, 1, 1]}))
    assert res.fills.iloc[0]["date"] == md.dates[1]
    assert res.fills.iloc[0]["fill_price"] == pytest.approx(101.0)


def test_always_long_frictionless_matches_benchmark(synthetic_data, frictionless):
    md = synthetic_data
    w = _weights(md, {"SPY": [1.0] * len(md.dates)})
    res = Backtester(frictionless).run(md, w, benchmark="SPY")
    np.testing.assert_allclose(res.equity_curve.to_numpy(), res.benchmark.to_numpy(), rtol=1e-10)


def test_commission_slippage_and_cash_constraint():
    md = MarketData.from_frames({"X": make_frame([100.0] * 4)})
    cfg = BacktestConfig(
        initial_capital=10_000,
        fractional_shares=False,
        rebalance_threshold=None,
        commission=CommissionModel(per_share=0.005, min_per_order=1.0, max_pct_of_value=0.01),
        slippage=SlippageModel(bps=10.0),
    )
    res = Backtester(cfg).run(md, _weights(md, {"X": [1, 1, 1, 1]}))
    f = res.fills.iloc[0]
    # 100 shares would cost 100*100.10 + $1 = $10,011 > cash, so the engine buys 99.
    assert f["quantity"] == 99
    assert f["fill_price"] == pytest.approx(100.10)
    assert f["commission"] == pytest.approx(1.0)  # 99*0.005 = 0.495 -> $1 minimum
    assert f["slippage_cost"] == pytest.approx(99 * 0.10)
    assert res.equity["cash"].iloc[-1] == pytest.approx(10_000 - 99 * 100.10 - 1.0)
    assert res.equity_curve.iloc[-1] == pytest.approx(10_000 - 99 * 0.10 - 1.0)


def test_commission_model_caps_and_floors():
    c = CommissionModel(per_share=0.005, min_per_order=1.0, max_pct_of_value=0.01)
    assert c(10, 50.0) == pytest.approx(1.0)  # floor
    assert c(10_000, 400.0) == pytest.approx(50.0)  # per-share
    assert c(1_000, 0.10) == pytest.approx(1.0)  # 1% cap = $1.00
    assert c(0, 100.0) == 0.0
    assert c(-200, 100.0) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        CommissionModel(per_share=-1)


def test_cash_and_positions_never_negative_with_costs(synthetic_data):
    sig = MACrossover(fast=10, slow=30).generate_signals(synthetic_data)
    w = signals_to_weights(sig, "equal_active")
    cfg = BacktestConfig(slippage=SlippageModel(bps=25), commission=CommissionModel(per_share=0.05, min_per_order=5))
    res = Backtester(cfg).run(synthetic_data, w)
    assert (res.equity["cash"] >= -1e-6).all()
    assert (res.positions >= 0).all().all()
    assert (res.positions == res.positions.round()).all().all()  # integer shares
    assert res.equity["gross_exposure"].max() <= 1.0 + 1e-9
    assert res.total_commission > 0 and res.total_slippage > 0


def test_costs_reduce_returns(synthetic_data, frictionless):
    w = signals_to_weights(MACrossover(fast=10, slow=30).generate_signals(synthetic_data))
    free = Backtester(frictionless).run(synthetic_data, w).equity_curve.iloc[-1]
    costly_cfg = BacktestConfig(
        fractional_shares=True, rebalance_threshold=None,
        commission=CommissionModel(bps=10, min_per_order=0, per_share=0), slippage=SlippageModel(bps=10),
    )
    costly = Backtester(costly_cfg).run(synthetic_data, w).equity_curve.iloc[-1]
    assert costly < free


def test_engine_has_no_look_ahead(synthetic_data, frictionless):
    """Shocking prices from bar k onward must not change equity before bar k."""
    md = synthetic_data
    k = len(md.dates) // 2
    strat = MACrossover(fast=10, slow=30)

    def run(data):
        w = signals_to_weights(strat.generate_signals(data))
        return Backtester(frictionless).run(data, w).equity_curve

    base = run(md)
    scale = pd.Series(1.0, index=md.dates)
    scale.iloc[k:] = 0.5
    shocked = MarketData(
        open=md.open.mul(scale, axis=0), high=md.high.mul(scale, axis=0), low=md.low.mul(scale, axis=0),
        close=md.close.mul(scale, axis=0), volume=md.volume,
    )
    after = run(shocked)
    pd.testing.assert_series_equal(base.iloc[:k], after.iloc[:k])
    assert not np.allclose(base.iloc[k:], after.iloc[k:])


def test_drift_rebalancing(frictionless):
    n = 60
    a = make_frame(np.linspace(100, 200, n))  # rallies, so its weight drifts up
    b = make_frame([100.0] * n)
    md = MarketData.from_frames({"A": a, "B": b})
    w = _weights(md, {"A": [0.5] * n, "B": [0.5] * n})

    no_drift = Backtester(frictionless).run(md, w)
    assert no_drift.fills["date"].nunique() == 1  # only the initial purchase

    frictionless.rebalance_threshold = 0.05
    drift = Backtester(frictionless).run(md, w)
    assert drift.fills["date"].nunique() > 1
    assert (drift.weights["A"].iloc[1:] - 0.5).abs().max() <= 0.05 + 0.02


def test_drawdown_circuit_breaker_liquidates_and_cools_down(frictionless):
    close = [100, 100, 90, 70, 60, 55, 50, 50, 50, 60, 70, 80]
    md = MarketData.from_frames({"X": make_frame(close)})
    w = _weights(md, {"X": [1.0] * len(close)})
    risk = RiskLimits(max_drawdown=0.2, drawdown_cooldown=3)
    res = Backtester(frictionless, risk).run(md, w)
    # Bar 3 closes 30% below peak -> halt; liquidate at bar 4's open.
    assert res.halted.iloc[3]
    assert res.positions["X"].iloc[4] == 0
    assert res.positions["X"].iloc[5] == 0
    assert res.drawdown_trips >= 1
    # Trading resumes after the cooldown.
    assert res.positions["X"].iloc[-1] > 0


def test_round_trip_trade_pnl(frictionless):
    close = [100, 100, 110, 120, 120, 120]
    md = MarketData.from_frames({"X": make_frame(close)})
    w = _weights(md, {"X": [1, 1, 0, 0, 0, 0]})  # buy at bar1 open (100), sell at bar3 open (120)
    res = Backtester(frictionless).run(md, w)
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    assert t["entry_date"] == md.dates[1] and t["exit_date"] == md.dates[3]
    assert t["bars_held"] == 2
    assert t["pnl"] == pytest.approx(20_000)
    assert t["return"] == pytest.approx(0.20)


def test_apply_weight_limits_and_sizing():
    sig = pd.DataFrame({"A": [1, 1, 0, 0], "B": [1, 0, 0, 1], "C": [1, 0, 0, 0]}, dtype=float)
    eq = signals_to_weights(sig, "equal_active")
    np.testing.assert_allclose(eq.sum(axis=1), [1, 1, 0, 1])
    np.testing.assert_allclose(eq.iloc[0], [1 / 3] * 3)
    slot = signals_to_weights(sig, "fixed_slot")
    np.testing.assert_allclose(slot.iloc[1], [1 / 3, 0, 0])

    capped = apply_weight_limits(eq, RiskLimits(max_position_weight=0.5, max_gross_exposure=0.8))
    assert capped.max().max() <= 0.5 + 1e-12
    assert capped.abs().sum(axis=1).max() <= 0.8 + 1e-12
    np.testing.assert_allclose(capped.iloc[1], [0.5, 0, 0])
    with pytest.raises(ValueError):
        signals_to_weights(sig, "kelly")
    with pytest.raises(ValueError):
        RiskLimits(max_drawdown=1.5)


def test_missing_symbol_raises(synthetic_data, frictionless):
    w = pd.DataFrame({"IWM": 1.0}, index=synthetic_data.dates)
    with pytest.raises(ValueError, match="no price data"):
        Backtester(frictionless).run(synthetic_data, w)
