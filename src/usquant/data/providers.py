"""Concrete market data providers.

* :class:`YahooProvider`   - free daily data via the optional ``yfinance`` package.
* :class:`CSVProvider`     - local ``{symbol}.csv`` files (e.g. exports from any vendor).
* :class:`SyntheticProvider` - deterministic random-walk data for tests and offline demos.

Adding a vendor (Polygon, Tiingo, IBKR historical bars, ...) means subclassing
:class:`~usquant.data.base.DataProvider` and registering it in :data:`PROVIDERS`.
API keys, if a vendor needs one, must be read from environment variables, never
from config files committed to the repository.
"""

from __future__ import annotations

import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from usquant.data.base import DataLike, DataProvider, slice_dates, to_timestamp, validate_ohlcv

_COLUMN_ALIASES = {
    "date": "date",
    "datetime": "date",
    "timestamp": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "adj close": "adj_close",
    "adj_close": "adj_close",
    "adjclose": "adj_close",
    "adjusted_close": "adj_close",
    "volume": "volume",
}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map vendor column names (``Adj Close``, ``Open``...) to canonical names."""
    rename = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in _COLUMN_ALIASES:
            rename[col] = _COLUMN_ALIASES[key]
    out = df.rename(columns=rename)
    if "adj_close" not in out.columns and "close" in out.columns:
        # No adjusted series supplied: assume prices are already adjusted / no corporate actions.
        out["adj_close"] = out["close"]
    return out


class YahooProvider(DataProvider):
    """Daily bars from Yahoo Finance through ``yfinance`` (optional dependency).

    Requests unadjusted OHLC *and* ``Adj Close`` (``auto_adjust=False``) so that
    the adjustment is explicit and auditable downstream.
    """

    name = "yahoo"

    def fetch(self, symbol: str, start: DataLike | None = None, end: DataLike | None = None) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError("YahooProvider requires `pip install usquant[yahoo]` (yfinance)") from exc

        s, e = to_timestamp(start), to_timestamp(end)
        # yfinance treats `end` as exclusive; add a day so our interface is inclusive.
        raw = yf.download(
            symbol,
            start=None if s is None else s.strftime("%Y-%m-%d"),
            end=None if e is None else (e + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            auto_adjust=False,
            actions=False,
            progress=False,
            multi_level_index=False,
        )
        if raw is None or raw.empty:
            raise RuntimeError(f"yahoo: no data returned for {symbol}")
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.xs(symbol, axis=1, level=-1)
        return slice_dates(validate_ohlcv(normalize_columns(raw), symbol), s, e)


class CSVProvider(DataProvider):
    """Reads ``<directory>/<SYMBOL>.csv`` files with a date column and OHLCV columns."""

    name = "csv"

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def fetch(self, symbol: str, start: DataLike | None = None, end: DataLike | None = None) -> pd.DataFrame:
        path = self.directory / f"{symbol}.csv"
        if not path.exists():
            raise FileNotFoundError(f"csv provider: {path} not found")
        raw = normalize_columns(pd.read_csv(path))
        if "date" not in raw.columns:
            raise ValueError(f"{path}: no date column")
        raw = raw.set_index(pd.to_datetime(raw.pop("date")))
        return slice_dates(validate_ohlcv(raw, symbol), start, end)


class SyntheticProvider(DataProvider):
    """Deterministic geometric-Brownian-motion bars on the US business-day calendar.

    Each symbol gets its own reproducible random stream derived from ``seed`` and the
    symbol name, so results are identical across runs and machines. Intended for unit
    tests, CI and offline demos - *not* for drawing investment conclusions.
    """

    name = "synthetic"
    SIM_ORIGIN = pd.Timestamp("1995-01-02")

    DEFAULT_PARAMS = {
        "SPY": {"mu": 0.08, "sigma": 0.18, "start_price": 100.0},
        "QQQ": {"mu": 0.11, "sigma": 0.24, "start_price": 80.0},
    }

    def __init__(
        self,
        seed: int = 42,
        params: dict[str, dict[str, float]] | None = None,
        default_start: DataLike = "2005-01-01",
        default_end: DataLike = "2024-12-31",
        dividend_yield: float = 0.0,
    ):
        self.seed = int(seed)
        self.params = {**self.DEFAULT_PARAMS, **(params or {})}
        self.default_start = to_timestamp(default_start)
        self.default_end = to_timestamp(default_end)
        self.dividend_yield = float(dividend_yield)

    def fetch(self, symbol: str, start: DataLike | None = None, end: DataLike | None = None) -> pd.DataFrame:
        s = to_timestamp(start) or self.default_start
        e = to_timestamp(end) or self.default_end
        if s < self.SIM_ORIGIN:
            raise ValueError(f"synthetic data starts at {self.SIM_ORIGIN.date()}")
        # Always simulate from a fixed origin, with independent random streams per
        # component, so any requested range is an exact slice of the same path
        # (important for reproducibility and look-ahead tests).
        dates = pd.bdate_range(self.SIM_ORIGIN, max(e, self.default_end), name="date")
        p = {"mu": 0.07, "sigma": 0.2, "start_price": 50.0, **self.params.get(symbol, {})}

        sym_key = zlib.crc32(symbol.encode())

        def stream(component: int) -> np.random.Generator:
            return np.random.default_rng([self.seed, sym_key, component])

        n = len(dates)
        dt = 1.0 / 252.0
        vol = p["sigma"] * np.sqrt(dt)
        rets = (p["mu"] - 0.5 * p["sigma"] ** 2) * dt + vol * stream(0).standard_normal(n)
        rets[0] = 0.0
        close = p["start_price"] * np.exp(np.cumsum(rets))
        # Overnight gap is a fraction of the daily move; intraday range envelopes open/close.
        gap = 0.3 * vol * stream(1).standard_normal(n)
        prev_close = np.concatenate([[close[0]], close[:-1]])
        open_ = prev_close * np.exp(gap)
        spread = np.abs(stream(2).standard_normal(n)) * vol * 0.5
        high = np.maximum(open_, close) * (1 + spread)
        low = np.minimum(open_, close) * (1 - spread)
        volume = stream(3).integers(1_000_000, 50_000_000, n).astype(float)
        # Optional dividend yield: adj_close compounds a small daily accrual on top of close,
        # mimicking a back-adjusted total-return series.
        div_factor = (1 + self.dividend_yield) ** (np.arange(n) / 252.0)
        adj_close = close * div_factor  # factor is 1.0 at the origin; ratios are what matter

        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "adj_close": adj_close, "volume": volume},
            index=dates,
        )
        return slice_dates(validate_ohlcv(df, symbol), s, e)


PROVIDERS: dict[str, type[DataProvider]] = {
    YahooProvider.name: YahooProvider,
    CSVProvider.name: CSVProvider,
    SyntheticProvider.name: SyntheticProvider,
}


def make_provider(name: str, **options) -> DataProvider:
    """Instantiate a registered provider by name."""
    try:
        cls = PROVIDERS[name]
    except KeyError:
        raise ValueError(f"unknown data provider {name!r}; available: {sorted(PROVIDERS)}") from None
    return cls(**options)
