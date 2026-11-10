"""Strategy interface.

Contract (enforced by tests): the value of ``generate_signals(data)`` at row ``t``
may depend only on bars with date ``<= t``. In other words signals are computed
"at the close" of day ``t``. The backtester is responsible for acting on them no
earlier than the next bar, which removes look-ahead bias by construction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from usquant.data.panel import MarketData


class Strategy(ABC):
    """Maps market data to a dates x symbols frame of desired exposure signals.

    Signals are in ``[0, 1]`` for the long-only Phase 1 strategies: ``1`` = want to
    hold the symbol, ``0`` = flat. Converting signals to portfolio weights is done
    separately by :mod:`usquant.portfolio.sizing` so sizing rules can be swapped
    without touching alpha logic.
    """

    name: str = "base"

    @abstractmethod
    def generate_signals(self, data: MarketData) -> pd.DataFrame:
        """Return signals aligned to ``data.close`` (same index and columns)."""

    def params(self) -> dict[str, Any]:
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.params().items())
        return f"{type(self).__name__}({args})"


def first_bar_of_period(index: pd.DatetimeIndex, freq: str) -> pd.Series:
    """Boolean mask that is True on the first trading bar of each period.

    Uses only the current and previous bar's date, so it never needs to know
    future dates (unlike a "last trading day of the month" mask).
    ``freq``: ``"D"`` (every bar), ``"W"``, ``"M"``, ``"Q"``.
    """
    if freq == "D":
        return pd.Series(True, index=index)
    if freq == "W":
        key = index.isocalendar().week.to_numpy() + 100 * index.isocalendar().year.to_numpy()
    elif freq == "M":
        key = index.year * 100 + index.month
    elif freq == "Q":
        key = index.year * 10 + index.quarter
    else:
        raise ValueError(f"unsupported rebalance frequency {freq!r}")
    key = pd.Series(key, index=index)
    return key.ne(key.shift(1))


def hold_between_rebalances(signals: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Keep signals only on rebalance bars and carry them forward in between."""
    if freq == "D":
        return signals
    mask = first_bar_of_period(signals.index, freq)
    return signals.where(mask, axis=0).ffill().fillna(0.0)
