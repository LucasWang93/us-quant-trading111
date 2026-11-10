"""Performance and risk statistics for equity curves.

Conventions: daily bars, ``periods_per_year = 252``. Returns are simple
(arithmetic) bar-to-bar returns of the equity curve. The risk-free rate is an
annual rate converted to a per-bar rate geometrically.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _per_bar_rf(rf_annual: float, periods: int) -> float:
    return (1 + rf_annual) ** (1 / periods) - 1


def total_return(equity: pd.Series) -> float:
    return float(equity.iloc[-1] / equity.iloc[0] - 1)


def cagr(equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """Compound annual growth rate, using the number of bars elapsed."""
    n = len(equity) - 1
    if n <= 0 or equity.iloc[0] <= 0:
        return 0.0
    growth = equity.iloc[-1] / equity.iloc[0]
    if growth <= 0:
        return -1.0
    return float(growth ** (periods_per_year / n) - 1)


def annualized_volatility(returns: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    if len(returns) < 2:
        return 0.0
    return float(returns.std(ddof=1) * math.sqrt(periods_per_year))


def sharpe_ratio(returns: pd.Series, rf_annual: float = 0.0, periods_per_year: int = TRADING_DAYS) -> float:
    excess = returns - _per_bar_rf(rf_annual, periods_per_year)
    sd = excess.std(ddof=1)
    if len(returns) < 2 or not np.isfinite(sd) or sd < 1e-12:
        return 0.0
    return float(excess.mean() / sd * math.sqrt(periods_per_year))


def sortino_ratio(returns: pd.Series, rf_annual: float = 0.0, periods_per_year: int = TRADING_DAYS) -> float:
    excess = returns - _per_bar_rf(rf_annual, periods_per_year)
    downside = np.minimum(excess.to_numpy(), 0.0)
    dd = math.sqrt(np.mean(downside**2)) if len(downside) else 0.0
    if dd < 1e-12:
        return 0.0
    return float(excess.mean() / dd * math.sqrt(periods_per_year))


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Fractional drawdown from the running peak (0 at new highs, negative below)."""
    return equity / equity.cummax() - 1.0


def max_drawdown(equity: pd.Series) -> float:
    """Maximum drawdown as a negative fraction (e.g. -0.25 for a 25% drawdown)."""
    return float(drawdown_series(equity).min())


def max_drawdown_duration(equity: pd.Series) -> int:
    """Longest stretch, in bars, spent below a previous equity peak."""
    underwater = (drawdown_series(equity) < 0).to_numpy()
    longest = cur = 0
    for u in underwater:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    return int(longest)


def calmar_ratio(equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    mdd = max_drawdown(equity)
    return 0.0 if mdd == 0 else float(cagr(equity, periods_per_year) / abs(mdd))


def daily_win_rate(returns: pd.Series) -> float:
    """Share of bars with a positive return among bars with a non-zero return."""
    active = returns[returns != 0]
    return float((active > 0).mean()) if len(active) else 0.0


def trade_stats(trades: pd.DataFrame) -> dict[str, float]:
    """Win rate, average win/loss and profit factor over closed round trips."""
    if trades is None or trades.empty:
        return {"num_trades": 0, "trade_win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0}
    pnl = trades["pnl"].astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_loss = -losses.sum()
    return {
        "num_trades": int(len(pnl)),
        "trade_win_rate": float(len(wins) / len(pnl)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else float("inf") if len(wins) else 0.0,
    }


def relative_stats(
    returns: pd.Series, bench_returns: pd.Series, rf_annual: float = 0.0, periods_per_year: int = TRADING_DAYS
) -> dict[str, float]:
    """CAPM alpha (annualised) and beta, correlation, tracking error and information ratio."""
    df = pd.concat([returns, bench_returns], axis=1, join="inner").dropna()
    if len(df) < 3:
        return {"alpha": 0.0, "beta": 0.0, "correlation": 0.0, "tracking_error": 0.0, "information_ratio": 0.0}
    rf = _per_bar_rf(rf_annual, periods_per_year)
    r, b = df.iloc[:, 0] - rf, df.iloc[:, 1] - rf
    var_b = b.var(ddof=1)
    beta = float(r.cov(b) / var_b) if var_b > 0 else 0.0
    alpha = float((r.mean() - beta * b.mean()) * periods_per_year)
    active = df.iloc[:, 0] - df.iloc[:, 1]
    te = float(active.std(ddof=1) * math.sqrt(periods_per_year))
    ir = float(active.mean() * periods_per_year / te) if te > 1e-12 else 0.0
    corr = float(df.iloc[:, 0].corr(df.iloc[:, 1])) if df.iloc[:, 0].std() > 0 and df.iloc[:, 1].std() > 0 else 0.0
    return {"alpha": alpha, "beta": beta, "correlation": corr, "tracking_error": te, "information_ratio": ir}


def equity_stats(
    equity: pd.Series, rf_annual: float = 0.0, periods_per_year: int = TRADING_DAYS
) -> dict[str, float]:
    rets = equity.pct_change().dropna()
    return {
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "final_equity": float(equity.iloc[-1]),
        "total_return": total_return(equity),
        "cagr": cagr(equity, periods_per_year),
        "volatility": annualized_volatility(rets, periods_per_year),
        "sharpe": sharpe_ratio(rets, rf_annual, periods_per_year),
        "sortino": sortino_ratio(rets, rf_annual, periods_per_year),
        "max_drawdown": max_drawdown(equity),
        "max_drawdown_duration_bars": max_drawdown_duration(equity),
        "calmar": calmar_ratio(equity, periods_per_year),
        "daily_win_rate": daily_win_rate(rets),
    }


def summarize(result, rf_annual: float = 0.0, periods_per_year: int = TRADING_DAYS) -> dict[str, dict]:
    """Full metrics report for a :class:`~usquant.backtest.result.BacktestResult`.

    Returns ``{"strategy": {...}, "benchmark": {...} | None, "relative": {...} | None}``.
    """
    eq = result.equity_curve
    strat = equity_stats(eq, rf_annual, periods_per_year)
    strat.update(trade_stats(result.trades))
    years = max((len(eq) - 1) / periods_per_year, 1e-9)
    traded = float(result.fills["value"].abs().sum()) if not result.fills.empty else 0.0
    strat["annual_turnover"] = traded / float(eq.mean()) / years
    strat["avg_gross_exposure"] = float(result.equity["gross_exposure"].mean())
    strat["total_commission"] = result.total_commission
    strat["total_slippage"] = result.total_slippage
    strat["num_fills"] = int(len(result.fills))
    strat["drawdown_halts"] = int(result.drawdown_trips)

    out: dict[str, dict | None] = {"strategy": strat, "benchmark": None, "relative": None}
    if result.benchmark is not None:
        out["benchmark"] = equity_stats(result.benchmark, rf_annual, periods_per_year)
        rel = relative_stats(result.returns.iloc[1:], result.benchmark_returns.iloc[1:], rf_annual, periods_per_year)
        rel["excess_cagr"] = strat["cagr"] - out["benchmark"]["cagr"]
        out["relative"] = rel
    return out
