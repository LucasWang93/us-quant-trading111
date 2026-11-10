"""Daily bar-by-bar portfolio backtester with next-bar execution.

Timeline for each bar ``t`` (daily data):

1. **Execute** the target weights decided at the close of bar ``t-1`` at bar ``t``'s
   execution price (``open`` by default; ``close`` optionally). Sells are processed
   before buys; buys are scaled down if cash would go negative.
2. **Mark to market** all positions at bar ``t``'s close.
3. **Decide** the target for bar ``t+1`` from ``target_weights`` row ``t`` (which the
   strategy computed using data up to and including bar ``t``'s close), after the
   drawdown circuit breaker has looked at bar ``t``'s closing equity.

Because a decision made with close ``t`` information can only trade at bar ``t+1``,
signal generation cannot leak future prices into fills.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from usquant.backtest.costs import CommissionModel, SlippageModel
from usquant.backtest.result import BacktestResult, round_trip_trades
from usquant.data.panel import MarketData
from usquant.risk.limits import DrawdownGuard, RiskLimits

_EPS = 1e-9


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    #: Price used to fill orders on the bar after the signal: "open" or "close".
    execution_price: str = "open"
    fractional_shares: bool = False
    #: Rebalance back to target when any position's weight drifts more than this
    #: (absolute) from its target. ``None`` = only trade when the target changes.
    rebalance_threshold: float | None = 0.05
    #: Skip individual orders smaller than this notional (avoids dust trades).
    min_trade_value: float = 0.0
    commission: CommissionModel = field(default_factory=CommissionModel)
    slippage: SlippageModel = field(default_factory=SlippageModel)

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.execution_price not in ("open", "close"):
            raise ValueError("execution_price must be 'open' or 'close'")
        if self.rebalance_threshold is not None and self.rebalance_threshold < 0:
            raise ValueError("rebalance_threshold must be >= 0 or None")


class Backtester:
    def __init__(self, config: BacktestConfig | None = None, risk: RiskLimits | None = None):
        self.config = config or BacktestConfig()
        self.risk = risk or RiskLimits()

    # ------------------------------------------------------------------ helpers
    def _order_quantities(self, target_w: np.ndarray, shares: np.ndarray, cash: float, px: np.ndarray) -> np.ndarray:
        equity = cash + float(shares @ px)
        desired = target_w * max(equity, 0.0) / px
        if not self.config.fractional_shares:
            desired = np.trunc(desired)
        delta = desired - shares
        if self.config.min_trade_value > 0:
            delta[np.abs(delta * px) < self.config.min_trade_value] = 0.0
        delta[np.abs(delta) < _EPS] = 0.0
        return delta

    def _buy_cost(self, qty: np.ndarray, px: np.ndarray) -> float:
        total = 0.0
        for q, p in zip(qty, px):
            if q > 0:
                fp = self.config.slippage.fill_price(q, p)
                total += q * fp + self.config.commission(q, fp)
        return total

    def _fit_buys_to_cash(self, buys: np.ndarray, px: np.ndarray, cash: float) -> np.ndarray:
        """Shrink buy quantities until their all-in cost fits in available cash."""
        buys = buys.copy()
        for _ in range(100):
            cost = self._buy_cost(buys, px)
            if cost <= cash + _EPS or not np.any(buys > 0):
                return buys
            scale = max(cash, 0.0) / cost * (1 - 1e-6)
            new = buys * scale
            if not self.config.fractional_shares:
                new = np.trunc(new)
                # Guarantee progress when truncation leaves quantities unchanged.
                stuck = (new == buys) & (buys > 0)
                new[stuck] -= 1
            buys = np.maximum(new, 0.0)
        return np.zeros_like(buys)

    # --------------------------------------------------------------------- run
    def run(
        self,
        data: MarketData,
        target_weights: pd.DataFrame,
        benchmark: str | None = None,
        strategy_name: str = "strategy",
    ) -> BacktestResult:
        cfg = self.config
        symbols = list(target_weights.columns)
        missing = [s for s in symbols if s not in data.symbols]
        if missing:
            raise ValueError(f"no price data for {missing}")
        dates = data.dates
        if len(dates) < 2:
            raise ValueError("need at least two bars to backtest")
        targets = target_weights.reindex(index=dates).fillna(0.0)[symbols].to_numpy(dtype=float)
        exec_px = (data.open if cfg.execution_price == "open" else data.close)[symbols].to_numpy(dtype=float)
        close_px = data.close[symbols].to_numpy(dtype=float)

        n_bars, n_sym = close_px.shape
        shares = np.zeros(n_sym)
        cash = float(cfg.initial_capital)
        guard = DrawdownGuard(self.risk)

        pending: np.ndarray | None = None
        last_executed: np.ndarray | None = None
        fills: list[dict] = []
        eq_rows = np.zeros((n_bars, 4))
        pos_rows = np.zeros((n_bars, n_sym))
        wt_rows = np.zeros((n_bars, n_sym))
        decided = np.zeros((n_bars, n_sym))
        halted = np.zeros(n_bars, dtype=bool)

        for i in range(n_bars):
            date = dates[i]
            # 1) execute decisions from the previous close at this bar's execution price
            if pending is not None:
                px = exec_px[i]
                equity_now = cash + float(shares @ px)
                cur_w = shares * px / equity_now if equity_now > 0 else np.zeros(n_sym)
                target_changed = last_executed is None or np.max(np.abs(pending - last_executed)) > _EPS
                drifted = (
                    cfg.rebalance_threshold is not None
                    and np.max(np.abs(cur_w - pending)) > cfg.rebalance_threshold
                )
                if target_changed or drifted:
                    delta = self._order_quantities(pending, shares, cash, px)
                    # Sells first to free cash.
                    for j in np.flatnonzero(delta < 0):
                        cash += self._fill(fills, date, symbols[j], delta[j], px[j], shares, j)
                    buys = self._fit_buys_to_cash(np.where(delta > 0, delta, 0.0), px, cash)
                    for j in np.flatnonzero(buys > 0):
                        cash += self._fill(fills, date, symbols[j], buys[j], px[j], shares, j)
                    last_executed = pending

            # 2) mark to market at the close
            holdings = shares * close_px[i]
            equity = cash + float(holdings.sum())
            gross = float(np.abs(holdings).sum())
            eq_rows[i] = (cash, holdings.sum(), equity, gross / equity if equity > 0 else 0.0)
            pos_rows[i] = shares
            wt_rows[i] = holdings / equity if equity > 0 else 0.0

            # 3) decide the target for the next bar using information up to this close
            is_halted = guard.update(equity)
            halted[i] = is_halted
            pending = np.zeros(n_sym) if is_halted else targets[i]
            decided[i] = pending

        idx = dates
        equity_df = pd.DataFrame(eq_rows, index=idx, columns=["cash", "holdings", "equity", "gross_exposure"])
        positions = pd.DataFrame(pos_rows, index=idx, columns=symbols)
        weights = pd.DataFrame(wt_rows, index=idx, columns=symbols)
        decided_df = pd.DataFrame(decided, index=idx, columns=symbols)
        fills_df = pd.DataFrame(
            fills,
            columns=["date", "symbol", "quantity", "price", "fill_price", "value", "commission", "slippage_cost"],
        )

        bench = None
        if benchmark is not None:
            if benchmark not in data.symbols:
                raise ValueError(f"benchmark {benchmark!r} not in market data")
            bench = buy_and_hold_equity(data, benchmark, cfg.initial_capital, cfg.execution_price)

        return BacktestResult(
            strategy_name=strategy_name,
            equity=equity_df,
            positions=positions,
            weights=weights,
            target_weights=decided_df,
            fills=fills_df,
            trades=round_trip_trades(fills_df),
            benchmark=bench,
            benchmark_symbol=benchmark,
            initial_capital=cfg.initial_capital,
            halted=pd.Series(halted, index=idx, name="halted"),
            drawdown_trips=guard.trip_count,
        )

    def _fill(self, fills: list, date, symbol: str, qty: float, ref_px: float, shares: np.ndarray, j: int) -> float:
        """Record a fill, update shares; return the cash delta."""
        fp = self.config.slippage.fill_price(qty, ref_px)
        comm = self.config.commission(qty, fp)
        value = qty * fp
        shares[j] += qty
        if abs(shares[j]) < _EPS:
            shares[j] = 0.0
        fills.append(
            {
                "date": date,
                "symbol": symbol,
                "quantity": qty,
                "price": ref_px,
                "fill_price": fp,
                "value": value,
                "commission": comm,
                "slippage_cost": abs(qty) * abs(fp - ref_px),
            }
        )
        return -value - comm


def buy_and_hold_equity(data: MarketData, symbol: str, initial_capital: float, execution_price: str = "open") -> pd.Series:
    """Frictionless buy-and-hold of ``symbol`` entered at the first bar a strategy
    could trade (bar 1, at the same execution price), so the comparison is fair."""
    close = data.close[symbol]
    entry = (data.open if execution_price == "open" else data.close)[symbol].iloc[1]
    eq = initial_capital * close / entry
    eq.iloc[0] = initial_capital
    return eq.rename(f"benchmark_{symbol}")
