"""Risk-free rate series used for excess-return Sharpe ratios.

The default is the CBOE 13-week US Treasury bill index (Yahoo symbol ``^IRX``),
quoted as an annualised discount yield in percent. It is converted to a daily
compounding rate ``(1 + y) ** (1 / 252) - 1``. Discount yield slightly understates
the bond-equivalent yield; the difference (a few bps) does not change conclusions.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from usquant.data.base import DataLike, to_timestamp
from usquant.data.provenance import Provenance, file_sha256, read_provenance, write_provenance

log = logging.getLogger(__name__)

TBILL_SYMBOL = "^IRX"


def _from_yahoo(symbol: str, start: pd.Timestamp | None, end: pd.Timestamp | None) -> pd.Series:
    import yfinance as yf

    raw = yf.download(
        symbol,
        start=None if start is None else start.strftime("%Y-%m-%d"),
        end=None if end is None else (end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        auto_adjust=False,
        progress=False,
        multi_level_index=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"yahoo: no data for {symbol}")
    s = raw["Close"].astype(float)
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    return s


def _read_cache(path: Path) -> pd.Series:
    return pd.read_csv(path, index_col="date", parse_dates=["date"])["close"].astype(float)


def load_rate_percent(
    symbol: str = TBILL_SYMBOL,
    start: DataLike | None = None,
    end: DataLike | None = None,
    cache_dir: str | Path = "data/cache",
    csv_dir: str | Path = "data/csv",
    refresh: bool = False,
) -> tuple[pd.Series, Provenance]:
    """Annualised yield in percent, indexed by date: cache -> Yahoo -> ``<csv_dir>/<name>.csv``.

    The CSV fallback needs columns ``date`` and ``close`` (percent yield).
    """
    s, e = to_timestamp(start), to_timestamp(end)
    fname = symbol.replace("^", "") + ".csv"
    cache = Path(cache_dir) / "yahoo" / fname
    if cache.exists() and not refresh:
        ser = _read_cache(cache)
        if (s is None or ser.index[0] - s <= pd.Timedelta(days=5)) and (e is None or e - ser.index[-1] <= pd.Timedelta(days=5)):
            prov = read_provenance(cache) or Provenance(symbol, "yahoo", "cache", "annualized_yield_percent")
            current = file_sha256(cache)
            if prov.cache_sha256 is not None and prov.cache_sha256 != current:
                prov.notes.insert(0, "WARNING: cache file modified after download")
            prov.cache_file, prov.cache_sha256 = str(cache), current
            prov.notes = [f"served from cache {cache}", *prov.notes]
            return ser.loc[s:e], prov
    errors = []
    try:
        ser = _from_yahoo(symbol, s, e).dropna()
        prov = Provenance(
            symbol=symbol, source="yahoo", vendor="Yahoo Finance via yfinance (CBOE 13-week T-bill index)",
            price_basis="annualized_discount_yield_percent",
            requested_start=None if s is None else str(s.date()), requested_end=None if e is None else str(e.date()),
            first_date=str(ser.index[0].date()), last_date=str(ser.index[-1].date()), rows=len(ser),
        )
        cache.parent.mkdir(parents=True, exist_ok=True)
        ser.rename("close").to_csv(cache, index_label="date", float_format="%.6f")
        ser = _read_cache(cache).loc[s:e]  # canonical on-disk values (see DataStore)
        prov.cache_file, prov.cache_sha256 = str(cache), file_sha256(cache)
        write_provenance(cache, prov)
        return ser, prov
    except Exception as exc:  # noqa: BLE001 - fall back to CSV
        errors.append(f"yahoo: {exc}")
        log.warning("risk-free %s from yahoo failed (%s); trying CSV", symbol, exc)
    path = Path(csv_dir) / fname
    if not path.exists():
        raise RuntimeError(f"risk-free rate {symbol} unavailable: {errors} and {path} missing")
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ser = df.set_index(pd.to_datetime(df["date"]))["close"].astype(float).sort_index().loc[s:e]
    prov = Provenance(symbol=symbol, source="csv", vendor=f"local CSV {path}", price_basis="annualized_yield_percent",
                      first_date=str(ser.index[0].date()), last_date=str(ser.index[-1].date()), rows=len(ser),
                      notes=[f"fallback: {err}" for err in errors])
    return ser, prov


def daily_rate(yield_percent: pd.Series, index: pd.DatetimeIndex, periods_per_year: int = 252) -> pd.Series:
    """Per-bar rate aligned to ``index``; a missing quote uses the last known value (never a future one)."""
    y = yield_percent.clip(lower=0.0) / 100.0
    aligned = y.reindex(index.union(y.index)).ffill().reindex(index).fillna(0.0)
    return (1 + aligned) ** (1 / periods_per_year) - 1
