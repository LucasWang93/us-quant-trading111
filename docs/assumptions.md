# Modelling assumptions

Read this before you trust any backtest number. Every item below is a choice that
changes results.

## 1. Timing and look-ahead bias

| Step | When | Data it may use |
|---|---|---|
| Signal / target weights | After the **close** of bar *t* | Bars `<= t` only |
| Order execution | **Open** of bar *t+1* (default) or close of *t+1* (`execution_price: close`) | Bar *t+1* price |
| Mark-to-market | Close of every bar | Bar close |

* Strategies are vectorised but **causal**: they only use rolling windows, `shift(k)` with
  `k >= 0`, and recursive EMAs. Tests check this by recomputing signals on data
  truncated at random dates (`test_no_look_ahead_truncation_invariance`) and by
  shocking future prices (`test_engine_has_no_look_ahead`).
* Monthly/weekly rebalancing triggers on the **first trading bar of the new period**,
  found by comparing the current date with the previous bar's date. A "last trading
  day of the month" rule would need to know tomorrow's date and is avoided.
* The drawdown circuit breaker looks at equity at the close of *t* and liquidates at
  the next bar, like any other signal.
* Each backtest uses the *intersection* of its symbols' trading calendars. Prices are
  never forward-filled.

## 2. Adjusted prices, splits and dividends

* Providers return `open/high/low/close` and a split- and dividend-adjusted
  `adj_close`. For Yahoo, OHLC are already **split-adjusted** but not
  dividend-adjusted. Each series' basis is recorded in its provenance (see
  [`data.md`](data.md)). With `adjust: true` (default) every price column is multiplied by
  `adj_close / close` (the vendor's cumulative adjustment factor), giving
  back-adjusted OHLC.
* **Effect:** returns approximate *total return with dividends reinvested on the
  ex-date*, and splits leave no artificial jumps. There is no separate cash dividend
  stream: dividends are already in the adjusted prices.
* **Caveats:**
  * Past adjusted prices are not the prices that actually traded. Per-share
    commissions and integer share counts are computed on adjusted prices, so they are
    slightly off for early dates. For SPY/QQQ the error is small next to the other
    cost assumptions.
  * Dividend withholding tax and the cash drag between ex-date and pay-date are
    ignored.
  * Yahoo's adjusted data is revised retroactively when new dividends are paid.
    Cached CSVs and data checksums in `metadata.json` let you reproduce a run. A
    `refresh` will change historical adjusted values.
* `adjust: false` uses raw prices, which **understates** ETF returns by roughly the
  dividend yield (about 1.3–2%/yr for SPY) and creates false crashes at splits.
  Don't use it for research.

## 3. Transaction costs

* **Commission** (`CommissionModel`): `per_share * |qty| + bps * notional`, with a
  floor of `min_per_order` and a cap of `max_pct_of_value * notional`. The default
  (USD 0.005/share, USD 1 min, 1% cap) approximates IBKR's US *Fixed* tier.
  Exchange, clearing and regulatory fees (SEC Section 31, FINRA TAF) are left out.
  Model them through `bps` if you need them.
* **Slippage** (`SlippageModel`): a fixed `bps` adverse to the trade direction
  (buy higher, sell lower) on the reference price. The default is 2 bps, which is
  reasonable for SPY/QQQ in retail size at the open. It does **not** scale with
  order size, volatility or volume, so treat it as optimistic for large or illiquid
  orders.
* The opening auction price is assumed fillable at the daily `open` print.

## 4. Portfolio and position sizing

* Long-only; target weights are fractions of current equity.
* Sizing (`portfolio.sizing`): `equal_active` splits equity across symbols with an
  "on" signal; `fixed_slot` gives each symbol a 1/N slot and keeps unused slots in
  cash.
* Risk limits clip each weight to `max_position_weight` and scale the row down to
  `max_gross_exposure`. Excess weight stays in **cash** (it is not redistributed).
* Integer shares by default (`fractional_shares: false`), rounded toward zero.
* Orders are executed **sells first, then buys**. If buys plus costs would exceed
  cash, they are scaled down, so cash never goes negative. No margin, no shorting.
* Rebalancing happens when the target changes or when any weight drifts more than
  `rebalance_threshold` from target. Orders below `min_trade_value` are skipped.
* **Cash earns 0%.** Set `risk_free_rate` to make Sharpe/Sortino/alpha excess-return
  based. The cash balance itself still earns nothing.

## 5. Benchmarks and evaluation window

* `usquant backtest`: frictionless buy-and-hold of `data.benchmark` (default SPY),
  bought at the open of bar 1, the first bar on which the strategy could trade. It
  uses the same adjusted (total-return) prices.
* `usquant study`: buy-and-hold portfolios (`backtest.benchmarks.static_portfolio`)
  run **through the same engine**. They pay the same commission and slippage, use
  integer shares and enter on the same bar as the strategies. A mix such as 50/50
  SPY/QQQ re-balances only when a weight drifts past its `rebalance_threshold`.
* `data.evaluation_start`: bars from `data.start` up to this date are indicator
  warm-up only. Signals are computed on the full history, then the backtest (and
  every benchmark) starts at `evaluation_start`. Without this, a 200-day or 12-month
  strategy would sit in cash for its first year while the benchmark is invested.

## 6. Metrics

* 252 bars per year. CAGR uses the number of elapsed bars.
* Sharpe and Sortino are annualised from daily excess returns. The risk-free rate is
  converted geometrically to a daily rate. Sortino uses root-mean-square downside
  deviation.
* Max drawdown is peak-to-trough on daily closes. Duration is the longest underwater
  stretch in bars.
* *Daily win rate* = share of positive days among days with a non-zero return.
  *Trade win rate* = share of closed round trips (flat → position → flat) with
  positive P&L net of costs. Open positions at the end are excluded from trade
  statistics; they are still in the equity curve.
* Alpha/beta are OLS (CAPM) on daily excess returns, with alpha annualised (× 252).
  The information ratio uses daily active returns.
* Turnover = total traded notional / average equity / years.

## 7. Known limitations

* Survivorship bias is not an issue for SPY/QQQ, but it **will** be once single
  stocks are added. That needs point-in-time constituents.
* No borrow costs, short selling, futures roll, FX, taxes or corporate-action cash
  flows beyond what the adjusted prices contain.
* Daily bars only: fills at the open/close print with fixed slippage, no partial
  fills, no market-impact or spread model, no intraday stops.
* Uninvested cash earns 0%. The study reports Sharpe both with rf = 0 and in excess
  of the 13-week T-bill.
* Free Yahoo data is unofficial and revised retroactively; see [`data.md`](data.md).
* The synthetic data provider exists only for tests and offline demos. Its numbers
  carry no information about real markets.
