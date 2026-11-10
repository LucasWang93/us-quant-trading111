# Architecture

```
            configs/*.yaml
                  │  usquant.config (dataclasses, unknown keys rejected)
                  ▼
 ┌──────────── usquant.pipeline.run_experiment ────────────────────────────┐
 │                                                                          │
 │  data.providers ──► data.store (CSV cache) ──► data.panel.MarketData    │
 │  (yahoo/csv/auto/     + provenance sidecars    (adjusted, aligned)       │
 │   synthetic)          + data.quality checks                             │
 │                                                    │                     │
 │                                  strategies.Strategy.generate_signals    │
 │                                                    │  signals [0,1]      │
 │                                  portfolio.sizing.signals_to_weights     │
 │                                                    │  weights            │
 │                                  risk.apply_weight_limits                │
 │                                                    │  target weights     │
 │                    backtest.Backtester.run  ◄──────┘  (+ DrawdownGuard,  │
 │                    (next-bar execution, costs)         CommissionModel,  │
 │                                │                       SlippageModel)    │
 │                                ▼                                         │
 │                    BacktestResult ──► metrics.summarize                  │
 │                                │                │                        │
 │                                ▼                ▼                        │
 │                    reporting (CSV, JSON, Markdown, PNG, metadata)        │
 └──────────────────────────────────────────────────────────────────────────┘

 usquant.study ── runs several strategies + engine-run buy-and-hold benchmarks
                  on one dataset; sub-periods, cost scenarios, Markdown report

 broker/  ── interface only (paper-trading-only design; no connectivity in Phase 1)
```

## Design principles

1. **One-way data flow with narrow interfaces.** Each stage consumes plain pandas
   objects (wide `dates × symbols` frames) and produces the next. You can swap a
   provider, strategy, sizing rule or cost model without touching the other stages.
2. **Causality by construction.** Strategies may only look backwards. The engine is
   the only place that knows about time ordering, and it always acts one bar after a
   decision.
3. **Separation of alpha, sizing and risk.** Strategies say *what* they like
   (signals). Sizing turns that into *how much* (weights). Risk limits cap the
   result. The engine turns weights into share orders.
4. **Reproducibility.** Configs fully describe a run. Each run folder stores the
   resolved config, the git commit, library versions and checksums of the input data.
   Synthetic data is seeded per symbol.
5. **Safety.** No credentials in code or config. No order-routing code. The broker
   interface rejects anything other than paper mode.

## Extension points

| Want to add | Do this |
|---|---|
| New data vendor | Subclass `DataProvider`, implement `fetch`, register in `PROVIDERS`. Read API keys from env vars. |
| New strategy | Subclass `Strategy`, implement `generate_signals`, register in `STRATEGIES`, and add it to the look-ahead parametrised test. |
| New sizing (vol-target, risk parity) | Add a method in `portfolio/sizing.py` (it may need returns history: keep it causal). |
| New risk rule | Weight-level rules go in `risk/limits.py`. Path-dependent rules hook into the engine loop like `DrawdownGuard`. |
| Different cost model | Any callable with the `CommissionModel.__call__` / `SlippageModel.fill_price` signature. |

## Roadmap

* **Phase 2:** mean-reversion strategy, volatility-targeted sizing, walk-forward /
  parameter sweeps, HTML report, more ETFs and single stocks (with point-in-time
  universes).
* **Phase 3:** IBKR **paper** adapter implementing `broker.Broker` (via TWS/IB Gateway
  paper port, credentials from env), dry-run order generation from target weights,
  and reconciliation. Live trading stays out of scope unless explicitly decided later.
* **Phase 4:** index futures (continuous contracts, roll logic, multipliers, margin).
