"""Historical study: several strategies vs buy-and-hold benchmarks on real data.

One YAML file (``configs/study_spy_qqq.yaml``) defines the data window, the
strategies, the benchmarks, sub-periods and cost scenarios. :func:`run_study`
loads the data once, runs everything through the same engine and returns tables;
:func:`write_study` writes a Markdown report with full data provenance, charts
and machine-readable results.

Research only - nothing here connects to a broker or places orders.
"""

from __future__ import annotations

import json
import logging
import platform
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

import usquant
from usquant.backtest.benchmarks import static_portfolio
from usquant.backtest.costs import CommissionModel, SlippageModel
from usquant.backtest.engine import BacktestConfig, Backtester
from usquant.backtest.result import BacktestResult
from usquant.config import DataConfig, ExperimentConfig, PortfolioConfig, StrategyConfig, _check_keys, _datestr
from usquant.data.rates import TBILL_SYMBOL, daily_rate, load_rate_percent
from usquant.metrics.performance import (
    TRADING_DAYS,
    annualized_volatility,
    cagr,
    calmar_ratio,
    excess_sharpe_ratio,
    max_drawdown,
    max_drawdown_duration,
    sharpe_ratio,
    sortino_ratio,
)
from usquant.pipeline import LoadedData, evaluation_window, load_data, target_weights
from usquant.reporting.report import _git_commit, _jsonable
from usquant.risk.limits import RiskLimits

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- config
@dataclass
class StrategySpec:
    label: str
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    sizing: str = "equal_active"


@dataclass
class BenchmarkSpec:
    label: str
    weights: dict[str, float]
    rebalance_threshold: float | None = None


@dataclass
class PeriodSpec:
    label: str
    start: str | None = None
    end: str | None = None


@dataclass
class CostScenario:
    label: str
    commission: CommissionModel | None = None  # None = keep the study default
    slippage_bps: float | None = None
    execution_price: str | None = None


@dataclass
class StudyConfig:
    name: str
    data: DataConfig
    strategies: list[StrategySpec]
    benchmarks: list[BenchmarkSpec]
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskLimits = field(default_factory=RiskLimits)
    periods: list[PeriodSpec] = field(default_factory=list)
    cost_scenarios: list[CostScenario] = field(default_factory=list)
    #: Yahoo symbol of the risk-free yield (percent), or None to use 0%.
    risk_free_symbol: str | None = TBILL_SYMBOL
    output_dir: str = "results"
    raw: dict[str, Any] = field(default_factory=dict)


def _commission(v: Any) -> CommissionModel:
    if v == "zero":
        return CommissionModel.zero()
    return CommissionModel(**(v or {}))


def study_config_from_dict(raw: dict[str, Any]) -> StudyConfig:
    raw = dict(raw)
    _check_keys(
        "study", raw,
        {"name", "data", "strategies", "benchmarks", "backtest", "risk", "periods", "cost_scenarios",
         "risk_free_symbol", "output_dir"},
    )
    d = dict(raw["data"])
    _check_keys("data", d, set(DataConfig.__dataclass_fields__))
    for key in ("start", "end", "evaluation_start"):
        d[key] = _datestr(d.get(key))
    data = DataConfig(**d)

    strategies = []
    for s in raw.get("strategies") or []:
        _check_keys("strategies[]", s, {"label", "name", "params", "sizing"})
        strategies.append(StrategySpec(s["label"], s["name"], dict(s.get("params") or {}), s.get("sizing", "equal_active")))
    benchmarks = []
    for b in raw.get("benchmarks") or []:
        _check_keys("benchmarks[]", b, {"label", "weights", "rebalance_threshold"})
        benchmarks.append(BenchmarkSpec(b["label"], {k: float(v) for k, v in b["weights"].items()}, b.get("rebalance_threshold")))
    periods = []
    for p in raw.get("periods") or []:
        _check_keys("periods[]", p, {"label", "start", "end"})
        periods.append(PeriodSpec(p["label"], _datestr(p.get("start")), _datestr(p.get("end"))))

    b = dict(raw.get("backtest") or {})
    _check_keys("backtest", b, set(BacktestConfig.__dataclass_fields__))
    if "commission" in b:
        b["commission"] = _commission(b["commission"])
    if "slippage" in b:
        sl = b["slippage"]
        b["slippage"] = SlippageModel(**sl) if isinstance(sl, dict) else SlippageModel(bps=float(sl))
    scenarios = []
    for c in raw.get("cost_scenarios") or []:
        _check_keys("cost_scenarios[]", c, {"label", "commission", "slippage_bps", "execution_price"})
        scenarios.append(
            CostScenario(
                c["label"],
                commission=_commission(c["commission"]) if "commission" in c else None,
                slippage_bps=None if c.get("slippage_bps") is None else float(c["slippage_bps"]),
                execution_price=c.get("execution_price"),
            )
        )
    r = dict(raw.get("risk") or {})
    _check_keys("risk", r, set(RiskLimits.__dataclass_fields__))
    return StudyConfig(
        name=raw["name"],
        data=data,
        strategies=strategies,
        benchmarks=benchmarks,
        backtest=BacktestConfig(**b),
        risk=RiskLimits(**r),
        periods=periods,
        cost_scenarios=scenarios,
        risk_free_symbol=raw.get("risk_free_symbol", TBILL_SYMBOL),
        output_dir=raw.get("output_dir", "results"),
        raw=raw,
    )


