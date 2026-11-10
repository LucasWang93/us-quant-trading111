"""Concrete market data providers.

* :class:`YahooProvider`   - free daily data via the optional ``yfinance`` package.
* :class:`CSVProvider`     - local ``{symbol}.csv`` files (e.g. exports from any vendor).
* :class:`FallbackProvider` - tries several providers in order (e.g. Yahoo, then CSV).
* :class:`SyntheticProvider` - deterministic random-walk data for tests and offline demos.

Adding a vendor (Polygon, Tiingo, IBKR historical bars, ...) means subclassing
:class:`~usquant.data.base.DataProvider` and registering it in :data:`PROVIDERS`.
API keys, if a vendor needs one, must be read from environment variables, never
from config files committed to the repository.
"""

from __future__ import annotations

import logging
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from usquant.data.base import DataLike, DataProvider, slice_dates, to_timestamp, validate_ohlcv
from usquant.data.provenance import PRICE_BASIS, file_sha256, read_provenance

log = logging.getLogger(__name__)

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
    "adjusted close": "adj_close",
    "volume": "volume",
    "dividends": "dividends",
    "dividend": "dividends",
    "stock splits": "splits",
    "splits": "splits",
    "split": "splits",
}

#: Accepted ``price_basis`` values for CSV files (see :mod:`usquant.data.provenance`).
CSV_PRICE_BASES = ("auto", "raw_with_adj_close", "adjusted", "raw")


def normalize_columns(df: pd.DataFrame, fill_adj_close: bool = True) -> pd.DataFrame:
    """Map vendor column names (``Adj Close``, ``Open``...) to canonical names.

    If no adjusted-close column exists and ``fill_adj_close`` is true, ``adj_close``
    is set equal to ``close``; callers must record what that means (see
    :class:`CSVProvider` ``price_basis``).
    """
    rename = {}
    for col in df.columns:
        key = str(col).strip().lower().replace("<", "").replace(">", "")
        if key in _COLUMN_ALIASES:
            rename[col] = _COLUMN_ALIASES[key]
    out = df.rename(columns=rename)
    if fill_adj_close and "adj_close" not in out.columns and "close" in out.columns:
        out["adj_close"] = out["close"]
    return out


