"""Backtest result container and trade bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

_TRADE_COLUMNS = ["symbol", "entry_date", "exit_date", "bars_held", "fills", "cost_basis", "pnl", "return"]


def round_trip_trades(fills: pd.DataFrame, tol: float = 1e-9) -> pd.DataFrame:
    """Group fills into round trips per symbol.

    A round trip starts when a symbol's position moves away from flat and ends when
    it returns to flat. Rebalancing fills in between (adds / trims) belong to the
    same round trip. P&L includes slippage and commissions:
    ``pnl = sum(sell proceeds) - sum(buy cost) - sum(commissions)``.
    ``return = pnl / total buy notional``. Open (unclosed) positions are omitted
    here; their unrealised P&L is in the equity curve.
    """
    rows = []
    if fills.empty:
        return pd.DataFrame(columns=_TRADE_COLUMNS)
    for symbol, grp in fills.sort_values("date", kind="stable").groupby("symbol", sort=False):
        pos = 0.0
        cash = 0.0
        bought = 0.0
        entry = None
        n = 0
        for _, f in grp.iterrows():
            if abs(pos) < tol:
                entry, cash, bought, n = f["date"], 0.0, 0.0, 0
            pos += f["quantity"]
            cash += -f["value"] - f["commission"]
            if f["quantity"] > 0:
                bought += f["value"]
            n += 1
            if abs(pos) < tol:
                rows.append(
                    {
                        "symbol": symbol,
                        "entry_date": entry,
                        "exit_date": f["date"],
                        "fills": n,
                        "cost_basis": bought,
                        "pnl": cash,
                        "return": cash / bought if bought > 0 else 0.0,
                    }
                )
                pos = 0.0
    trades = pd.DataFrame(rows)
    if trades.empty:
        return pd.DataFrame(columns=_TRADE_COLUMNS)
    trades["bars_held"] = pd.NA  # filled in by BacktestResult, which knows the bar calendar
    return trades.sort_values(["exit_date", "symbol"]).reset_index(drop=True)[_TRADE_COLUMNS]


@dataclass
class BacktestResult:
    strategy_name: str
    equity: pd.DataFrame  # columns: cash, holdings, equity, gross_exposure
    positions: pd.DataFrame  # shares held at each close
    weights: pd.DataFrame  # realised weights at each close
    target_weights: pd.DataFrame  # weights decided at each close (executed next bar)
    fills: pd.DataFrame
    trades: pd.DataFrame
    benchmark: pd.Series | None
    benchmark_symbol: str | None
    initial_capital: float
    halted: pd.Series
    drawdown_trips: int = 0

    def __post_init__(self) -> None:
        # Express holding period in trading bars using the backtest calendar.
        if not self.trades.empty:
            pos = pd.Series(range(len(self.equity.index)), index=self.equity.index)
            self.trades["bars_held"] = (
                pos.loc[self.trades["exit_date"]].to_numpy() - pos.loc[self.trades["entry_date"]].to_numpy()
            )

    @property
    def equity_curve(self) -> pd.Series:
        return self.equity["equity"]

    @property
    def returns(self) -> pd.Series:
        return self.equity_curve.pct_change().fillna(0.0)

    @property
    def benchmark_returns(self) -> pd.Series | None:
        return None if self.benchmark is None else self.benchmark.pct_change().fillna(0.0)

    @property
    def total_commission(self) -> float:
        return float(self.fills["commission"].sum()) if not self.fills.empty else 0.0

    @property
    def total_slippage(self) -> float:
        return float(self.fills["slippage_cost"].sum()) if not self.fills.empty else 0.0