def load_study_config(path: str | Path) -> StudyConfig:
    with open(path) as fh:
        return study_config_from_dict(yaml.safe_load(fh))


# -------------------------------------------------------------------------- metrics
def series_metrics(res: BacktestResult, rf_daily: pd.Series | None) -> dict[str, Any]:
    eq = res.equity_curve
    rets = eq.pct_change().dropna()
    years = (len(eq) - 1) / TRADING_DAYS
    fills = res.fills
    traded = float(fills["value"].abs().sum()) if not fills.empty else 0.0
    return {
        "start": str(eq.index[0].date()),
        "end": str(eq.index[-1].date()),
        "years": years,
        "final_equity": float(eq.iloc[-1]),
        "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1),
        "cagr": cagr(eq),
        "volatility": annualized_volatility(rets),
        "sharpe_rf0": sharpe_ratio(rets, 0.0),
        "sharpe_tbill": excess_sharpe_ratio(rets, rf_daily) if rf_daily is not None else None,
        "sortino_rf0": sortino_ratio(rets, 0.0),
        "max_drawdown": max_drawdown(eq),
        "max_drawdown_duration_bars": max_drawdown_duration(eq),
        "calmar": calmar_ratio(eq),
        "round_trips": int(len(res.trades)),
        "fills": int(len(fills)),
        "fills_per_year": len(fills) / years if years > 0 else 0.0,
        "avg_exposure": float(res.equity["gross_exposure"].mean()),
        "annual_turnover": traded / float(eq.mean()) / years if years > 0 else 0.0,
        "commission_paid": res.total_commission,
        "slippage_paid": res.total_slippage,
    }


def period_metrics(res: BacktestResult, start: str | None, end: str | None, rf_daily: pd.Series | None) -> dict:
    eq = res.equity_curve.loc[start:end]
    if len(eq) < 2:
        return {}
    rets = eq.pct_change().dropna()
    fills = res.fills
    in_period = fills[(fills["date"] >= eq.index[0]) & (fills["date"] <= eq.index[-1])] if not fills.empty else fills
    return {
        "start": str(eq.index[0].date()),
        "end": str(eq.index[-1].date()),
        "cagr": cagr(eq),
        "sharpe_rf0": sharpe_ratio(rets, 0.0),
        "sharpe_tbill": excess_sharpe_ratio(rets, rf_daily) if rf_daily is not None else None,
        "max_drawdown": max_drawdown(eq),
        "fills": int(len(in_period)),
    }


# ----------------------------------------------------------------------------- run
@dataclass
class StudyResult:
    config: StudyConfig
    loaded: LoadedData
    results: dict[str, BacktestResult]
    kinds: dict[str, str]  # label -> "strategy" | "benchmark"
    metrics: dict[str, dict]
    periods: dict[str, dict[str, dict]]
    costs: dict[str, dict[str, dict]]
    rf_provenance: dict | None
    rf_mean: float | None


def _experiment(cfg: StudyConfig, spec: StrategySpec, backtest: BacktestConfig) -> ExperimentConfig:
    return ExperimentConfig(
        name=spec.label,
        data=cfg.data,
        strategy=StrategyConfig(spec.name, spec.params),
        portfolio=PortfolioConfig(spec.sizing),
        risk=cfg.risk,
        backtest=backtest,
    )


