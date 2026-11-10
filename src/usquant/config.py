"""Experiment configuration loaded from YAML.

Every backtest is fully described by one YAML file (see ``configs/``), which is
copied into the run folder for reproducibility. Secrets never belong in these
files; providers that need keys must read them from environment variables.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from usquant.backtest.costs import CommissionModel, SlippageModel
from usquant.backtest.engine import BacktestConfig
from usquant.risk.limits import RiskLimits


@dataclass
class DataConfig:
    symbols: list[str]
    provider: str = "yahoo"
    benchmark: str | None = "SPY"
    start: str | None = None
    end: str | None = None
    cache_dir: str = "data/cache"
    refresh: bool = False
    adjust: bool = True
    provider_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyConfig:
    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class PortfolioConfig:
    sizing: str = "equal_active"


@dataclass
class OutputConfig:
    dir: str = "results"
    plots: bool = True


@dataclass
class ExperimentConfig:
    name: str
    data: DataConfig
    strategy: StrategyConfig
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    risk: RiskLimits = field(default_factory=RiskLimits)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk_free_rate: float = 0.0
    output: OutputConfig = field(default_factory=OutputConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _datestr(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _check_keys(section: str, given: dict, allowed: set[str]) -> None:
    unknown = set(given) - allowed
    if unknown:
        raise ValueError(f"config section {section!r}: unknown keys {sorted(unknown)}")


def config_from_dict(raw: dict[str, Any]) -> ExperimentConfig:
    raw = dict(raw)
    _check_keys(
        "root", raw,
        {"name", "data", "strategy", "portfolio", "risk", "backtest", "risk_free_rate", "output"},
    )
    d = dict(raw["data"])
    _check_keys("data", d, set(DataConfig.__dataclass_fields__))
    d["start"], d["end"] = _datestr(d.get("start")), _datestr(d.get("end"))
    data = DataConfig(**d)

    s = dict(raw["strategy"])
    _check_keys("strategy", s, {"name", "params"})
    strategy = StrategyConfig(name=s["name"], params=dict(s.get("params") or {}))

    p = dict(raw.get("portfolio") or {})
    _check_keys("portfolio", p, set(PortfolioConfig.__dataclass_fields__))
    r = dict(raw.get("risk") or {})
    _check_keys("risk", r, set(RiskLimits.__dataclass_fields__))

    b = dict(raw.get("backtest") or {})
    _check_keys("backtest", b, set(BacktestConfig.__dataclass_fields__))
    if "commission" in b:
        b["commission"] = CommissionModel(**(b["commission"] or {}))
    if "slippage" in b:
        sl = b["slippage"]
        b["slippage"] = SlippageModel(**sl) if isinstance(sl, dict) else SlippageModel(bps=float(sl))

    o = dict(raw.get("output") or {})
    _check_keys("output", o, set(OutputConfig.__dataclass_fields__))

    return ExperimentConfig(
        name=raw["name"],
        data=data,
        strategy=strategy,
        portfolio=PortfolioConfig(**p),
        risk=RiskLimits(**r),
        backtest=BacktestConfig(**b),
        risk_free_rate=float(raw.get("risk_free_rate", 0.0)),
        output=OutputConfig(**o),
    )


def load_config(path: str | Path) -> ExperimentConfig:
    with open(path) as fh:
        return config_from_dict(yaml.safe_load(fh))
