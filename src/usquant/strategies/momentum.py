"""Time-series / cross-sectional price momentum strategy."""

from __future__ import annotations

import pandas as pd

from usquant.data.panel import MarketData
from usquant.strategies.base import Strategy, hold_between_rebalances


class Momentum(Strategy):
    """Hold assets whose trailing return is positive (and optionally among the top N).

    Momentum at bar ``t`` is ``close[t - skip] / close[t - lookback] - 1``; the
    classic "12-1" setup is ``lookback=252, skip=21`` which ignores the most recent
    month to sidestep short-term reversal.

    Parameters
    ----------
    lookback, skip:
        Trading-day windows, ``0 <= skip < lookback``.
    threshold:
        Absolute-momentum filter: only hold when momentum > ``threshold``
        (``0.0`` = positive trailing return; a common proxy compares to T-bills).
    top_n:
        If set, additionally require the asset to rank in the top ``top_n`` by
        momentum on that bar (relative momentum / rotation).
    rebalance:
        ``"D"``, ``"W"``, ``"M"`` or ``"Q"``: signals update on the first bar of each period.
    """

    name = "momentum"

    def __init__(
        self,
        lookback: int = 252,
        skip: int = 21,
        threshold: float = 0.0,
        top_n: int | None = None,
        rebalance: str = "M",
    ):
        if not 0 <= skip < lookback:
            raise ValueError("require 0 <= skip < lookback")
        if top_n is not None and top_n < 1:
            raise ValueError("top_n must be >= 1")
        self.lookback = int(lookback)
        self.skip = int(skip)
        self.threshold = float(threshold)
        self.top_n = top_n
        self.rebalance = rebalance

    def momentum(self, close: pd.DataFrame) -> pd.DataFrame:
        return close.shift(self.skip) / close.shift(self.lookback) - 1.0

    def generate_signals(self, data: MarketData) -> pd.DataFrame:
        mom = self.momentum(data.close)
        signal = (mom > self.threshold).astype(float)
        if self.top_n is not None:
            rank = mom.rank(axis=1, ascending=False, method="first")
            signal = signal.where(rank <= self.top_n, 0.0)
        signal = signal.where(mom.notna(), 0.0)
        return hold_between_rebalances(signal, self.rebalance)
