"""Event-driven daily backtesting engine."""

from usquant.backtest.benchmarks import static_portfolio
from usquant.backtest.costs import CommissionModel, SlippageModel
from usquant.backtest.engine import BacktestConfig, Backtester, buy_and_hold_equity
from usquant.backtest.result import BacktestResult, round_trip_trades

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "Backtester",
    "CommissionModel",
    "SlippageModel",
    "buy_and_hold_equity",
    "round_trip_trades",
    "static_portfolio",
]
