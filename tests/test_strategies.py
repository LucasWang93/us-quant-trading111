from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import make_frame
from usquant.data import MarketData
from usquant.strategies import MACrossover, Momentum, make_strategy
from usquant.strategies.base import first_bar_of_period


def _md(close, symbol="X"):
    return MarketData.from_frames({symbol: make_frame(close)})


def test_ma_crossover_known_values():
    # Falling then rising prices: fast(2) crosses above slow(4) once the rise starts.
    close = [10, 9, 8, 7, 6, 7, 8, 9, 10]
    sig = MACrossover(fast=2, slow=4).generate_signals(_md(close))["X"].tolist()
    # Bars 0-2: slow MA not yet defined -> flat.
    c = pd.Series(close, dtype=float)
    expected = (c.rolling(2).mean() > c.rolling(4).mean()).astype(float).where(c.rolling(4).mean().notna(), 0.0)
    assert sig == expected.tolist()
    assert sig[:3] == [0.0, 0.0, 0.0]
    assert sig[-1] == 1.0 and sig[4] == 0.0


def test_ma_crossover_validates_params():
    with pytest.raises(ValueError):
        MACrossover(fast=10, slow=5)
    with pytest.raises(ValueError):
        MACrossover(ma_type="wma")


def test_momentum_known_values():
    close = [100, 101, 102, 103, 104, 103, 102, 101, 100, 99]
    sig = Momentum(lookback=3, skip=1, rebalance="D").generate_signals(_md(close))["X"]
    c = pd.Series(close, dtype=float)
    mom = c.shift(1) / c.shift(3) - 1
    expected = (mom > 0).astype(float).where(mom.notna(), 0.0)
    assert sig.tolist() == expected.tolist()
    assert sig.iloc[:3].sum() == 0  # warm-up is flat


def test_momentum_top_n_picks_strongest():
    strong = make_frame(np.linspace(100, 200, 30))
    weak = make_frame(np.linspace(100, 110, 30))
    md = MarketData.from_frames({"A": strong, "B": weak})
    sig = Momentum(lookback=10, skip=0, top_n=1, rebalance="D").generate_signals(md)
    assert (sig["A"].iloc[10:] == 1).all()
    assert (sig["B"] == 0).all()


def test_first_bar_of_month_mask_uses_only_past_dates():
    idx = pd.bdate_range("2024-01-25", "2024-03-10")
    mask = first_bar_of_period(idx, "M")
    firsts = idx[mask.to_numpy()]
    assert list(firsts.strftime("%Y-%m-%d")) == ["2024-01-25", "2024-02-01", "2024-03-01"]


def test_monthly_rebalance_only_changes_on_first_bar_of_month(synthetic_data):
    sig = Momentum(lookback=60, skip=5, rebalance="M").generate_signals(synthetic_data)
    changes = sig.diff().abs().sum(axis=1) > 0
    change_dates = sig.index[changes.to_numpy()][1:]
    mask = first_bar_of_period(sig.index, "M")
    assert mask.loc[change_dates].all()


@pytest.mark.parametrize(
    "strategy",
    [
        MACrossover(fast=20, slow=50),
        MACrossover(fast=10, slow=30, ma_type="ema", rebalance="W"),
        Momentum(lookback=126, skip=21, rebalance="M"),
        Momentum(lookback=60, skip=0, top_n=1, rebalance="D"),
    ],
    ids=repr,
)
def test_no_look_ahead_truncation_invariance(strategy, synthetic_data):
    """Signal at date t must be identical whether or not data after t exists."""
    full = strategy.generate_signals(synthetic_data)
    rng = np.random.default_rng(0)
    cut_points = rng.choice(np.arange(60, len(synthetic_data.dates)), size=15, replace=False)
    for i in cut_points:
        t = synthetic_data.dates[i]
        partial = strategy.generate_signals(synthetic_data.truncate(t))
        pd.testing.assert_series_equal(partial.loc[t], full.loc[t], check_names=False)


def test_future_price_shock_does_not_change_past_signals(synthetic_data):
    strat = MACrossover(fast=20, slow=50)
    base = strat.generate_signals(synthetic_data)
    shocked_close = synthetic_data.close.copy()
    k = len(shocked_close) // 2
    shocked_close.iloc[k:] *= 3.0
    shocked = MarketData(
        open=synthetic_data.open, high=synthetic_data.high, low=synthetic_data.low,
        close=shocked_close, volume=synthetic_data.volume,
    )
    after = strat.generate_signals(shocked)
    pd.testing.assert_frame_equal(base.iloc[:k], after.iloc[:k])


def test_signals_are_aligned_and_bounded(synthetic_data):
    for name in ("ma_crossover", "momentum"):
        sig = make_strategy(name).generate_signals(synthetic_data)
        assert sig.index.equals(synthetic_data.dates)
        assert list(sig.columns) == synthetic_data.symbols
        assert sig.isin([0.0, 1.0]).all().all()
    with pytest.raises(ValueError):
        make_strategy("nope")
