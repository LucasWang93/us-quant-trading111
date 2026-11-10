# us-quant-trading

A modular Python platform for quantitative research and backtesting on US stocks and
ETFs. Phase 1 covers SPY and QQQ: historical data, two strategies (moving-average
crossover and momentum), a next-bar-execution backtester with realistic costs,
performance metrics, charts and tests. It also includes a reproducible **real-data
SPY/QQQ study** covering April 2000 to October 2026 (see below).

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

## Real-data study: SPY/QQQ, 2000-04 to 2026-10

```bash
usquant study --config configs/study_spy_qqq.yaml
```

This downloads SPY, QQQ and the 13-week T-bill yield from Yahoo Finance, falling
back to `data/csv/` if Yahoo fails. It runs two strategies and three buy-and-hold
benchmarks through the same engine with the same costs, then writes a Markdown
report. The committed run is
[`docs/results/spy_qqq_study/report.md`](docs/results/spy_qqq_study/report.md)
(data retrieved 2026-11-10, 6,685 trading days, USD 100k start, IBKR-Fixed-like
commission and 2 bps slippage):

| | CAGR | Sharpe (rf=0) | Sharpe (T-bill) | max drawdown | round trips | fills |
|---|---:|---:|---:|---:|---:|---:|
| MA 50/200 crossover | 9.99% | 0.68 | 0.56 | -30.7% | 29 | 94 |
| 12-1 momentum rotation | 7.33% | 0.46 | 0.37 | -55.5% | 40 | 81 |
| SPY buy & hold | 8.13% | 0.50 | 0.41 | -55.2% | 0 | 1 |
| QQQ buy & hold | 8.11% | 0.43 | 0.36 | -81.2% | 0 | 1 |
| 50/50 SPY+QQQ (5-pt drift rebalance) | 8.42% | 0.48 | 0.39 | -66.8% | 0 | 30 |

Read the report before drawing conclusions:

* **Start date.** The window starts days after the March 2000 Nasdaq peak, which
  hurts QQQ buy-and-hold and the momentum strategy (it bought QQQ on
  2000-04-04). In 2010–2019 and 2020–2026, buy-and-hold QQQ had the highest CAGR of
  the five. The MA strategy's edge comes almost entirely from avoiding
  2000–2002 and 2008.
* **Costs.** At 25 bps slippage per fill, MA CAGR falls to 9.41%. Filling at the next
  close instead of the next open changes momentum CAGR from 7.33% to 8.18%.
* **Cash.** Uninvested cash earns 0% in the backtest.
* **Data.** The data are unofficial Yahoo Finance data. Dividends are verified
  independently (see the provenance and quality sections of the report).

This is a research result, not a recommendation, and not evidence that either
strategy will outperform in the future.

## Data sources, provenance and CSV fallback

Details are in [`docs/data.md`](docs/data.md).

* `yahoo`: Yahoo Finance through `yfinance`. Includes adjusted close, dividends and
  splits.
* `csv`: local vendor files with an explicit `price_basis`: `raw_with_adj_close`,
  `adjusted` or `raw`.
* `auto`: Yahoo first, then `data/csv/<SYMBOL>.csv`, per symbol.
* Every series gets a `<SYMBOL>.provenance.json` record (vendor, retrieval time,
  price basis, notes). Runs also store each series' checksum and a data-quality
  report, including a check that the adjusted close matches close plus cash
  dividends.

To import a file from another vendor, for example if Yahoo is unavailable or you
have licensed data:

```bash
usquant import-csv --file ~/Downloads/SPY.csv --symbol SPY --source "Vendor X export 2026-11-01"
```

Raw price data are not committed: Yahoo's terms restrict redistribution. Only
checksums, file hashes, provenance and derived results are.

To check a committed study against a fresh run:

```bash
usquant reproduce --summary docs/results/spy_qqq_study/summary.json
```

The comparison is exact if your cache files have the SHA-256 hashes recorded in
the report. A fresh Yahoo download only matches within a 1e-4 tolerance, because
Yahoo's adjusted closes vary by about 1e-6 between downloads (see
[`docs/data.md`](docs/data.md#reproducibility)).

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
docs/data.md             data sources, provenance, CSV import/fallback, quality checks
docs/results/            committed study reports (generated by `usquant study`)
examples/quickstart.py   using the library from Python
src/usquant/
  data/        providers (Yahoo, CSV, auto fallback, Synthetic), cache, provenance, quality checks,
               CSV import, T-bill rates, MarketData panel
  strategies/  Strategy interface; MACrossover; Momentum
  portfolio/   signals -> target weights (equal_active, fixed_slot)
  risk/        position / gross-exposure limits, drawdown circuit breaker
  backtest/    engine (next-bar execution), commission & slippage models, results/trades
  metrics/     CAGR, vol, Sharpe, Sortino, max DD, Calmar, win rates, turnover, alpha/beta/IR
  reporting/   matplotlib charts, run-folder writer
  broker/      paper-only broker interface (no implementation yet)
  study.py     multi-strategy vs benchmark study + Markdown report
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
* **Benchmark:** `backtest` uses frictionless buy-and-hold SPY entered on the same bar
  the strategy could first trade. `study` runs buy-and-hold SPY, QQQ and 50/50 through
  the engine with the same costs.
* **Warm-up:** `data.evaluation_start` lets indicators warm up on earlier data
  without trading.

## Tests

```bash
pytest
```

The suite needs no network access. Yahoo access is mocked and everything else uses
synthetic or hand-built data.

## Secrets

Never commit API keys or broker credentials. Providers or brokers that need them must
read them from environment variables. `.env` files are git-ignored.
