"""Data provenance: where every price series came from and how it was adjusted.

Each cached or imported series has a JSON sidecar (``<SYMBOL>.provenance.json``)
next to its CSV. Backtest runs copy these records into ``metadata.json`` so any
reported number can be traced to a source, a retrieval time and a checksum.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

#: How ``open/high/low/close`` relate to traded prices for a source.
PRICE_BASIS = {
    # Yahoo: Close is split-adjusted (not the raw historical print) but NOT
    # dividend-adjusted; Adj Close is split- and dividend-adjusted.
    "yahoo": "split_adjusted_ohlc_with_total_return_adj_close",
    # File has an explicit adjusted-close column next to unadjusted/split-adjusted OHLC.
    "raw_with_adj_close": "ohlc_with_total_return_adj_close",
    # Every price column in the file is already split- and dividend-adjusted.
    "adjusted": "fully_adjusted_ohlc",
    # File has no adjustment at all: dividends are NOT included in returns.
    "raw": "unadjusted_ohlc_no_dividends",
    "synthetic": "synthetic",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Provenance:
    """Where a single-symbol daily series came from."""

    symbol: str
    source: str  # provider name: yahoo | csv | synthetic | ...
    vendor: str  # human-readable origin, e.g. "Yahoo Finance via yfinance 1.8.0"
    price_basis: str  # see PRICE_BASIS
    retrieved_utc: str = field(default_factory=utc_now)
    requested_start: str | None = None
    requested_end: str | None = None
    first_date: str | None = None
    last_date: str | None = None
    rows: int = 0
    has_dividends: bool = False
    has_splits: bool = False
    original_file: str | None = None
    original_sha256: str | None = None
    #: The on-disk file the backtest actually read, and its SHA-256. Exact
    #: reproduction of a run requires a file with this hash.
    cache_file: str | None = None
    cache_sha256: str | None = None
    notes: list[str] = field(default_factory=list)

    def describe_frame(self, df: pd.DataFrame) -> "Provenance":
        self.first_date = str(df.index[0].date())
        self.last_date = str(df.index[-1].date())
        self.rows = int(len(df))
        self.has_dividends = bool("dividends" in df.columns and (df["dividends"] > 0).any())
        self.has_splits = bool("splits" in df.columns and (df["splits"] > 0).any())
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Provenance":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def sidecar_path(csv_path: str | Path) -> Path:
    p = Path(csv_path)
    return p.with_name(f"{p.stem}.provenance.json")


def write_provenance(csv_path: str | Path, prov: Provenance) -> Path:
    path = sidecar_path(csv_path)
    path.write_text(json.dumps(prov.to_dict(), indent=2) + "\n")
    return path


def read_provenance(csv_path: str | Path) -> Provenance | None:
    path = sidecar_path(csv_path)
    if not path.exists():
        return None
    return Provenance.from_dict(json.loads(path.read_text()))
