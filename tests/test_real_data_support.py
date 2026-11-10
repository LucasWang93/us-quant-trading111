"""Tests for provenance, CSV import / fallback, data-quality checks, evaluation
windows, engine-run benchmarks and the study report. No network access."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from conftest import make_frame
from usquant.backtest import BacktestConfig, CommissionModel, SlippageModel, static_portfolio
from usquant.cli import main
from usquant.data import (
    CSVProvider,
    DataProvider,
    DataStore,
    FallbackProvider,
    MarketData,
    SyntheticProvider,
    check_quality,
    dividend_consistency,
    make_provider,
    read_provenance,
)
from usquant.data.csv_import import import_csv
from usquant.data.rates import daily_rate
from usquant.metrics import excess_sharpe_ratio, sharpe_ratio
from usquant.pipeline import evaluation_window, load_data, run_experiment
from usquant.study import run_study, study_config_from_dict, write_study


def _with_dividends(n: int = 300, div_every: int = 63, div: float = 0.5) -> pd.DataFrame:
    """Flat raw price of 100 with quarterly dividends and a correct back-adjusted close."""
    close = np.full(n, 100.0)
    divs = np.zeros(n)
    divs[div_every::div_every] = div
    df = make_frame(close, start="2020-01-01")
    df["dividends"] = divs
    # Correct back adjustment: before each ex-date multiply by (1 - div / prev_close).
    factor = np.ones(n)
    for i in np.flatnonzero(divs)[::-1]:
        factor[:i] *= 1 - divs[i] / close[i - 1]
    df["adj_close"] = close * factor
    return df


# ------------------------------------------------------------------ quality checks
def test_dividend_consistency_accepts_correct_adjustment_and_flags_missing_dividends():
    good = _with_dividends()
    chk = dividend_consistency(good)
    assert chk["dividend_events"] == 4
    assert chk["cagr_price_only"] == pytest.approx(0.0, abs=1e-12)
    assert abs(chk["annualized_gap_vendor_minus_reconstructed"]) < 1e-4
    assert not any("dividends" in w for w in check_quality(good, "X").warnings)

    stale = good.copy()
    stale["adj_close"] = stale["close"]  # vendor "forgot" the dividends
    rep = check_quality(stale, "X")
    assert rep.dividend_check["annualized_gap_vendor_minus_reconstructed"] < -0.005
    assert any("dividends reconstruction" in w for w in rep.warnings)


def test_quality_flags_gaps_extreme_moves_and_stale_bars():
    df = make_frame([100, 101, 102, 130, 130, 131, 132])
    df.iloc[4] = df.iloc[3]  # stale copy of the previous bar
    df = df.drop(df.index[5])
    df.index = df.index.where(df.index < df.index[5], df.index + pd.Timedelta(days=14))
    rep = check_quality(df, "X")
    assert len(rep.long_gaps) == 1
    assert rep.extreme_moves and rep.extreme_moves[0][1] == pytest.approx(130 / 102 - 1)
    assert rep.stale_bars == 1
    assert rep.dividend_check is None  # no dividend column supplied


# ------------------------------------------------------------- CSV import/provider
def test_import_csv_vendor_format_records_provenance(tmp_path):
    raw = make_frame([10.0, 10.5, 10.25, 10.75], start="2024-03-01")
    vendor = tmp_path / "spy_export.csv"
    raw.rename(columns=str.title).rename(columns={"Adj_Close": "Adj Close"}).to_csv(vendor, index_label="Date")
    dest = tmp_path / "csv"
    path, prov, quality = import_csv(vendor, "SPY", dest, source="Unit-test vendor export")
    assert path == dest / "SPY.csv"
    assert prov.vendor == "Unit-test vendor export"
    assert prov.price_basis == "ohlc_with_total_return_adj_close"
    assert prov.rows == 4 and prov.first_date == "2024-03-01"
    assert len(prov.original_sha256) == 64
    assert quality.rows == 4

    provider = CSVProvider(dest)
    df = provider.fetch("SPY")
    np.testing.assert_allclose(df["close"], raw["close"])
    p2 = provider.provenance("SPY", df)
    assert "Unit-test vendor export" in p2.vendor
    assert p2.price_basis == "ohlc_with_total_return_adj_close"


def test_csv_price_basis_adjusted_and_raw(tmp_path):
    df = make_frame([10.0, 11.0, 12.0]).drop(columns=["adj_close", "volume"])
    df.to_csv(tmp_path / "qqq.csv", index_label="date")  # lower-case file name, no volume
    adjusted = CSVProvider(tmp_path, price_basis="adjusted")
    out = adjusted.fetch("QQQ")
    np.testing.assert_allclose(out["adj_close"], out["close"])
    assert (out["volume"] == 0).all()
    assert adjusted.provenance("QQQ", out).price_basis == "fully_adjusted_ohlc"

    raw = CSVProvider(tmp_path)  # auto -> raw because there is no adjusted column
    out = raw.fetch("QQQ")
    prov = raw.provenance("QQQ", out)
    assert prov.price_basis == "unadjusted_ohlc_no_dividends"
    assert any("dividends not included" in n for n in prov.notes)

    with pytest.raises(ValueError):
        CSVProvider(tmp_path, price_basis="raw_with_adj_close").fetch("QQQ")


def test_cli_import_csv(tmp_path, capsys):
    vendor = tmp_path / "in.csv"
    make_frame([1.0, 2.0, 3.0]).to_csv(vendor, index_label="Date")
    rc = main(["import-csv", "--file", str(vendor), "--symbol", "SPY", "--dest", str(tmp_path / "out"),
               "--source", "test"])
    assert rc == 0
    assert "3 bars" in capsys.readouterr().out
    assert read_provenance(tmp_path / "out" / "SPY.csv").vendor == "test"


# --------------------------------------------------------------------- fallback
class FailingProvider(DataProvider):
    name = "failing"

    def fetch(self, symbol, start=None, end=None):
        raise ConnectionError("network unreachable")


def test_fallback_uses_csv_when_primary_fails(tmp_path):
    make_frame([10.0, 11.0, 12.0]).to_csv(tmp_path / "SPY.csv", index_label="date")
    fb = FallbackProvider([{"provider": "csv", "directory": str(tmp_path)}])
    fb.sources.insert(0, FailingProvider())

    df = fb.fetch("SPY")
    prov = fb.provenance("SPY", df)
    assert prov.source == "csv"
    assert any("network unreachable" in n for n in prov.notes)

    df2, prov2 = DataStore(tmp_path / "cache").load_with_provenance(fb, "SPY")
    assert prov2.source == "csv"
    assert any("fallback" in n for n in prov2.notes)
    assert not (tmp_path / "cache" / "csv").exists()  # local CSVs are not copied into the cache

    with pytest.raises(RuntimeError, match="all data sources failed"):
        fb.fetch("QQQ")


def test_auto_provider_default_sources():
    fb = make_provider("auto")
    assert [p.name for p in fb.sources] == ["yahoo", "csv"]


def test_datastore_writes_provenance_sidecar(tmp_path):
    class Remote(SyntheticProvider):
        name = "remote"

    store = DataStore(tmp_path)
    _, prov = store.load_with_provenance(Remote(seed=1), "SPY", "2020-01-01", "2020-06-30")
    side = read_provenance(store.path(Remote(), "SPY"))
    assert side is not None and side.rows == prov.rows
    _, cached = store.load_with_provenance(Remote(seed=1), "SPY", "2020-01-01", "2020-06-30")
    assert any("served from cache" in n for n in cached.notes)


# ---------------------------------------------------------- evaluation window etc.
def test_evaluation_window_has_no_trades_before_start():
    md = MarketData.from_frames(SyntheticProvider(seed=5).fetch_many(["SPY", "QQQ"], "2010-01-01", "2014-12-31"))
    w = pd.DataFrame(0.5, index=md.dates, columns=["SPY", "QQQ"])
    data, weights = evaluation_window(md, w, "2012-01-03")
    assert data.dates[0] == pd.Timestamp("2012-01-03")
    assert weights.index.equals(data.dates)


def test_run_experiment_evaluation_start_and_metadata(tmp_path):
    from usquant.config import load_config
    from pathlib import Path

    cfg = load_config(Path(__file__).resolve().parents[1] / "configs" / "ma_crossover_spy_qqq.yaml")
    cfg.data.provider, cfg.data.provider_options = "synthetic", {}
    cfg.data.start, cfg.data.evaluation_start = "2005-01-01", "2006-01-02"
    out = run_experiment(cfg, write=True, out_dir=tmp_path)
    assert out.result.equity.index[0] == pd.Timestamp("2006-01-02")
    assert out.result.fills["date"].min() > pd.Timestamp("2006-01-02")
    meta = json.loads((out.run_dir / "metadata.json").read_text())
    assert meta["data_provenance"]["SPY"]["source"] == "synthetic"
    assert "dividend_check" in meta["data_quality"]["QQQ"]


def test_static_portfolio_buy_and_hold_pays_costs_once():
    md = MarketData.from_frames(SyntheticProvider(seed=2).fetch_many(["SPY", "QQQ"], "2015-01-01", "2016-12-31"))
    cfg = BacktestConfig(commission=CommissionModel(), slippage=SlippageModel(bps=5))
    res = static_portfolio(md, {"SPY": 1.0}, cfg)
    assert len(res.fills) == 1 and res.fills["date"].iloc[0] == md.dates[1]
    assert res.total_commission > 0 and res.total_slippage > 0
    mixed = static_portfolio(md, {"SPY": 0.5, "QQQ": 0.5}, cfg, rebalance_threshold=0.01)
    assert len(mixed.fills) > 2  # drift re-balances happen


def test_excess_sharpe_and_daily_rate():
    idx = pd.bdate_range("2020-01-01", periods=500)
    rets = pd.Series(np.random.default_rng(0).normal(0.0005, 0.01, 500), index=idx)
    zero = pd.Series(0.0, index=idx)
    assert excess_sharpe_ratio(rets, zero) == pytest.approx(sharpe_ratio(rets, 0.0))
    y = pd.Series([5.0, 5.0], index=[idx[0], idx[300]])  # sparse quotes, forward-filled only
    rf = daily_rate(y, idx)
    assert rf.iloc[100] == pytest.approx(1.05 ** (1 / 252) - 1)
    assert excess_sharpe_ratio(rets, rf) < sharpe_ratio(rets, 0.0)


# ------------------------------------------------------------------------ study
STUDY = {
    "name": "unit_study",
    "data": {"provider": "synthetic", "symbols": ["SPY", "QQQ"], "benchmark": "SPY",
             "start": "2005-01-03", "evaluation_start": "2006-02-01", "end": "2012-12-31"},
    "strategies": [
        {"label": "MA", "name": "ma_crossover", "params": {"fast": 20, "slow": 100}},
        {"label": "Mom", "name": "momentum", "params": {"lookback": 200, "skip": 20, "top_n": 1}},
    ],
    "benchmarks": [
        {"label": "SPY B&H", "weights": {"SPY": 1.0}},
        {"label": "QQQ B&H", "weights": {"QQQ": 1.0}},
    ],
    "periods": [{"label": "first half", "start": "2006-02-01", "end": "2009-06-30"}],
    "cost_scenarios": [{"label": "free", "commission": "zero", "slippage_bps": 0}, {"label": "default"}],
    "risk_free_symbol": None,
}


def test_study_runs_and_writes_report(tmp_path):
    cfg = study_config_from_dict(STUDY)
    sr = run_study(cfg)
    assert set(sr.metrics) == {"MA", "Mom", "SPY B&H", "QQQ B&H"}
    for m in sr.metrics.values():
        assert m["start"] == "2006-02-01"
        for key in ("cagr", "sharpe_rf0", "max_drawdown", "round_trips", "fills"):
            assert key in m
    assert sr.metrics["SPY B&H"]["fills"] == 1
    # Zero costs can only help.
    assert sr.costs["free"]["MA"]["cagr"] >= sr.costs["default"]["MA"]["cagr"]
    assert sr.costs["default"]["MA"]["cagr"] == pytest.approx(sr.metrics["MA"]["cagr"])

    out = write_study(sr, run_dir=tmp_path / "run", plots=True)
    report = (out / "report.md").read_text()
    for heading in ("## Headline results", "## Data provenance", "### Data-quality checks", "## Adjusted prices",
                    "## Transaction costs", "## Execution model and limitations", "## Sub-periods"):
        assert heading in report
    assert "SyntheticProvider" in report
    summary = json.loads((out / "summary.json").read_text())
    assert summary["data_checksums"].keys() == {"SPY", "QQQ"}
    assert (out / "equity.png").exists() and (out / "drawdown.png").exists()


def test_study_is_deterministic():
    a = run_study(study_config_from_dict(STUDY))
    b = run_study(study_config_from_dict(STUDY))
    assert a.metrics == b.metrics


def test_study_rejects_unknown_keys():
    bad = {**STUDY, "strategies": [{**STUDY["strategies"][0], "parms": {}}]}
    with pytest.raises(ValueError, match="unknown keys"):
        study_config_from_dict(bad)


def test_shipped_study_config_parses():
    from pathlib import Path

    from usquant.study import load_study_config

    cfg = load_study_config(Path(__file__).resolve().parents[1] / "configs" / "study_spy_qqq.yaml")
    assert cfg.data.provider == "auto"
    assert cfg.data.evaluation_start == "2000-04-03"
    assert [b.label for b in cfg.benchmarks][:2] == ["SPY buy & hold", "QQQ buy & hold"]
    assert load_data  # imported for API stability
