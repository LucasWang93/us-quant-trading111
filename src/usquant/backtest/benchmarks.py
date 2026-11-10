"""Buy-and-hold benchmarks run through the same engine (same costs, same fills).

:func:`usquant.backtest.engine.buy_and_hold_equity` is a frictionless reference.
The portfolios here pay the same commissions and slippage, use integer shares and
enter on the same bar as the strategy, so strategy-vs-benchmark comparisons are
apples to apples.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from usquant.backtest.engine import BacktestConfig, Backtester
from usquant.backtest.result import BacktestResult
from usquant.data.panel import MarketData


def static_portfolio(
    data: MarketData,
    weights: dict[str, float],
    config: BacktestConfig,
    name: str | None = None,
    rebalance_threshold: float | None = None,
) -> BacktestResult:
    """Hold fixed ``weights`` from the first tradable bar to the end.

    ``rebalance_threshold=None`` = pure buy-and-hold (buy once, never trade again);
    a number re-balances back to target whenever any weight drifts further than that.
    """
    symbols = list(weights)
    missing = [s for s in symbols if s not in data.symbols]
    if missing:
        raise ValueError(f"no price data for benchmark symbols {missing}")
    target = pd.DataFrame({s: float(w) for s, w in weights.items()}, index=data.dates)
    cfg = replace(config, rebalance_threshold=rebalance_threshold)
    label = name or " / ".join(f"{w:.0%} {s}" for s, w in weights.items())
    return Backtester(cfg).run(data.select(symbols), target, benchmark=None, strategy_name=label)
