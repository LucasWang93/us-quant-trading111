"""Regression tests for the Phase 2 audit fixes (reproducible data checksums/provenance,
accurate reproducibility documentation, and result regression against committed artifacts)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from usquant.cli import main
from usquant.data import DataStore, SyntheticProvider, frame_checksum, read_provenance
from usquant.data import rates
from usquant.data.provenance import file_sha256
from usquant.reproduce import compare_summaries, reproduce
from usquant.study import run_study, study_config_from_dict, summary_dict, write_study

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).resolve().parent / "golden" / "synthetic_study_summary.json"
COMMITTED = ROOT / "docs" / "results" / "spy_qqq_study" / "summary.json"


class Remote(SyntheticProvider):
    """A 'remote' provider: full-precision floats, so it goes through the download/cache path."""

    name = "remote"


# ---------------------------------------------------------------- fix 1: checksums
def test_download_and_cache_hit_return_identical_data(tmp_path):
    """Audit finding: the first (downloading) run used full-precision in-memory data while
    later runs read the 6-decimal cache, so checksums and results could never be reproduced."""
    store = DataStore(tmp_path)
    first, p1 = store.load_with_provenance(Remote(seed=3), "SPY", "2010-01-01", "2012-12-31")
    second, p2 = store.load_with_provenance(Remote(seed=3), "SPY", "2010-01-01", "2012-12-31")
    assert any("served from cache" in n for n in p2.notes)
    pd.testing.assert_frame_equal(first, second)
    assert frame_checksum(first) == frame_checksum(second)
    # The returned data is the on-disk data, not the higher-precision download.
    raw = Remote(seed=3).fetch("SPY", "2010-01-01", "2012-12-31")
    assert frame_checksum(raw) != frame_checksum(first)


def test_provenance_records_cache_file_hash_and_detects_edits(tmp_path):
    store = DataStore(tmp_path)
    _, prov = store.load_with_provenance(Remote(seed=1), "QQQ", "2015-01-01", "2015-12-31")
    path = store.path(Remote(), "QQQ")
    assert prov.cache_file == str(path)
    assert prov.cache_sha256 == file_sha256(path)
    assert read_provenance(path).cache_sha256 == prov.cache_sha256

    text = path.read_text().splitlines()
    cols = text[5].split(",")
    cols[4] = f"{float(cols[4]) * 1.01:.6f}"  # tamper with one close
    text[5] = ",".join(cols)
    path.write_text("\n".join(text) + "\n")
    _, prov2 = store.load_with_provenance(Remote(seed=1), "QQQ", "2015-01-01", "2015-12-31")
    assert prov2.cache_sha256 != prov.cache_sha256
    assert any("modified after download" in n for n in prov2.notes)


def test_risk_free_download_and_cache_hit_are_identical(tmp_path, monkeypatch):
    idx = pd.bdate_range("2020-01-01", periods=60)
    noisy = pd.Series(1.0 + np.random.default_rng(0).random(60) / 3, index=idx)  # many decimals
    monkeypatch.setattr(rates, "_from_yahoo", lambda *a, **k: noisy)
    first, p1 = rates.load_rate_percent("^IRX", "2020-01-01", idx[-1], cache_dir=tmp_path)
    second, p2 = rates.load_rate_percent("^IRX", "2020-01-01", idx[-1], cache_dir=tmp_path)
    pd.testing.assert_series_equal(first, second, check_names=False)
    assert p1.cache_sha256 == p2.cache_sha256 == file_sha256(tmp_path / "yahoo" / "IRX.csv")


# ------------------------------------------------------- reproduce: pass and fail
def _csv_study(csv_dir: Path) -> dict:
    return {
        "name": "csv_repro",
        "data": {"provider": "csv", "provider_options": {"directory": str(csv_dir)}, "symbols": ["SPY", "QQQ"],
                 "benchmark": "SPY", "start": "2005-01-03", "evaluation_start": "2006-02-01", "end": "2010-12-31"},
        "strategies": [{"label": "MA", "name": "ma_crossover", "params": {"fast": 20, "slow": 100}}],
        "benchmarks": [{"label": "SPY B&H", "weights": {"SPY": 1.0}}],
        "cost_scenarios": [{"label": "free", "commission": "zero", "slippage_bps": 0}],
        "risk_free_symbol": None,
    }


def test_reproduce_exact_then_detects_changed_input(tmp_path, capsys):
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    for sym, df in SyntheticProvider(seed=9).fetch_many(["SPY", "QQQ"], "2005-01-03", "2010-12-31").items():
        df.to_csv(csv_dir / f"{sym}.csv", index_label="date", float_format="%.6f")
    out = write_study(run_study(study_config_from_dict(_csv_study(csv_dir))), run_dir=tmp_path / "run", plots=False)
    summary = out / "summary.json"

    rep, _ = reproduce(summary)
    assert rep.inputs_match and rep.numbers_match and rep.max_abs_diff == 0.0
    assert main(["reproduce", "--summary", str(summary)]) == 0
    assert "exact reproduction" in capsys.readouterr().out

    # Change one price: inputs no longer match, and the numbers move.
    p = csv_dir / "SPY.csv"
    df = pd.read_csv(p)
    df.loc[400, ["open", "high", "low", "close", "adj_close"]] *= 1.05
    df.to_csv(p, index=False, float_format="%.6f")
    rep2, _ = reproduce(summary)
    assert not rep2.inputs_match and any("SPY" in d for d in rep2.input_diffs)
    assert not rep2.numbers_match
    assert main(["reproduce", "--summary", str(summary)]) == 1


def test_compare_summaries_tolerance_and_integer_rules():
    base = {"data_checksums": {"SPY": "a"}, "metrics": {"S": {"cagr": 0.1, "fills": 10}}}
    near = {"data_checksums": {"SPY": "b"}, "metrics": {"S": {"cagr": 0.1 + 3e-6, "fills": 10}}}
    r = compare_summaries(base, near, rtol=1e-4, atol=1e-4)
    assert not r.inputs_match and r.numbers_match
    assert not compare_summaries(base, near).numbers_match  # exact tolerance rejects 3e-6
    off_by_one = {"data_checksums": {"SPY": "a"}, "metrics": {"S": {"cagr": 0.1, "fills": 11}}}
    assert compare_summaries(base, off_by_one, rtol=1, atol=1).integer_mismatches


# ------------------------------------------------- fix 2: documentation accuracy
def test_report_documents_input_hashes_and_reproduction_limits(tmp_path):
    from test_real_data_support import STUDY

    out = write_study(run_study(study_config_from_dict(STUDY)), run_dir=tmp_path / "r", plots=False)
    report = (out / "report.md").read_text()
    assert "input file SHA-256" in report
    assert "usquant reproduce --summary" in report
    assert "does **not**" in report  # fresh downloads are explicitly not bit-reproducible
    data_md = (ROOT / "docs" / "data.md").read_text()
    assert "Use the cached files to reproduce an exact run" not in data_md  # the inaccurate claim from the audit
    assert "usquant reproduce" in data_md


def test_committed_report_matches_committed_summary_hashes():
    summary = json.loads(COMMITTED.read_text())
    report = (COMMITTED.parent / "report.md").read_text()
    for sym, prov in summary["data_provenance"].items():
        assert prov["cache_sha256"], f"{sym}: committed run has no input file hash"
        assert prov["cache_sha256"][:16] in report
        assert summary["data_checksums"][sym] in report


# -------------------------------------------- fix 3: result regression tests
STUDY_FOR_GOLDEN = {
    "name": "golden_synthetic",
    "data": {"provider": "synthetic", "provider_options": {"seed": 42}, "symbols": ["SPY", "QQQ"], "benchmark": "SPY",
             "start": "2003-01-01", "evaluation_start": "2004-01-02", "end": "2014-12-31"},
    "strategies": [
        {"label": "MA 50/200", "name": "ma_crossover", "params": {"fast": 50, "slow": 200}},
        {"label": "Momentum 12-1", "name": "momentum", "params": {"lookback": 252, "skip": 21, "top_n": 1, "rebalance": "M"}},
    ],
    "benchmarks": [
        {"label": "SPY B&H", "weights": {"SPY": 1.0}},
        {"label": "50/50", "weights": {"SPY": 0.5, "QQQ": 0.5}, "rebalance_threshold": 0.05},
    ],
    "periods": [{"label": "first", "start": "2004-01-02", "end": "2008-12-31"}],
    "cost_scenarios": [{"label": "25 bps", "slippage_bps": 25}, {"label": "close", "execution_price": "close"}],
    "risk_free_symbol": None,
}


def test_engine_results_match_golden_snapshot():
    """Runs in CI without network: any change to engine, costs, strategies or metrics that
    moves results must update tests/golden/ deliberately."""
    golden = json.loads(GOLDEN.read_text())
    rerun = json.loads(json.dumps(summary_dict(run_study(study_config_from_dict(STUDY_FOR_GOLDEN))), default=str))
    rep = compare_summaries(golden, rerun, rtol=1e-9, atol=1e-12)
    assert rep.inputs_match, rep.format()
    assert rep.numbers_match, rep.format()
    assert rep.compared_values > 200


def _local_inputs_match_committed() -> bool:
    if not COMMITTED.exists():
        return False
    summary = json.loads(COMMITTED.read_text())
    for prov in [*summary["data_provenance"].values(), summary.get("risk_free_provenance") or {}]:
        path, digest = prov.get("cache_file"), prov.get("cache_sha256")
        if not path or not digest or not (ROOT / path).exists() or file_sha256(ROOT / path) != digest:
            return False
    return True


@pytest.mark.skipif(
    not _local_inputs_match_committed(),
    reason="raw Yahoo files with the committed SHA-256 are not present (they are not redistributable); "
    "run `usquant study ...` once to create them, or copy the original cache",
)
def test_committed_spy_qqq_study_reproduces_exactly(monkeypatch):
    monkeypatch.chdir(ROOT)
    rep, _ = reproduce(COMMITTED)
    assert rep.inputs_match, rep.format()
    assert rep.numbers_match, rep.format()
    assert rep.max_abs_diff <= 1e-12, rep.format()
