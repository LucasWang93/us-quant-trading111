"""Moving-average crossover trend-following strategy."""

from __future__ import annotations

import pandas as pd

from usquant.data.panel import MarketData
from usquant.strategies.base import Strategy, hold_between_rebalances


class MACrossover(Strategy):
    """Long when the fast moving average of the close is above the slow one.

    Parameters
    ----------
    fast, slow:
        Window lengths in trading days (``fast < slow``).
    ma_type:
        ``"sma"`` (simple) or ``"ema"`` (exponential).
    rebalance:
        ``"D"`` to act on every crossover, or ``"W"``/``"M"`` to only update the
        signal on the first bar of each week/month (reduces whipsaw turnover).

    Bars before the slow average has a full window produce a flat (0) signal.
    """

    name = "ma_crossover"

    def __init__(self, fast: int = 50, slow: int = 200, ma_type: str = "sma", rebalance: str = "D"):
        if not 0 < fast < slow:
            raise ValueError("require 0 < fast < slow")
        if ma_type not in ("sma", "ema"):
            raise ValueError("ma_type must be 'sma' or 'ema'")
        self.fast = int(fast)
        self.slow = int(slow)
        self.ma_type = ma_type
        self.rebalance = rebalance

    def _ma(self, close: pd.DataFrame, window: int) -> pd.DataFrame:
        if self.ma_type == "sma":
            return close.rolling(window, min_periods=window).mean()
        # EMA: mask the warm-up period so early values are not trusted.
        ema = close.ewm(span=window, adjust=False, min_periods=window).mean()
        return ema

    def generate_signals(self, data: MarketData) -> pd.DataFrame:
        close = data.close
        fast = self._ma(close, self.fast)
        slow = self._ma(close, self.slow)
        signal = (fast > slow).astype(float)
        signal = signal.where(slow.notna(), 0.0)
        return hold_between_rebalances(signal, self.rebalance)
