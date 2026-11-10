"""Multi-symbol price panel used by strategies and the backtester."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def adjust_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Back-adjust open/high/low/close for splits and dividends.

    Uses the ratio ``adj_close / close`` (the vendor's cumulative adjustment factor)
    and applies it to every price column. Adjusted series therefore produce
    total-return-like returns (dividends reinvested on the ex-date) while
    preserving each bar's intraday shape (open-to-close moves, gaps).
    Volume is left unadjusted.
    """
    factor = df["adj_close"] / df["close"]
    out = pd.DataFrame(index=df.index)
    for col in ("open", "high", "low", "close"):
        out[col] = df[col] * factor
    out["volume"] = df["volume"]
    return out


@dataclass(frozen=True)
class MarketData:
    """Aligned wide frames (dates x symbols) of daily bars.

    All frames share the same index and columns. Dates are the *intersection* of the
    symbols' trading calendars so no price is ever forward-filled into the backtest.
    """

    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    adjusted: bool = True

    @classmethod
    def from_frames(cls, frames: dict[str, pd.DataFrame], adjust: bool = True) -> "MarketData":
        if not frames:
            raise ValueError("no price frames supplied")
        prepared = {s: (adjust_ohlc(df) if adjust else df) for s, df in frames.items()}
        common = None
        for df in prepared.values():
            common = df.index if common is None else common.intersection(df.index)
        if common is None or len(common) == 0:
            raise ValueError("symbols have no overlapping dates")
        common = common.sort_values()
        fields = {}
        for field in ("open", "high", "low", "close", "volume"):
            wide = pd.DataFrame({s: df.loc[common, field] for s, df in prepared.items()})
            wide.index.name = "date"
            wide.columns.name = "symbol"
            fields[field] = wide
        return cls(**fields, adjusted=adjust)

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    def select(self, symbols: list[str]) -> "MarketData":
        return MarketData(
            open=self.open[symbols],
            high=self.high[symbols],
            low=self.low[symbols],
            close=self.close[symbols],
            volume=self.volume[symbols],
            adjusted=self.adjusted,
        )

    def truncate(self, end: pd.Timestamp) -> "MarketData":
        """Data available up to and including ``end`` (used in look-ahead tests)."""
        return MarketData(
            open=self.open.loc[:end],
            high=self.high.loc[:end],
            low=self.low.loc[:end],
            close=self.close.loc[:end],
            volume=self.volume.loc[:end],
            adjusted=self.adjusted,
        )
