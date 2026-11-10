"""Re-run a committed study and compare it with its ``summary.json``.

Two separate questions are answered:

1. **Same inputs?** Each symbol's data checksum (and, when recorded, the SHA-256 of
   the cache file that was read) is compared with the summary. Matching inputs are
   required for an *exact* reproduction.
2. **Same numbers?** Every metric in ``metrics``, ``periods`` and
   ``cost_scenarios`` is compared. Integers (fills, round trips, drawdown days) must
   match exactly; floats must agree within ``rtol``/``atol``.

Yahoo's adjusted closes differ by ~1e-6 between downloads made at different
times, so a run on re-downloaded data is expected to fail (1) and pass (2) only
with a loose tolerance (see ``DOWNLOAD_RTOL``). A run on the same cache files
must pass both with the default (tight) tolerance.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Tolerances for a run on the *same* input files (bit-identical expected; slack
#: covers summation-order differences between numpy/pandas versions).
EXACT_RTOL = 1e-9
EXACT_ATOL = 1e-12
#: Tolerances for a run on *re-downloaded* Yahoo data (measured drift ~3e-6).
DOWNLOAD_RTOL = 1e-4
DOWNLOAD_ATOL = 1e-4

COMPARED_SECTIONS = ("metrics", "periods", "cost_scenarios")


@dataclass
class ReproductionReport:
    inputs_match: bool
    input_diffs: list[str] = field(default_factory=list)
    numeric_mismatches: list[tuple[str, Any, Any]] = field(default_factory=list)
    integer_mismatches: list[tuple[str, Any, Any]] = field(default_factory=list)
    compared_values: int = 0
    max_abs_diff: float = 0.0
    max_abs_diff_path: str = ""

    @property
    def numbers_match(self) -> bool:
        return not self.numeric_mismatches and not self.integer_mismatches

    def format(self) -> str:
        lines = [
            f"inputs identical to committed run: {'yes' if self.inputs_match else 'NO'}",
            *[f"  {d}" for d in self.input_diffs],
            f"values compared: {self.compared_values}",
            f"max |difference|: {self.max_abs_diff:.3e} at {self.max_abs_diff_path or '-'}",
            f"integer mismatches: {len(self.integer_mismatches)}",
            f"float mismatches beyond tolerance: {len(self.numeric_mismatches)}",
        ]
        for path, a, b in (self.integer_mismatches + self.numeric_mismatches)[:10]:
            lines.append(f"  {path}: committed={a!r} rerun={b!r}")
        return "\n".join(lines)


def compare_inputs(committed: dict, rerun: dict) -> tuple[bool, list[str]]:
    diffs = []
    a, b = committed.get("data_checksums", {}), rerun.get("data_checksums", {})
    for sym in sorted(set(a) | set(b)):
        if a.get(sym) != b.get(sym):
            diffs.append(f"{sym}: data checksum committed={a.get(sym)} rerun={b.get(sym)}")
    pa, pb = committed.get("data_provenance", {}), rerun.get("data_provenance", {})
    for sym in sorted(set(pa) & set(pb)):
        ha, hb = pa[sym].get("cache_sha256"), pb[sym].get("cache_sha256")
        if ha and hb and ha != hb:
            diffs.append(f"{sym}: cache file SHA-256 committed={ha[:16]} rerun={hb[:16]}")
    ra, rb = committed.get("risk_free_provenance") or {}, rerun.get("risk_free_provenance") or {}
    if ra.get("cache_sha256") and rb.get("cache_sha256") and ra["cache_sha256"] != rb["cache_sha256"]:
        diffs.append(f"risk-free: cache file SHA-256 committed={ra['cache_sha256'][:16]} rerun={rb['cache_sha256'][:16]}")
    return not diffs, diffs


def compare_numbers(committed: dict, rerun: dict, rtol: float, atol: float, report: ReproductionReport) -> None:
    def walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b), key=str):
                if k not in a or k not in b:
                    report.integer_mismatches.append((f"{path}/{k}", a.get(k, "<missing>"), b.get(k, "<missing>")))
                else:
                    walk(a[k], b[k], f"{path}/{k}")
            return
        report.compared_values += 1
        if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, str) or a is None or b is None:
            if a != b:
                report.integer_mismatches.append((path, a, b))
            return
        if isinstance(a, int) and isinstance(b, int):
            if a != b:
                report.integer_mismatches.append((path, a, b))
            return
        fa, fb = float(a), float(b)
        diff = abs(fa - fb)
        if diff > report.max_abs_diff:
            report.max_abs_diff, report.max_abs_diff_path = diff, path
        if not math.isclose(fa, fb, rel_tol=rtol, abs_tol=atol):
            report.numeric_mismatches.append((path, a, b))

    for section in COMPARED_SECTIONS:
        walk(committed.get(section, {}), rerun.get(section, {}), section)


def compare_summaries(committed: dict, rerun: dict, rtol: float = EXACT_RTOL, atol: float = EXACT_ATOL) -> ReproductionReport:
    ok, diffs = compare_inputs(committed, rerun)
    rep = ReproductionReport(inputs_match=ok, input_diffs=diffs)
    compare_numbers(committed, rerun, rtol, atol, rep)
    return rep


def reproduce(
    summary_path: str | Path,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    rtol: float | None = None,
    atol: float | None = None,
) -> tuple[ReproductionReport, dict]:
    """Re-run the study stored in ``summary_path`` and compare. Returns ``(report, rerun_summary)``."""
    from usquant.study import run_study, study_config_from_dict, summary_dict

    committed = json.loads(Path(summary_path).read_text())
    cfg = study_config_from_dict(committed["config"])
    if cache_dir is not None:
        cfg.data.cache_dir = str(cache_dir)
    cfg.data.refresh = refresh
    rerun = json.loads(json.dumps(summary_dict(run_study(cfg)), default=str))
    if rtol is None:
        rtol = DOWNLOAD_RTOL if refresh else EXACT_RTOL
    if atol is None:
        atol = DOWNLOAD_ATOL if refresh else EXACT_ATOL
    return compare_summaries(committed, rerun, rtol, atol), rerun