def _run_strategy(cfg: StudyConfig, spec: StrategySpec, loaded: LoadedData, backtest: BacktestConfig) -> BacktestResult:
    exp = _experiment(cfg, spec, backtest)
    weights = target_weights(exp, loaded.market)
    data, weights = evaluation_window(loaded.market, weights, cfg.data.evaluation_start)
    return Backtester(backtest, cfg.risk).run(data, weights, benchmark=None, strategy_name=spec.label)


def _apply_scenario(base: BacktestConfig, sc: CostScenario) -> BacktestConfig:
    out = base
    if sc.commission is not None:
        out = replace(out, commission=sc.commission)
    if sc.slippage_bps is not None:
        out = replace(out, slippage=SlippageModel(bps=sc.slippage_bps))
    if sc.execution_price is not None:
        out = replace(out, execution_price=sc.execution_price)
    return out


def run_study(cfg: StudyConfig, loaded: LoadedData | None = None) -> StudyResult:
    bench_syms = [s for b in cfg.benchmarks for s in b.weights]
    loaded = loaded or load_data(cfg.data, extra_symbols=bench_syms)
    eval_data = loaded.market.between(cfg.data.evaluation_start, None)

    rf_daily, rf_prov, rf_mean = None, None, None
    if cfg.risk_free_symbol:
        try:
            pct, prov = load_rate_percent(
                cfg.risk_free_symbol, cfg.data.start, cfg.data.end, cache_dir=cfg.data.cache_dir,
                refresh=cfg.data.refresh,
            )
            rf_daily = daily_rate(pct, eval_data.dates)
            rf_prov = prov.to_dict()
            rf_mean = float((1 + rf_daily.iloc[1:]).prod() ** (TRADING_DAYS / max(len(rf_daily) - 1, 1)) - 1)
        except Exception as exc:  # noqa: BLE001 - report without excess Sharpe rather than fail
            log.warning("risk-free rate unavailable (%s); excess-return Sharpe omitted", exc)
            rf_prov = {"error": str(exc)}

    results: dict[str, BacktestResult] = {}
    kinds: dict[str, str] = {}
    for spec in cfg.strategies:
        results[spec.label] = _run_strategy(cfg, spec, loaded, cfg.backtest)
        kinds[spec.label] = "strategy"
    for b in cfg.benchmarks:
        results[b.label] = static_portfolio(eval_data, b.weights, cfg.backtest, b.label, b.rebalance_threshold)
        kinds[b.label] = "benchmark"

    metrics = {label: series_metrics(r, rf_daily) for label, r in results.items()}
    periods = {
        p.label: {label: period_metrics(r, p.start, p.end, rf_daily) for label, r in results.items()}
        for p in cfg.periods
    }
    costs: dict[str, dict[str, dict]] = {}
    for sc in cfg.cost_scenarios:
        bt = _apply_scenario(cfg.backtest, sc)
        row = {}
        for spec in cfg.strategies:
            row[spec.label] = series_metrics(_run_strategy(cfg, spec, loaded, bt), rf_daily)
        for b in cfg.benchmarks:
            row[b.label] = series_metrics(static_portfolio(eval_data, b.weights, bt, b.label, b.rebalance_threshold), rf_daily)
        costs[sc.label] = row
    return StudyResult(cfg, loaded, results, kinds, metrics, periods, costs, rf_prov, rf_mean)


# -------------------------------------------------------------------------- report
def _pct(v: float | None, digits: int = 2) -> str:
    return "n/a" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:.{digits}f}%"


def _num(v: float | None, digits: int = 2) -> str:
    return "n/a" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{digits}f}"


def _commission_text(c: CommissionModel) -> str:
    if c.per_share == 0 and c.min_per_order == 0 and c.bps == 0:
        return "none"
    parts = []
    if c.per_share:
        parts.append(f"USD {c.per_share:g}/share")
    if c.bps:
        parts.append(f"{c.bps:g} bps of notional")
    if c.min_per_order:
        parts.append(f"min USD {c.min_per_order:g}/order")
    if c.max_pct_of_value is not None:
        parts.append(f"max {c.max_pct_of_value:.0%} of trade value")
    return ", ".join(parts)


