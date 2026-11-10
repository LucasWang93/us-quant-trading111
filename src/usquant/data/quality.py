"""Data-quality checks for daily bars, run before a backtest result is trusted.

None of these checks modifies data. They return findings so a report can show
them next to the results; hard schema errors are raised earlier by
:func:`usquant.data.base.validate_ohlcv`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd


@dataclass
class QualityReport:
    symbol: str
    rows: int
    first_date: str
    last_date: str
    #: Calendar gaps between consecutive bars longer than ``max_gap_days``.
    long_gaps: list[tuple[str, str, int]] = field(default_factory=list)
    #: Bars whose open/close lies outside [low, high].
    ohlc_inconsistent: int = 0
    zero_volume_bars: int = 0
    #: Bars with |adjusted close-to-close return| above ``extreme_move``.
    extreme_moves: list[tuple[str, float]] = field(default_factory=list)
    #: Bars where the price is unchanged from the previous bar on all of OHLC (stale data).
    stale_bars: int = 0
    #: ``adj_close / close`` should be non-decreasing in time for back-adjusted data.
    adj_factor_decreases: int = 0
    adj_factor_min: float = 1.0
    #: Dividend cross-check (only if the source supplied dividends).
    dividend_check: dict | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def dividend_consistency(df: pd.DataFrame, periods_per_year: int = 252) -> dict | None:
    """Rebuild a total-return series from ``close`` + cash ``dividends`` and compare it
    with the vendor's ``adj_close``.

    Reconstructed daily return: ``(close_t + div_t) / close_{t-1}`` (dividend
    reinvested at the ex-date close). The vendor's adjusted return should match it
    closely; a large annualised gap means the adjusted series is stale, mis-adjusted
    or missing distributions. Returns ``None`` if the frame has no dividend column.
    """
    if "dividends" not in df.columns:
        return None
    close, adj, div = df["close"], df["adj_close"], df["dividends"]
    recon = ((close + div) / close.shift(1)).iloc[1:]
    vendor = (adj / adj.shift(1)).iloc[1:]
    price_only = (close / close.shift(1)).iloc[1:]
    years = max(len(recon) / periods_per_year, 1e-9)

    def ann(growth: pd.Series) -> float:
        return float(np.prod(growth.to_numpy()) ** (1 / years) - 1)

    diff = (vendor - recon).abs()
    return {
        "dividend_events": int((div > 0).sum()),
        "cagr_price_only": ann(price_only),
        "cagr_reconstructed_total_return": ann(recon),
        "cagr_vendor_adj_close": ann(vendor),
        "annualized_gap_vendor_minus_reconstructed": ann(vendor) - ann(recon),
        "max_daily_abs_diff": float(diff.max()) if len(diff) else 0.0,
    }


def check_quality(
    df: pd.DataFrame,
    symbol: str = "?",
    max_gap_days: int = 5,
    extreme_move: float = 0.15,
    max_dividend_gap: float = 0.001,
) -> QualityReport:
    """Run all checks on a validated single-symbol frame (raw columns, not adjusted)."""
    rep = QualityReport(
        symbol=symbol, rows=len(df), first_date=str(df.index[0].date()), last_date=str(df.index[-1].date())
    )
    idx = df.index
    gaps = pd.Series(idx[1:] - idx[:-1], index=idx[1:]).dt.days
    for end, days in gaps[gaps > max_gap_days].items():
        start = idx[idx.get_loc(end) - 1]
        rep.long_gaps.append((str(start.date()), str(end.date()), int(days)))
    if rep.long_gaps:
        rep.warnings.append(
            f"{len(rep.long_gaps)} gap(s) longer than {max_gap_days} calendar days "
            "(expected only for market closures such as 2001-09-11)"
        )

    tol = 1e-6 * df["close"]
    bad = (df[["open", "close"]].max(axis=1) > df["high"] + tol) | (df[["open", "close"]].min(axis=1) < df["low"] - tol)
    rep.ohlc_inconsistent = int(bad.sum())
    rep.zero_volume_bars = int((df["volume"] <= 0).sum())

    ret = df["adj_close"].pct_change().dropna()
    big = ret[ret.abs() > extreme_move]
    rep.extreme_moves = [(str(d.date()), float(r)) for d, r in big.items()]
    if rep.extreme_moves:
        rep.warnings.append(f"{len(rep.extreme_moves)} daily move(s) beyond {extreme_move:.0%}; verify against a second source")

    ohlc = df[["open", "high", "low", "close"]]
    rep.stale_bars = int((ohlc.diff().abs().sum(axis=1) == 0).iloc[1:].sum())
    if rep.stale_bars:
        rep.warnings.append(f"{rep.stale_bars} bar(s) identical to the previous bar (possible stale data)")

    factor = df["adj_close"] / df["close"]
    rep.adj_factor_min = float(factor.min())
    rep.adj_factor_decreases = int((factor.diff() < -1e-5 * factor).sum())  # ignore CSV rounding
    if rep.adj_factor_decreases:
        rep.warnings.append(
            f"adjustment factor decreases on {rep.adj_factor_decreases} bar(s); back-adjusted data should "
            "only step up at ex-dates"
        )
    if rep.ohlc_inconsistent:
        rep.warnings.append(f"{rep.ohlc_inconsistent} bar(s) with open/close outside the high-low range")

    rep.dividend_check = dividend_consistency(df)
    if rep.dividend_check is not None:
        gap = rep.dividend_check["annualized_gap_vendor_minus_reconstructed"]
        if abs(gap) > max_dividend_gap:
            rep.warnings.append(
                f"adjusted close differs from close+dividends reconstruction by {gap:+.2%}/yr"
            )
    return rep
