"""Market data provider interface and OHLCV schema validation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

import numpy as np
import pandas as pd

#: Canonical column set every provider must return (lower-case, raw/unadjusted
#: OHLC plus a split- and dividend-adjusted close).
OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "adj_close", "volume")

DataLike = str | date | datetime | pd.Timestamp


class DataValidationError(ValueError):
    """Raised when a price frame violates the canonical OHLCV schema."""


def to_timestamp(value: DataLike | None) -> pd.Timestamp | None:
    """Convert a date-like value to a tz-naive, normalized ``pd.Timestamp``."""
    if value is None:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.normalize()


def validate_ohlcv(df: pd.DataFrame, symbol: str = "?") -> pd.DataFrame:
    """Validate and normalise a single-symbol OHLCV frame.

    Returns a cleaned copy: sorted, de-duplicated, tz-naive ``DatetimeIndex`` named
    ``date``, canonical columns as floats, rows with a missing close removed.

    Raises :class:`DataValidationError` on structural problems (missing columns,
    non-positive prices, high < low).
    """
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(f"{symbol}: missing columns {missing}")
    if df.empty:
        raise DataValidationError(f"{symbol}: no rows")

    out = df.loc[:, list(OHLCV_COLUMNS)].copy()
    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    out.index = idx.normalize()
    out.index.name = "date"
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out.astype(float)
    out = out.dropna(subset=["close", "adj_close"])
    if out.empty:
        raise DataValidationError(f"{symbol}: no rows with a valid close")

    # Fill missing open/high/low with close (rare for daily ETF data) rather than drop.
    for col in ("open", "high", "low"):
        out[col] = out[col].fillna(out["close"])
    out["volume"] = out["volume"].fillna(0.0)

    prices = out[["open", "high", "low", "close", "adj_close"]].to_numpy()
    if not np.all(np.isfinite(prices)) or np.any(prices <= 0):
        raise DataValidationError(f"{symbol}: non-positive or non-finite prices")
    tol = 1e-6 * out["close"]
    if np.any(out["high"] + tol < out["low"]):
        raise DataValidationError(f"{symbol}: high < low on some rows")
    return out


class DataProvider(ABC):
    """Source of daily historical OHLCV bars.

    Implementations must return a frame with the columns in :data:`OHLCV_COLUMNS`
    indexed by trading date. ``open/high/low/close`` are *unadjusted* traded prices;
    ``adj_close`` is adjusted for splits and dividends. Adjustment of the other
    columns happens downstream in :func:`usquant.data.panel.adjust_ohlc`.
    """

    #: Short identifier used for cache paths and logging.
    name: str = "base"

    @abstractmethod
    def fetch(self, symbol: str, start: DataLike | None = None, end: DataLike | None = None) -> pd.DataFrame:
        """Return validated daily bars for ``symbol`` in ``[start, end]`` (inclusive)."""

    def fetch_many(
        self, symbols: list[str], start: DataLike | None = None, end: DataLike | None = None
    ) -> dict[str, pd.DataFrame]:
        return {s: self.fetch(s, start, end) for s in symbols}


def slice_dates(df: pd.DataFrame, start: DataLike | None, end: DataLike | None) -> pd.DataFrame:
    """Inclusive date slice that tolerates ``None`` bounds."""
    s, e = to_timestamp(start), to_timestamp(end)
    return df.loc[s:e]
