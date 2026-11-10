from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from usquant.broker import Broker, TradingMode
from usquant.cli import main
from usquant.config import config_from_dict, load_config
from usquant.pipeline import run_experiment

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"


@pytest.mark.parametrize(
    "path", sorted(p for p in CONFIG_DIR.glob("*.yaml") if not p.name.startswith("study_")), ids=lambda p: p.name
)
def test_example_configs_load(path):
    cfg = load_config(path)
    assert cfg.data.symbols == ["SPY", "QQQ"]
    assert cfg.backtest.execution_price == "open"


def test_unknown_config_keys_are_rejected():
    raw = yaml.safe_load((CONFIG_DIR / "ma_crossover_spy_qqq.yaml").read_text())
    raw["backtest"]["comission"] = {}  # typo
    with pytest.raises(ValueError, match="unknown keys"):
        config_from_dict(raw)


def test_run_experiment_is_reproducible(tmp_path):
    cfg = load_config(CONFIG_DIR / "momentum_spy_qqq.yaml")
    cfg.data.provider = "synthetic"
    cfg.data.provider_options = {"seed": 11}
    a = run_experiment(cfg, write=False)
    b = run_experiment(cfg, write=False)
    assert a.metrics == b.metrics
    assert a.data_checksums == b.data_checksums


def test_cli_backtest_writes_run_folder(tmp_path, capsys):
    rc = main(
        [
            "backtest",
            "--config", str(CONFIG_DIR / "ma_crossover_spy_qqq.yaml"),
            "--provider", "synthetic",
            "--output", str(tmp_path),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "sharpe" in out and "max_drawdown" in out
    (run_dir,) = list(tmp_path.iterdir())
    for name in ("equity.csv", "fills.csv", "trades.csv", "weights.csv", "metrics.json", "metrics.md",
                 "config.yaml", "metadata.json", "equity.png", "drawdown.png", "weights.png"):
        assert (run_dir / name).exists(), name
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert metrics["benchmark"] is not None
    meta = json.loads((run_dir / "metadata.json").read_text())
    assert set(meta["data_checksums"]) == {"SPY", "QQQ"}
    # The saved config round-trips into an identical experiment definition.
    assert load_config(run_dir / "config.yaml").strategy.params["fast"] == 50


def test_cli_download_with_synthetic_provider(tmp_path, capsys):
    rc = main(["download", "--provider", "synthetic", "--symbols", "SPY", "--start", "2020-01-01",
               "--end", "2020-12-31", "--cache-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "synthetic" / "SPY.csv").exists()


def test_broker_is_paper_only():
    assert [m.name for m in TradingMode] == ["PAPER"]

    class Dummy(Broker):
        def positions(self):
            return {}

        def cash(self):
            return 0.0

        def submit(self, order):
            return "x"

    assert Dummy().mode is TradingMode.PAPER
    with pytest.raises(PermissionError):
        Dummy(mode="live")