def format_report(sr: StudyResult, created_utc: str, git_commit: str | None) -> str:
    cfg, m = sr.config, sr.metrics
    bt = cfg.backtest
    labels = list(sr.results)
    first = next(iter(m.values()))
    L: list[str] = []
    L += [
        f"# {cfg.name}: SPY/QQQ historical backtest",
        "",
        f"Generated {created_utc} by `usquant study` (usquant {usquant.__version__}, git `{git_commit or 'unknown'}`).",
        "Research output only. No live or paper orders are placed by this code.",
        "",
        f"**Evaluation window:** {first['start']} to {first['end']} ({first['years']:.1f} years, "
        f"{len(sr.results[labels[0]].equity_curve):,} trading days). "
        f"Indicator warm-up data from {cfg.data.start}. Starting capital USD {bt.initial_capital:,.0f}.",
        "",
        "## Headline results",
        "",
        "Costs included (see *Transaction costs*). Sharpe (rf=0) uses raw daily returns; Sharpe (T-bill) uses "
        "daily returns in excess of the 13-week T-bill"
        + (f" (average {_pct(sr.rf_mean)}/yr over the window)." if sr.rf_mean is not None else " (unavailable)."),
        "",
        "| | type | CAGR | total return | volatility | Sharpe (rf=0) | Sharpe (T-bill) | Sortino | max drawdown "
        "| longest underwater (days) | round trips | fills (orders) | avg exposure | costs paid (USD) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label in labels:
        x = m[label]
        L.append(
            f"| {label} | {sr.kinds[label]} | {_pct(x['cagr'])} | {_pct(x['total_return'], 0)} | {_pct(x['volatility'])} "
            f"| {_num(x['sharpe_rf0'])} | {_num(x['sharpe_tbill'])} | {_num(x['sortino_rf0'])} | {_pct(x['max_drawdown'])} "
            f"| {x['max_drawdown_duration_bars']:,} | {x['round_trips']} | {x['fills']} | {_pct(x['avg_exposure'], 0)} "
            f"| {x['commission_paid'] + x['slippage_paid']:,.0f} |"
        )
    L += [
        "",
        "*Round trips* = closed trades (flat -> position -> flat) per symbol; *fills* = individual executed orders, "
        "including rebalancing adds/trims and any position still open at the end. Final equity: "
        + "; ".join(f"{k} USD {v['final_equity']:,.0f}" for k, v in m.items())
        + ".",
        "",
        "![Equity curves](equity.png)",
        "",
        "![Drawdowns](drawdown.png)",
        "",
    ]
    if sr.periods:
        L += ["## Sub-periods", "",
              "Metrics of the same runs, measured inside each window (positions carried in from the previous window).", ""]
        L += ["| period | " + " | ".join(labels) + " |", "|---|" + "---:|" * len(labels)]
        for plabel, rows in sr.periods.items():
            for key, name, f in (("cagr", "CAGR", _pct), ("sharpe_rf0", "Sharpe (rf=0)", _num),
                                 ("max_drawdown", "max DD", _pct), ("fills", "fills", lambda v: str(v))):
                cells = [f(rows[l].get(key)) if rows[l] else "" for l in labels]
                L.append(f"| {plabel} {name} | " + " | ".join(cells) + " |")
        L.append("")
    if sr.costs:
        L += ["## Cost and execution sensitivity", "",
              "Each row re-runs the full window with only the stated change; the default row matches the headline table.", ""]
        L += ["| scenario | " + " | ".join(f"{l} CAGR / Sharpe" for l in labels) + " |", "|---|" + "---:|" * len(labels)]
        for slabel, row in sr.costs.items():
            L.append(f"| {slabel} | " + " | ".join(f"{_pct(row[l]['cagr'])} / {_num(row[l]['sharpe_rf0'])}" for l in labels) + " |")
        L.append("")

    # Provenance
    L += ["## Data provenance", "",
          "| symbol | source | vendor | price basis | first bar | last bar | rows | retrieved (UTC) | checksum |",
          "|---|---|---|---|---|---|---:|---|---|"]
    for sym, p in sr.loaded.provenance.items():
        L.append(
            f"| {sym} | {p['source']} | {p['vendor']} | {p['price_basis']} | {p['first_date']} | {p['last_date']} "
            f"| {p['rows']:,} | {p['retrieved_utc']} | `{sr.loaded.checksums[sym]}` |"
        )
    if sr.rf_provenance and "error" not in sr.rf_provenance:
        p = sr.rf_provenance
        L.append(f"| {p['symbol']} (risk-free) | {p['source']} | {p['vendor']} | {p['price_basis']} | {p['first_date']} "
                 f"| {p['last_date']} | {p['rows']:,} | {p['retrieved_utc']} | |")
    L += ["", "Notes recorded with the data:", ""]
    for sym, p in sr.loaded.provenance.items():
        for n in p.get("notes", []):
            L.append(f"* {sym}: {n}")
    L += ["", "The checksum is a SHA-256 prefix of the validated input frame (`usquant.data.frame_checksum`); "
          "a re-download that changes any historical value changes it.", ""]

    L += ["### Data-quality checks", "",
          "| symbol | gaps > 5 days | open/close outside high-low | zero-volume bars | moves > 15% | stale bars "
          "| dividend events | price-only CAGR | close+dividends CAGR | vendor adj-close CAGR | adj vs reconstruction |",
          "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|"]
    for sym, q in sr.loaded.quality.items():
        gaps = ", ".join(f"{a}->{b}" for a, b, _ in q["long_gaps"]) or "none"
        moves = ", ".join(f"{d} ({r:+.1%})" for d, r in q["extreme_moves"]) or "none"
        dc = q.get("dividend_check") or {}
        L.append(
            f"| {sym} | {gaps} | {q['ohlc_inconsistent']} | {q['zero_volume_bars']} | {moves} | {q['stale_bars']} "
            f"| {dc.get('dividend_events', 'n/a')} | {_pct(dc.get('cagr_price_only'))} "
            f"| {_pct(dc.get('cagr_reconstructed_total_return'))} | {_pct(dc.get('cagr_vendor_adj_close'))} "
            f"| {_pct(dc.get('annualized_gap_vendor_minus_reconstructed'), 4)}/yr |"
        )
    L += ["",
          "The dividend check rebuilds a total-return series from the unadjusted close and the cash distributions "
          "(`(close_t + div_t) / close_{t-1}`) and compares it with the vendor's adjusted close. A gap near zero "
          "confirms the adjusted series contains every distribution. Data cover "
          f"{cfg.data.start} to {cfg.data.end}, so these CAGRs include the warm-up period.", ""]

    # Adjusted prices / costs / limitations
    L += [
        "## Adjusted prices",
        "",
        "* Backtest prices are back-adjusted: every OHLC column is multiplied by `adj_close / close`. Returns therefore "
        "include dividends reinvested on the ex-date and splits (QQQ 2:1 on 2000-03-20) cause no jumps.",
        "* Buy-and-hold benchmarks use the same adjusted prices, so they are total-return benchmarks.",
        "* Adjusted historical prices are not the prices that printed. Per-share commissions and integer share counts "
        "are computed on adjusted prices; for early years this slightly misstates share counts (immaterial here).",
        "* Dividend withholding tax, the ex-date to pay-date cash lag and taxes in general are ignored.",
        "",
        "## Transaction costs",
        "",
        f"* Commission: {_commission_text(bt.commission)} (approximates the IBKR US *Fixed* tier; "
        "exchange, clearing and regulatory fees are excluded).",
        f"* Slippage: {bt.slippage.bps:g} bps adverse on every fill (buys pay more, sells receive less), "
        "independent of order size or volatility.",
        f"* Orders under USD {bt.min_trade_value:,.0f} are skipped; positions are re-balanced when a weight drifts "
        + (f"more than {bt.rebalance_threshold * 100:g} percentage points from target."
           if bt.rebalance_threshold is not None else "only when the target changes."),
        "* Buy-and-hold benchmarks pay the same costs (on their single entry order, or on drift re-balances for the "
        "50/50 mix).",
        "",
        "## Execution model and limitations",
        "",
        f"* Signals use data up to the close of day *t*; orders fill at the **{bt.execution_price}** of day *t+1*. "
        "The opening print is assumed fully fillable at that price (plus slippage) for the whole order.",
        "* No intraday data, no bid/ask quotes, no partial fills, no market impact model, no order queueing, no halts "
        "or circuit-breaker days beyond what the daily bars show.",
        f"* {'Fractional' if bt.fractional_shares else 'Integer'} shares, long only, no leverage, no shorting. "
        "Sells execute before buys; buys are scaled down so cash never goes negative.",
        "* **Uninvested cash earns 0%.** This penalises strategies that spend time in cash (the T-bill rate averaged "
        f"{_pct(sr.rf_mean)}/yr over the window); Sharpe (T-bill) still subtracts the T-bill rate from every day's return."
        if sr.rf_mean is not None else "* **Uninvested cash earns 0%.**",
        "* Free Yahoo data is unofficial (no SLA, revised retroactively). Results should be re-checked against a "
        "licensed source (e.g. CRSP, a broker's historical bars) before any decision is made.",
        "* Strategy parameters are textbook defaults chosen before this run, not optimised; still, two strategies "
        "on two highly correlated ETFs over one history is a small sample. Past performance does not predict "
        "future results.",
        "* The evaluation start date matters: starting near the March 2000 Nasdaq peak heavily penalises QQQ "
        "buy-and-hold. See *Sub-periods*.",
        "",
        "## Reproduce",
        "",
        "```bash",
        'pip install -e ".[yahoo,dev]"',
        "```",
        "",
        "```bash",
        "usquant study --config configs/study_spy_qqq.yaml",
        "```",
        "",
        "Without network access, put vendor CSVs in `data/csv/` (see the README, *CSV import*) and the `auto` "
        "provider falls back to them; the provenance table then shows `csv` as the source.",
        "",
    ]
    return "\n".join(L)


def _plot(sr: StudyResult, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from usquant.metrics.performance import drawdown_series
    from usquant.reporting.plots import GRID, SERIES_COLORS, SURFACE, TEXT_MUTED, _style

    for kind in ("equity", "drawdown"):
        fig, ax = plt.subplots(figsize=(11, 4.6 if kind == "equity" else 3.8), dpi=120)
        fig.patch.set_facecolor(SURFACE)
        for i, (label, res) in enumerate(sr.results.items()):
            eq = res.equity_curve
            y = eq if kind == "equity" else drawdown_series(eq) * 100
            dashed = sr.kinds[label] == "benchmark"
            ax.plot(y.index, y.to_numpy(), color=SERIES_COLORS[i % len(SERIES_COLORS)], linewidth=1.6 if not dashed else 1.2,
                    linestyle="--" if dashed else "-", label=label)
        if kind == "equity":
            ax.set_yscale("log")
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
            ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
            _style(ax, "Equity, USD (log scale, costs included)")
        else:
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
            _style(ax, "Drawdown from running peak")
            ax.axhline(0, color=GRID, linewidth=0.8)
        ax.legend(frameon=False, fontsize=8, labelcolor=TEXT_MUTED, loc="upper left" if kind == "equity" else "lower left",
                  ncol=2)
        fig.tight_layout()
        fig.savefig(out / f"{kind}.png", facecolor=SURFACE)
        plt.close(fig)


def write_study(sr: StudyResult, out_root: str | Path | None = None, run_dir: str | Path | None = None,
                plots: bool = True) -> Path:
    """Write ``report.md``, ``summary.json``, charts and per-series CSVs; return the folder."""
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = created.replace("-", "").replace(":", "")
    out = Path(run_dir) if run_dir else Path(out_root or sr.config.output_dir) / f"{sr.config.name}_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    commit = _git_commit()

    (out / "report.md").write_text(format_report(sr, created, commit))
    summary = {
        "name": sr.config.name,
        "created_utc": created,
        "usquant_version": usquant.__version__,
        "git_commit": commit,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "config": sr.config.raw,
        "metrics": sr.metrics,
        "periods": sr.periods,
        "cost_scenarios": sr.costs,
        "data_provenance": sr.loaded.provenance,
        "data_checksums": sr.loaded.checksums,
        "data_quality": sr.loaded.quality,
        "risk_free_provenance": sr.rf_provenance,
        "risk_free_mean_annual": sr.rf_mean,
    }
    (out / "summary.json").write_text(json.dumps(_jsonable(summary), indent=2, default=str) + "\n")

    curves = pd.DataFrame({label: r.equity_curve for label, r in sr.results.items()})
    curves.to_csv(out / "equity_curves.csv", index_label="date", float_format="%.2f")
    for label, r in sr.results.items():
        slug = "".join(ch if ch.isalnum() else "_" for ch in label.lower()).strip("_")
        r.fills.to_csv(out / f"fills_{slug}.csv", index=False, float_format="%.6f")
        r.trades.to_csv(out / f"trades_{slug}.csv", index=False, float_format="%.6f")
    if plots:
        _plot(sr, out)
    return out
