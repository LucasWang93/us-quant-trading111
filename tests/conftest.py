from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from usquant.backtest import BacktestConfig, CommissionModel, SlippageModel
from usquant.data import MarketData, SyntheticProvider


def make_frame(close, open_=None, adj_factor=1.0, start="2024-01-01") -> pd.DataFrame:
    """Hand-built single-symbol OHLCV frame on business days."""
    close = np.asarray(close, dtype=float)
    open_ = close.copy() if open_ is None else np.asarray(open_, dtype=float)
    idx = pd.bdate_range(start, periods=len(close), name="date")
    factor = np.broadcast_to(np.asarray(adj_factor, dtype=float), close.shape)
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * 1.01,
            "low": np.minimum(open_, close) * 0.99,
            "close": close,
            "adj_close": close * factor,
            "volume": 1e6,
        },
        index=idx,
    )


@pytest.fixture
def synthetic_data() -> MarketData:
    provider = SyntheticProvider(seed=7)
    frames = provider.fetch_many(["SPY", "QQQ"], "2015-01-01", "2020-12-31")
    return MarketData.from_frames(frames)


@pytest.fixture
def frictionless() -> BacktestConfig:
    return BacktestConfig(
        initial_capital=100_000,
        fractional_shares=True,
        rebalance_threshold=None,
        commission=CommissionModel.zero(),
        slippage=SlippageModel(bps=0.0),
    )
