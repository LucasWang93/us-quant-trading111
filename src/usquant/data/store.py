"""Local on-disk cache for downloaded bars, so experiments are reproducible offline."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pandas as pd

from usquant.data.base import DataLike, DataProvider, slice_dates, to_timestamp, validate_ohlcv
from usquant.data.provenance import Provenance, file_sha256, read_provenance, write_provenance

log = logging.getLogger(__name__)


class DataStore:
    """CSV cache at ``<root>/<provider>/<SYMBOL>.csv``.

    ``load`` serves from cache when the cached file covers the requested range
    (within ``tolerance_days`` at either end, to allow for weekends/holidays) and
    otherwise re-downloads the full requested range and overwrites the cache.
    Every downloaded file gets a ``<SYMBOL>.provenance.json`` sidecar holding the
    file's SHA-256. Callers always receive the data as re-read from disk, so a run
    that downloads and a later run served from the same cache file see identical
    numbers.
    """

    #: Providers whose data is already on disk and is never copied into the cache.
    LOCAL_PROVIDERS = frozenset({"csv"})

    def __init__(self, root: str | Path = "data/cache", tolerance_days: int = 5):
        self.root = Path(root)
        self.tolerance = pd.Timedelta(days=tolerance_days)

    def path(self, provider: DataProvider, symbol: str) -> Path:
        return self.root / provider.name / f"{symbol}.csv"

    def read(self, provider: DataProvider, symbol: str) -> pd.DataFrame | None:
        p = self.path(provider, symbol)
        if not p.exists():
            return None
        df = pd.read_csv(p, index_col="date", parse_dates=["date"])
        return validate_ohlcv(df, symbol)

    def write(self, provider: DataProvider, symbol: str, df: pd.DataFrame) -> Path:
        p = self.path(provider, symbol)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p, index_label="date", float_format="%.6f")
        return p

    def _covers(self, df: pd.DataFrame, start: pd.Timestamp | None, end: pd.Timestamp | None) -> bool:
        if start is not None and df.index[0] - start > self.tolerance:
            return False
        if end is not None and end - df.index[-1] > self.tolerance:
            return False
        return True

    def load_with_provenance(
        self,
        provider: DataProvider,
        symbol: str,
        start: DataLike | None = None,
        end: DataLike | None = None,
        refresh: bool = False,
    ) -> tuple[pd.DataFrame, Provenance]:
        """Load ``symbol`` and return ``(bars, provenance)``.

        * A :class:`~usquant.data.providers.FallbackProvider` is resolved source by
          source; each source has its own cache folder.
        * CSV sources are read directly (they are already local).
        * Remote sources are served from cache when it covers the range, otherwise
          downloaded and cached together with a provenance sidecar.
        """
        from usquant.data.providers import FallbackProvider

        if isinstance(provider, FallbackProvider):
            errors = []
            for source in provider.sources:
                try:
                    df, prov = self.load_with_provenance(source, symbol, start, end, refresh)
                except Exception as exc:  # noqa: BLE001 - try the next source
                    errors.append(f"{source.name}: {type(exc).__name__}: {exc}")
                    log.warning("%s: source %s failed (%s); trying next", symbol, source.name, exc)
                    continue
                prov.notes.extend(f"fallback: earlier source failed - {e}" for e in errors)
                return df, prov
            raise RuntimeError(f"all data sources failed for {symbol}: " + " | ".join(errors))

        s, e = to_timestamp(start), to_timestamp(end)
        if provider.name in self.LOCAL_PROVIDERS:
            df = provider.fetch(symbol, s, e)
            return df, provider.provenance(symbol, df, s, e)

        path = self.path(provider, symbol)
        cached = None if refresh else self.read(provider, symbol)
        if cached is not None and self._covers(cached, s, e):
            log.debug("cache hit %s/%s", provider.name, symbol)
            df = slice_dates(cached, s, e)
            prov = read_provenance(path) or Provenance(
                symbol=symbol, source=provider.name, vendor=f"{provider.name} (cache without provenance record)",
                price_basis="unknown", retrieved_utc="unknown",
            )
            current = file_sha256(path)
            if prov.cache_sha256 is not None and prov.cache_sha256 != current:
                log.warning("%s: cache file %s changed since it was downloaded", symbol, path)
                prov.notes.insert(0, f"WARNING: cache file modified after download (recorded {prov.cache_sha256[:16]}, "
                                     f"now {current[:16]})")
            prov.cache_file, prov.cache_sha256 = str(path), current
            prov.notes = [f"served from cache {path}", *prov.notes]
            prov.describe_frame(df)
            return df, prov
        log.info("fetching %s from %s", symbol, provider.name)
        fetched = provider.fetch(symbol, s, e)
        prov = provider.provenance(symbol, fetched, s, e)
        self.write(provider, symbol, fetched)
        # Return what is on disk, not the in-memory download: the cache is written at
        # fixed precision, so using the re-read frame makes the first run and every later
        # cache-served run bit-identical (same data checksum, same results).
        df = slice_dates(self.read(provider, symbol), s, e)
        prov.describe_frame(df)
        prov.cache_file, prov.cache_sha256 = str(path), file_sha256(path)
        write_provenance(path, prov)
        return df, prov

    def load(
        self,
        provider: DataProvider,
        symbol: str,
        start: DataLike | None = None,
        end: DataLike | None = None,
        refresh: bool = False,
    ) -> pd.DataFrame:
        return self.load_with_provenance(provider, symbol, start, end, refresh)[0]

    def load_many(
        self,
        provider: DataProvider,
        symbols: list[str],
        start: DataLike | None = None,
        end: DataLike | None = None,
        refresh: bool = False,
    ) -> dict[str, pd.DataFrame]:
        return {s: self.load(provider, s, start, end, refresh) for s in symbols}


def frame_checksum(df: pd.DataFrame) -> str:
    """Stable SHA-256 of a frame's values and index, recorded with each backtest run."""
    h = hashlib.sha256()
    h.update(pd.util.hash_pandas_object(df, index=True).to_numpy().tobytes())
    return h.hexdigest()[:16]
