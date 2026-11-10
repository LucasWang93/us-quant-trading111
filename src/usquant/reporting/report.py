"""Write a self-contained, reproducible run folder for a backtest."""

from __future__ import annotations

import json
import math
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import usquant


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        ).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except Exception:  # not a git checkout / git missing
        return None


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) else ("inf" if math.isinf(f) else f)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


_PCT_KEYS = {
    "total_return", "cagr", "volatility", "max_drawdown", "daily_win_rate", "trade_win_rate",
    "alpha", "tracking_error", "excess_cagr", "avg_gross_exposure",
}


def format_metrics_table(metrics: dict) -> str:
    """Markdown table: strategy vs benchmark side by side."""
    strat = metrics["strategy"]
    bench = metrics.get("benchmark") or {}
    keys = [k for k in strat if k not in ("start", "end")]

    def fmt(k, v):
        if v is None or v == "":
            return ""
        if isinstance(v, str):
            return v
        if k in _PCT_KEYS:
            return f"{v * 100:.2f}%"
        if isinstance(v, int):
            return f"{v:,d}"
        if abs(v) >= 1000:
            return f"{v:,.2f}"
        return f"{v:.3f}"

    lines = [
        f"Period: {strat['start']} -> {strat['end']}",
        "",
        "| metric | strategy | benchmark |",
        "|---|---:|---:|",
    ]
    for k in keys:
        lines.append(f"| {k} | {fmt(k, strat[k])} | {fmt(k, bench.get(k, ''))} |")
    rel = metrics.get("relative")
    if rel:
        lines += ["", "| relative to benchmark | value |", "|---|---:|"]
        lines += [f"| {k} | {fmt(k, v)} |" for k, v in rel.items()]
    return "\n".join(lines)


def write_run(
    result,
    metrics: dict,
    out_root: str | Path,
    config_dict: dict | None = None,
    data_checksums: dict[str, str] | None = None,
    plots: bool = True,
    run_name: str | None = None,
) -> Path:
    """Persist everything needed to audit or reproduce a run; return its folder."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(out_root) / f"{run_name or result.strategy_name}_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    eq = result.equity.copy()
    if result.benchmark is not None:
        eq["benchmark"] = result.benchmark
    eq.to_csv(run_dir / "equity.csv", index_label="date", float_format="%.4f")
    result.weights.to_csv(run_dir / "weights.csv", index_label="date", float_format="%.6f")
    result.fills.to_csv(run_dir / "fills.csv", index=False, float_format="%.6f")
    result.trades.to_csv(run_dir / "trades.csv", index=False, float_format="%.6f")
    (run_dir / "metrics.json").write_text(json.dumps(_jsonable(metrics), indent=2))
    (run_dir / "metrics.md").write_text(format_metrics_table(metrics) + "\n")

    if config_dict is not None:
        import yaml

        (run_dir / "config.yaml").write_text(yaml.safe_dump(_jsonable(config_dict), sort_keys=False))

    meta = {
        "created_utc": stamp,
        "usquant_version": usquant.__version__,
        "git_commit": _git_commit(),
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "data_checksums": data_checksums or {},
    }
    (run_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

    if plots:
        from usquant.reporting.plots import save_all_plots

        save_all_plots(result, run_dir)
    return run_dir
