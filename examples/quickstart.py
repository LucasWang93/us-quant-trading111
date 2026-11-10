"""Programmatic usage example: run both Phase 1 strategies on SPY/QQQ.

    python examples/quickstart.py                 # Yahoo Finance (needs network + yfinance)
    python examples/quickstart.py --synthetic     # offline, deterministic fake data
"""

from __future__ import annotations

import sys

from usquant.backtest import BacktestConfig, Backtester, CommissionModel, SlippageModel
from usquant.data import DataStore, MarketData, SyntheticProvider, YahooProvider
from usquant.metrics import summarize
from usquant.portfolio import signals_to_weights
from usquant.reporting import format_metrics_table
from usquant.risk import RiskLimits, apply_weight_limits
from usquant.strategies import MACrossover, Momentum


def main() -> None:
    synthetic = "--synthetic" in sys.argv
    symbols = ["SPY", "QQQ"]
    if synthetic:
        frames = SyntheticProvider(seed=42).fetch_many(symbols, "2005-01-01", "2024-12-31")
    else:
        frames = DataStore("data/cache").load_many(YahooProvider(), symbols, "2005-01-01", "2024-12-31")
    data = MarketData.from_frames(frames, adjust=True)

    risk = RiskLimits(max_position_weight=1.0, max_gross_exposure=1.0)
    engine = Backtester(
        BacktestConfig(
            initial_capital=100_000,
            commission=CommissionModel(per_share=0.005, min_per_order=1.0),
            slippage=SlippageModel(bps=2.0),
        ),
        risk,
    )

    for strategy in (MACrossover(fast=50, slow=200), Momentum(lookback=252, skip=21, top_n=1)):
        weights = apply_weight_limits(signals_to_weights(strategy.generate_signals(data)), risk)
        result = engine.run(data, weights, benchmark="SPY", strategy_name=repr(strategy))
        print(f"\n## {strategy!r}\n")
        print(format_metrics_table(summarize(result)))


if __name__ == "__main__":
    main()
