"""Import vendor CSV files as a fallback (or replacement) for downloaded data.

``usquant import-csv`` reads a CSV from any vendor (Yahoo export, Stooq, a broker,
CRSP extract...), maps its columns to the canonical schema, validates it, runs
the quality checks and writes ``<dest>/<SYMBOL>.csv`` plus a
``<SYMBOL>.provenance.json`` sidecar recording the original file's SHA-256, the
source you name and how its prices are adjusted. The ``csv`` and ``auto``
providers then pick it up.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from usquant.data.base import ACTION_COLUMNS, OHLCV_COLUMNS, validate_ohlcv
from usquant.data.provenance import PRICE_BASIS, Provenance, file_sha256, write_provenance
from usquant.data.providers import CSV_PRICE_BASES, normalize_columns
from usquant.data.quality import QualityReport, check_quality


def read_vendor_csv(path: str | Path, price_basis: str = "auto", date_format: str | None = None) -> tuple[pd.DataFrame, str, list[str]]:
    """Parse a vendor CSV into a validated canonical frame; return ``(frame, basis, notes)``."""
    if price_basis not in CSV_PRICE_BASES:
        raise ValueError(f"price_basis must be one of {CSV_PRICE_BASES}")
    raw = normalize_columns(pd.read_csv(path, thousands=","), fill_adj_close=False)
    if "date" not in raw.columns:
        raise ValueError(f"{path}: no date column (expected one of Date/Datetime/Timestamp)")
    missing = [c for c in ("open", "high", "low", "close") if c not in raw.columns]
    if missing:
        raise ValueError(f"{path}: missing price columns {missing}")
    notes: list[str] = []
    basis = price_basis
    if basis == "auto":
        basis = "raw_with_adj_close" if "adj_close" in raw.columns else "raw"
    if basis == "raw_with_adj_close" and "adj_close" not in raw.columns:
        raise ValueError(f"{path}: price_basis=raw_with_adj_close but the file has no adjusted-close column")
    if basis in ("adjusted", "raw"):
        if "adj_close" in raw.columns:
            notes.append(f"adjusted-close column ignored because price_basis={basis}")
        raw["adj_close"] = raw["close"]
    if basis == "raw":
        notes.append("WARNING: unadjusted prices - returns exclude dividends; splits appear as crashes")
    if "volume" not in raw.columns:
        raw["volume"] = 0.0
        notes.append("no volume column; volume set to 0")
    dates = pd.to_datetime(raw.pop("date"), format=date_format)
    frame = validate_ohlcv(raw.set_index(dates), "csv")
    return frame, basis, notes


def import_csv(
    file: str | Path,
    symbol: str,
    dest: str | Path = "data/csv",
    source: str = "unspecified",
    price_basis: str = "auto",
    date_format: str | None = None,
) -> tuple[Path, Provenance, QualityReport]:
    frame, basis, notes = read_vendor_csv(file, price_basis, date_format)
    quality = check_quality(frame, symbol)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{symbol}.csv"
    cols = list(OHLCV_COLUMNS) + [c for c in ACTION_COLUMNS if c in frame.columns]
    frame[cols].to_csv(out, index_label="date", float_format="%.6f")
    prov = Provenance(
        symbol=symbol,
        source="csv",
        vendor=source,
        price_basis=PRICE_BASIS.get(basis, basis),
        original_file=str(Path(file).resolve()),
        original_sha256=file_sha256(file),
        notes=[f"imported with usquant import-csv (price_basis={basis})", *notes],
    ).describe_frame(frame)
    write_provenance(out, prov)
    return out, prov, quality
