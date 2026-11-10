# us-quant-trading

A modular Python platform for quantitative research and backtesting on US stocks and
ETFs. Phase 1 covers SPY and QQQ: historical data, two strategies (moving-average
crossover and momentum), a next-bar-execution backtester with realistic costs,
performance metrics, charts and tests.

> **No live trading.** This repository contains no broker connectivity and no code
> that places orders. A future Interactive Brokers integration is planned as
> **paper trading only** (see `docs/architecture.md`).

## Install

Requires Python 3.11+.

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -e ".[yahoo,dev]"
```

`yahoo` installs `yfinance` for free daily data. Without it you can still use the
`csv` and `synthetic` providers.

## Quick start

Download and cache daily bars (to `data/cache/yahoo/*.csv`):

```bash
usquant download --symbols SPY QQQ --start 2005-01-01 --end 2024-12-31
```

Run a backtest from a config file:

```bash
usquant backtest --config configs/ma_crossover_spy_qqq.yaml
```

```bash
usquant backtest --config configs/momentum_spy_qqq.yaml
```

Offline / CI run with deterministic synthetic data (results are meaningless for
investing, but the full pipeline runs):

```bash
usquant backtest --config configs/ma_crossover_spy_qqq.yaml --provider synthetic
```

Each run writes `results/<name>_<UTC timestamp>/` containing:

| File | Contents |
|---|---|
| `metrics.md` / `metrics.json` | strategy vs benchmark metrics, alpha/beta/IR |
| `equity.csv` | cash, holdings, equity, gross exposure, benchmark equity per day |
| `weights.csv`, `fills.csv`, `trades.csv` | realised weights, every fill with costs, round-trip trades |
| `equity.png`, `drawdown.png`, `weights.png` | charts |
| `config.yaml`, `metadata.json` | resolved config, git commit, library versions, input-data checksums |

Programmatic use: see `examples/quickstart.py`.

## Repository layout

```
configs/                 experiment definitions (YAML)
docs/architecture.md     design, data flow, extension points, roadmap
docs/assumptions.md      timing, adjusted prices/dividends, costs, metric definitions
examples/quickstart.py   using the library from Python
src/usquant/
  data/        DataProvider interface; Yahoo, CSV, Synthetic providers; CSV cache; MarketData panel
  strategies/  Strategy interface; MACrossover; Momentum
  portfolio/   signals -> target weights (equal_active, fixed_slot)
  risk/        position / gross-exposure limits, drawdown circuit breaker
  backtest/    engine (next-bar execution), commission & slippage models, results/trades
  metrics/     CAGR, vol, Sharpe, Sortino, max DD, Calmar, win rates, turnover, alpha/beta/IR
  reporting/   matplotlib charts, run-folder writer
  broker/      paper-only broker interface (no implementation yet)
  config.py, pipeline.py, cli.py
tests/                   pytest suite
```

## Key assumptions (summary)

The full list is in [`docs/assumptions.md`](docs/assumptions.md).

* **No look-ahead:** signals use data up to the close of day *t* and are filled at
  the **open of day *t+1*** (optionally the close of *t+1*). Tests enforce this.
* **Adjusted prices:** OHLC are back-adjusted with the vendor's `adj_close / close`
  factor, so returns include reinvested dividends and splits are neutral.
* **Costs:** IBKR-Fixed-like commissions (USD 0.005/share, USD 1 min, 1% cap) and 2 bps
  adverse slippage by default. Both are configurable.
* **Portfolio:** long-only, no leverage, integer shares, sells before buys, cash
  never negative, cash earns 0%.
* **Benchmark:** frictionless buy-and-hold SPY entered on the same bar the strategy
  could first trade.

## Tests

```bash
pytest
```

The suite needs no network access. Yahoo access is mocked and everything else uses
synthetic or hand-built data.

## Secrets

Never commit API keys or broker credentials. Providers or brokers that need them must
read them from environment variables. `.env` files are git-ignored.
