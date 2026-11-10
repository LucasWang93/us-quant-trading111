from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from usquant import metrics as m


def _eq(values):
    return pd.Series(values, index=pd.bdate_range("2020-01-01", periods=len(values)), dtype=float)


def test_cagr_and_total_return():
    eq = _eq(np.linspace(100, 200, 253))  # 252 bars elapsed = 1 year
    assert m.total_return(eq) == pytest.approx(1.0)
    assert m.cagr(eq) == pytest.approx(1.0)
    two_years = _eq(100 * 1.1 ** (np.arange(505) / 252))
    assert m.cagr(two_years) == pytest.approx(0.10)


def test_max_drawdown_and_duration():
    eq = _eq([100, 120, 90, 100, 130, 117])
    assert m.max_drawdown(eq) == pytest.approx(-0.25)
    assert m.max_drawdown_duration(eq) == 2
    dd = m.drawdown_series(eq)
    assert dd.iloc[1] == 0 and dd.iloc[2] == pytest.approx(-0.25)
    assert m.calmar_ratio(eq) == pytest.approx(m.cagr(eq) / 0.25)


def test_sharpe_and_sortino_known_values():
    r = pd.Series([0.01, -0.005] * 126)
    expected = r.mean() / r.std(ddof=1) * math.sqrt(252)
    assert m.sharpe_ratio(r) == pytest.approx(expected)
    downside = np.sqrt(np.mean(np.minimum(r, 0) ** 2))
    assert m.sortino_ratio(r) == pytest.approx(r.mean() / downside * math.sqrt(252))
    assert m.sharpe_ratio(pd.Series([0.001] * 50)) == 0.0  # zero variance guard
    # A risk-free rate lowers Sharpe.
    assert m.sharpe_ratio(r, rf_annual=0.05) < m.sharpe_ratio(r)


def test_volatility_and_win_rate():
    r = pd.Series([0.01, -0.01, 0.0, 0.02])
    assert m.annualized_volatility(r) == pytest.approx(r.std(ddof=1) * math.sqrt(252))
    assert m.daily_win_rate(r) == pytest.approx(2 / 3)


def test_relative_stats():
    rng = np.random.default_rng(0)
    b = pd.Series(rng.normal(0.0005, 0.01, 500))
    same = m.relative_stats(b, b)
    assert same["beta"] == pytest.approx(1.0)
    assert same["alpha"] == pytest.approx(0.0, abs=1e-12)
    assert same["tracking_error"] == pytest.approx(0.0, abs=1e-12)
    levered = m.relative_stats(2 * b + 0.0001, b)
    assert levered["beta"] == pytest.approx(2.0)
    assert levered["alpha"] == pytest.approx(0.0001 * 252, rel=1e-6)
    assert levered["correlation"] == pytest.approx(1.0)


def test_trade_stats():
    trades = pd.DataFrame({"pnl": [100.0, -50.0, 200.0, -25.0]})
    s = m.trade_stats(trades)
    assert s["num_trades"] == 4
    assert s["trade_win_rate"] == pytest.approx(0.5)
    assert s["avg_win"] == pytest.approx(150)
    assert s["avg_loss"] == pytest.approx(-37.5)
    assert s["profit_factor"] == pytest.approx(300 / 75)
    assert m.trade_stats(pd.DataFrame())["num_trades"] == 0


def test_summarize_end_to_end(synthetic_data, frictionless):
    from usquant.backtest import Backtester
    from usquant.portfolio import signals_to_weights
    from usquant.strategies import Momentum

    w = signals_to_weights(Momentum(lookback=126, skip=21).generate_signals(synthetic_data))
    res = Backtester(frictionless).run(synthetic_data, w, benchmark="SPY")
    s = m.summarize(res)
    assert set(s) == {"strategy", "benchmark", "relative"}
    for key in ("cagr", "sharpe", "max_drawdown", "trade_win_rate", "annual_turnover"):
        assert key in s["strategy"]
    assert s["benchmark"]["max_drawdown"] <= 0
    assert s["relative"]["excess_cagr"] == pytest.approx(s["strategy"]["cagr"] - s["benchmark"]["cagr"])
