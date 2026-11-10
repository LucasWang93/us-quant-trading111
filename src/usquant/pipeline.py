"""End-to-end experiment runner: config -> data -> signals -> weights -> backtest -> report."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from usquant.backtest.engine import Backtester
from usquant.backtest.result import BacktestResult
from usquant.config import ExperimentConfig
from usquant.data.panel import MarketData
from usquant.data.providers import make_provider
from usquant.data.store import DataStore, frame_checksum
from usquant.metrics.performance import summarize
from usquant.portfolio.sizing import signals_to_weights
from usquant.reporting.report import write_run
from usquant.risk.limits import apply_weight_limits
from usquant.strategies import make_strategy

log = logging.getLogger(__name__)


@dataclass
class ExperimentOutput:
    result: BacktestResult
    metrics: dict
    run_dir: Path | None
    data_checksums: dict[str, str]


def load_market_data(cfg: ExperimentConfig) -> tuple[MarketData, dict[str, str]]:
    provider = make_provider(cfg.data.provider, **cfg.data.provider_options)
    symbols = list(dict.fromkeys(cfg.data.symbols + ([cfg.data.benchmark] if cfg.data.benchmark else [])))
    if cfg.data.provider == "synthetic":
        frames = provider.fetch_many(symbols, cfg.data.start, cfg.data.end)  # nothing to cache
    else:
        store = DataStore(cfg.data.cache_dir)
        frames = store.load_many(provider, symbols, cfg.data.start, cfg.data.end, refresh=cfg.data.refresh)
    checksums = {s: frame_checksum(df) for s, df in frames.items()}
    return MarketData.from_frames(frames, adjust=cfg.data.adjust), checksums


def run_experiment(cfg: ExperimentConfig, write: bool = True, out_dir: str | Path | None = None) -> ExperimentOutput:
    data, checksums = load_market_data(cfg)
    universe = data.select(cfg.data.symbols)

    strategy = make_strategy(cfg.strategy.name, **cfg.strategy.params)
    signals = strategy.generate_signals(universe)
    weights = apply_weight_limits(signals_to_weights(signals, cfg.portfolio.sizing), cfg.risk)

    engine = Backtester(cfg.backtest, cfg.risk)
    result = engine.run(data, weights, benchmark=cfg.data.benchmark, strategy_name=cfg.name)
    metrics = summarize(result, rf_annual=cfg.risk_free_rate)

    run_dir = None
    if write:
        run_dir = write_run(
            result,
            metrics,
            out_dir or cfg.output.dir,
            config_dict=cfg.to_dict(),
            data_checksums=checksums,
            plots=cfg.output.plots,
            run_name=cfg.name,
        )
        log.info("results written to %s", run_dir)
    return ExperimentOutput(result=result, metrics=metrics, run_dir=run_dir, data_checksums=checksums)
