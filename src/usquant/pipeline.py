"""End-to-end experiment runner: config -> data -> signals -> weights -> backtest -> report."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from usquant.backtest.engine import Backtester
from usquant.backtest.result import BacktestResult
from usquant.config import DataConfig, ExperimentConfig
from usquant.data.panel import MarketData
from usquant.data.providers import make_provider
from usquant.data.quality import check_quality
from usquant.data.store import DataStore, frame_checksum
from usquant.metrics.performance import summarize
from usquant.portfolio.sizing import signals_to_weights
from usquant.reporting.report import write_run
from usquant.risk.limits import apply_weight_limits
from usquant.strategies import make_strategy

log = logging.getLogger(__name__)


@dataclass
class LoadedData:
    """Aligned market data plus an audit trail for every input series."""

    market: MarketData
    frames: dict[str, pd.DataFrame]
    checksums: dict[str, str]
    provenance: dict[str, dict] = field(default_factory=dict)
    quality: dict[str, dict] = field(default_factory=dict)


@dataclass
class ExperimentOutput:
    result: BacktestResult
    metrics: dict
    run_dir: Path | None
    data_checksums: dict[str, str]
    provenance: dict[str, dict] = field(default_factory=dict)
    quality: dict[str, dict] = field(default_factory=dict)


def load_data(data_cfg: DataConfig, extra_symbols: list[str] | None = None) -> LoadedData:
    """Load, validate, quality-check and align all symbols an experiment needs."""
    provider = make_provider(data_cfg.provider, **data_cfg.provider_options)
    wanted = data_cfg.symbols + ([data_cfg.benchmark] if data_cfg.benchmark else []) + list(extra_symbols or [])
    symbols = list(dict.fromkeys(wanted))
    store = DataStore(data_cfg.cache_dir)
    frames, provenance = {}, {}
    for sym in symbols:
        if data_cfg.provider == "synthetic":  # nothing worth caching
            df = provider.fetch(sym, data_cfg.start, data_cfg.end)
            prov = provider.provenance(sym, df, data_cfg.start, data_cfg.end)
        else:
            df, prov = store.load_with_provenance(provider, sym, data_cfg.start, data_cfg.end, refresh=data_cfg.refresh)
        frames[sym], provenance[sym] = df, prov.to_dict()
    sources = {p["source"] for p in provenance.values()}
    if len(sources) > 1:
        log.warning("symbols come from different sources %s; check provenance before comparing them", sources)
    quality = {s: check_quality(df, s).to_dict() for s, df in frames.items()}
    for s, q in quality.items():
        for w in q["warnings"]:
            log.info("data quality %s: %s", s, w)
    checksums = {s: frame_checksum(df) for s, df in frames.items()}
    market = MarketData.from_frames(frames, adjust=data_cfg.adjust)
    return LoadedData(market=market, frames=frames, checksums=checksums, provenance=provenance, quality=quality)


def load_market_data(cfg: ExperimentConfig) -> tuple[MarketData, dict[str, str]]:
    """Backward-compatible wrapper around :func:`load_data`."""
    loaded = load_data(cfg.data)
    return loaded.market, loaded.checksums


def target_weights(cfg: ExperimentConfig, data: MarketData) -> pd.DataFrame:
    """Signals -> sized weights -> risk-limited weights, on the full (warm-up included) history."""
    strategy = make_strategy(cfg.strategy.name, **cfg.strategy.params)
    signals = strategy.generate_signals(data.select(cfg.data.symbols))
    return apply_weight_limits(signals_to_weights(signals, cfg.portfolio.sizing), cfg.risk)


def evaluation_window(data: MarketData, weights: pd.DataFrame, start: str | None) -> tuple[MarketData, pd.DataFrame]:
    """Restrict the backtest to ``[start, end]`` after indicators were computed on all data.

    Bars before ``start`` act as indicator warm-up only: no trades, no P&L. The
    first order is decided at the close of the first evaluation bar and filled on
    the next bar, exactly like the benchmark entry.
    """
    if start is None:
        return data, weights
    sliced = data.between(start, None)
    if len(sliced.dates) < 2:
        raise ValueError(f"evaluation_start {start} leaves fewer than two bars")
    return sliced, weights.loc[sliced.dates]


def run_experiment(
    cfg: ExperimentConfig,
    write: bool = True,
    out_dir: str | Path | None = None,
    loaded: LoadedData | None = None,
) -> ExperimentOutput:
    loaded = loaded or load_data(cfg.data)
    weights = target_weights(cfg, loaded.market)
    data, weights = evaluation_window(loaded.market, weights, cfg.data.evaluation_start)

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
            data_checksums=loaded.checksums,
            plots=cfg.output.plots,
            run_name=cfg.name,
            extra_metadata={"data_provenance": loaded.provenance, "data_quality": loaded.quality},
        )
        log.info("results written to %s", run_dir)
    return ExperimentOutput(
        result=result,
        metrics=metrics,
        run_dir=run_dir,
        data_checksums=loaded.checksums,
        provenance=loaded.provenance,
        quality=loaded.quality,
    )