class YahooProvider(DataProvider):
    """Daily bars from Yahoo Finance through ``yfinance`` (optional dependency).

    Requests OHLC *and* ``Adj Close`` (``auto_adjust=False``) plus dividend and
    split events (``actions=True``) so that the adjustment is explicit and can be
    cross-checked downstream (:func:`usquant.data.quality.dividend_consistency`).

    Yahoo's ``Close`` is **split-adjusted** but not dividend-adjusted, so it is not
    the exact historical print for dates before a split (QQQ split 2:1 in 2000).
    ``Adj Close`` additionally back-adjusts for cash distributions.
    """

    name = "yahoo"

    def fetch(self, symbol: str, start: DataLike | None = None, end: DataLike | None = None) -> pd.DataFrame:
        yf = self._yf()
        s, e = to_timestamp(start), to_timestamp(end)
        # yfinance treats `end` as exclusive; add a day so our interface is inclusive.
        raw = yf.download(
            symbol,
            start=None if s is None else s.strftime("%Y-%m-%d"),
            end=None if e is None else (e + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            auto_adjust=False,
            actions=True,
            progress=False,
            multi_level_index=False,
        )
        if raw is None or raw.empty:
            raise RuntimeError(f"yahoo: no data returned for {symbol}")
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.xs(symbol, axis=1, level=-1)
        return slice_dates(validate_ohlcv(normalize_columns(raw), symbol), s, e)

    @staticmethod
    def _yf():
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError("YahooProvider requires `pip install usquant[yahoo]` (yfinance)") from exc
        return yf

    def provenance(self, symbol, df, start=None, end=None):
        prov = super().provenance(symbol, df, start, end)
        version = getattr(self._yf(), "__version__", "unknown")
        prov.vendor = f"Yahoo Finance (query1/query2.finance.yahoo.com) via yfinance {version}"
        prov.price_basis = PRICE_BASIS["yahoo"]
        prov.notes = [
            "close/open/high/low are split-adjusted, not dividend-adjusted",
            "adj_close is split- and dividend-adjusted (Yahoo methodology, CRSP-style multiplier)",
            "Yahoo revises adj_close history whenever a new distribution is paid",
            "unofficial endpoint; no SLA; terms of use restrict redistribution of the raw data",
        ]
        return prov


class CSVProvider(DataProvider):
    """Reads ``<directory>/<SYMBOL>.csv`` (case-insensitive) with a date column and OHLC columns.

    ``price_basis`` says how to interpret the file - this matters because many
    vendor exports differ:

    ``auto`` (default)
        ``raw_with_adj_close`` if the file has an adjusted-close column, else ``raw``.
    ``raw_with_adj_close``
        OHLC as traded (or split-adjusted) plus an ``Adj Close`` column; the usual
        Yahoo-style export and the format written by ``usquant download``.
    ``adjusted``
        All OHLC columns are already split- and dividend-adjusted (e.g. Stooq,
        many paid vendors' "adjusted" files). ``adj_close`` is set to ``close``.
    ``raw``
        Unadjusted prices with no adjusted column. Returns then **exclude
        dividends** and a warning is logged.

    If ``<SYMBOL>.provenance.json`` exists next to the file (written by
    ``usquant import-csv`` or ``usquant download``) its description of the original
    source is carried into the run metadata.
    """

    name = "csv"

    def __init__(self, directory: str | Path = "data/csv", price_basis: str = "auto"):
        if price_basis not in CSV_PRICE_BASES:
            raise ValueError(f"price_basis must be one of {CSV_PRICE_BASES}")
        self.directory = Path(directory)
        self.price_basis = price_basis
        self._resolved_basis: dict[str, str] = {}

    def path_for(self, symbol: str) -> Path:
        exact = self.directory / f"{symbol}.csv"
        if exact.exists():
            return exact
        if self.directory.is_dir():
            for p in self.directory.iterdir():
                if p.suffix.lower() == ".csv" and p.stem.lower() == symbol.lower():
                    return p
        return exact

    def fetch(self, symbol: str, start: DataLike | None = None, end: DataLike | None = None) -> pd.DataFrame:
        path = self.path_for(symbol)
        if not path.exists():
            raise FileNotFoundError(f"csv provider: {path} not found")
        raw = normalize_columns(pd.read_csv(path), fill_adj_close=False)
        if "date" not in raw.columns:
            raise ValueError(f"{path}: no date column")
        basis = self.price_basis
        if basis == "auto":
            basis = "raw_with_adj_close" if "adj_close" in raw.columns else "raw"
        if basis == "raw_with_adj_close" and "adj_close" not in raw.columns:
            raise ValueError(f"{path}: price_basis=raw_with_adj_close but no adjusted-close column")
        if basis in ("adjusted", "raw"):
            raw["adj_close"] = raw["close"]
        if basis == "raw":
            log.warning(
                "%s: no adjusted close - returns will EXCLUDE dividends and splits will look like crashes", path
            )
        if "volume" not in raw.columns:
            raw["volume"] = 0.0
        self._resolved_basis[symbol] = basis
        raw = raw.set_index(pd.to_datetime(raw.pop("date")))
        return slice_dates(validate_ohlcv(raw, symbol), start, end)

    def provenance(self, symbol, df, start=None, end=None):
        prov = super().provenance(symbol, df, start, end)
        path = self.path_for(symbol)
        basis = self._resolved_basis.get(symbol, self.price_basis)
        prov.vendor = f"local CSV file {path}"
        prov.price_basis = PRICE_BASIS.get(basis, basis)
        prov.original_file = str(path)
        prov.original_sha256 = file_sha256(path)
        upstream = read_provenance(path)
        if upstream is not None:
            prov.vendor = f"{upstream.vendor} (imported to {path})"
            prov.notes = [f"upstream retrieved/imported {upstream.retrieved_utc}", *upstream.notes]
            if upstream.price_basis and upstream.price_basis != "unknown":
                prov.price_basis = upstream.price_basis
        if basis == "raw":
            prov.notes.append("WARNING: no adjusted close; dividends not included in returns")
        return prov


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

    def provenance(self, symbol, df, start=None, end=None):
        prov = super().provenance(symbol, df, start, end)
        prov.vendor = f"SyntheticProvider(seed={self.seed}) - random walk, NOT market data"
        prov.price_basis = PRICE_BASIS["synthetic"]
        return prov


class FallbackProvider(DataProvider):
    """Try several providers in order and use the first that succeeds per symbol.

    Typical use: Yahoo Finance first, then a directory of vendor CSV files when
    Yahoo is unreachable or rate-limited::

        provider: auto
        provider_options:
          sources:
            - {provider: yahoo}
            - {provider: csv, directory: data/csv}

    The provider actually used, and why earlier ones failed, is recorded in the
    series' provenance. When a cache is involved :class:`~usquant.data.store.DataStore`
    caches each source separately.
    """

    name = "auto"

    DEFAULT_SOURCES = ({"provider": "yahoo"}, {"provider": "csv", "directory": "data/csv"})

    def __init__(self, sources: list[dict] | None = None):
        specs = [dict(s) for s in (sources or self.DEFAULT_SOURCES)]
        if not specs:
            raise ValueError("FallbackProvider needs at least one source")
        self.sources: list[DataProvider] = []
        for spec in specs:
            name = spec.pop("provider")
            if name == self.name:
                raise ValueError("FallbackProvider sources cannot be nested")
            self.sources.append(make_provider(name, **spec))
        self.used: dict[str, DataProvider] = {}
        self.failures: dict[str, list[str]] = {}

    def fetch(self, symbol: str, start: DataLike | None = None, end: DataLike | None = None) -> pd.DataFrame:
        errors = []
        for provider in self.sources:
            try:
                df = provider.fetch(symbol, start, end)
            except Exception as exc:  # noqa: BLE001 - any failure means "try the next source"
                errors.append(f"{provider.name}: {type(exc).__name__}: {exc}")
                log.warning("%s: %s failed (%s); trying next source", symbol, provider.name, exc)
                continue
            self.used[symbol] = provider
            self.failures[symbol] = errors
            return df
        raise RuntimeError(f"all data sources failed for {symbol}: " + " | ".join(errors))

    def provenance(self, symbol, df, start=None, end=None):
        provider = self.used[symbol]
        prov = provider.provenance(symbol, df, start, end)
        for err in self.failures.get(symbol, []):
            prov.notes.append(f"fallback: earlier source failed - {err}")
        return prov


PROVIDERS: dict[str, type[DataProvider]] = {
    YahooProvider.name: YahooProvider,
    CSVProvider.name: CSVProvider,
    SyntheticProvider.name: SyntheticProvider,
    FallbackProvider.name: FallbackProvider,
}


def make_provider(name: str, **options) -> DataProvider:
    """Instantiate a registered provider by name."""
    try:
        cls = PROVIDERS[name]
    except KeyError:
        raise ValueError(f"unknown data provider {name!r}; available: {sorted(PROVIDERS)}") from None
    return cls(**options)
