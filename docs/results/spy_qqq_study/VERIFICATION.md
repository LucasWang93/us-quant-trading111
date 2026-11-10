# How this run was produced and checked

## Provenance of these files

* Generated 2026-11-10T09:17:19Z by `usquant -v study --config configs/study_spy_qqq.yaml` at commit
  `9ba20b9` (clean tree). None of the files were edited by hand. Exit code 0; 0 downloads, every series
  was served from the cache.
* Input data: Yahoo Finance via yfinance 1.8.0, retrieved 2026-11-10T08:31:23Z (SPY), 08:31:24Z (QQQ) and
  08:31:24Z (^IRX). The window is 1999-03-10 to 2026-10-30, with 6,955 rows each (^IRX: 6,947).
* Input files (not committed, because Yahoo's terms restrict redistribution), identified by SHA-256:

  | file | SHA-256 |
  |---|---|
  | `data/cache/yahoo/SPY.csv` | `bf74ffe799578f81…` (full hash in `summary.json` → `data_provenance.SPY.cache_sha256`) |
  | `data/cache/yahoo/QQQ.csv` | `a00f630db3fd8f33…` |
  | `data/cache/yahoo/IRX.csv` | `0aeade617417a3c7…` |

## History

An earlier version of these results existed only locally. It was never committed, because the
`results/` rule in `.gitignore` also matched `docs/results/`. That version computed its data checksums
(`b512477ade580a83` for SPY, `000c494db0db20fe` for QQQ) from full-precision downloads rather than from the
6-decimal cache files. Those checksums could not be reproduced by anyone. The Phase 2 audit fixed this
(see `docs/audits/phase2_audit.md`). This version was regenerated from the same downloaded data, and:

* all 705 compared values agree with the earlier version within 1e-6 relative;
* no integer differs (round trips, fills, drawdown durations);
* the largest absolute difference is USD 0.01 on a USD 1.25M final equity;
* the results tables in `report.md` are textually identical.

## Checks

1. **Exact reproduction**: `usquant reproduce --summary docs/results/spy_qqq_study/summary.json` (exit 0).
   With the input files above, the inputs are identical, 705 values are compared and the maximum
   difference is 0.0. `tests/test_reproducibility.py::test_committed_spy_qqq_study_reproduces_exactly`
   runs the same check whenever those files are present.
2. **Re-downloaded data**: `usquant reproduce ... --refresh --cache-dir <empty dir>` (exit 0). The inputs
   differ, as expected: Yahoo's adjusted closes vary by about 1e-6 between downloads. All 705 values
   still agree within 1e-4, with 0 integer mismatches. The largest absolute difference is USD 4.04 on a
   final equity of about USD 1.25M.
3. **Benchmarks recomputed without the engine.** Buy-and-hold CAGR computed directly from the adjusted
   series (entry at the adjusted open of 2000-04-04, exit at the 2026-10-30 adjusted close): SPY 8.13%,
   QQQ 8.11%. Both match the engine-run benchmarks.
4. **Strategy recomputed with vectorised pandas.** MA 50/200 with equal weight across active symbols and
   execution at the next open, rebalanced continuously and frictionless: 10.07% CAGR. The engine's
   frictionless scenario gives 10.05%. The difference comes from integer shares and the 5-point drift band.
5. **Look-ahead tests on these real data.** I truncated the history at 25 random dates and recomputed the
   signals: 0 mismatches. I multiplied all prices after 2015-06-01 by 3: equity up to that date was
   unchanged. Every fill's reference price equals that day's adjusted open. Every fill follows either a
   target change on the previous close or a drift of more than 5 percentage points.
6. **Costs and accounting.** Every fill matches the commission formula and the 2 bps slippage. Final cash
   equals the starting capital minus fill values and commissions. Cash never went negative (minimum
   USD 0.36).
7. **Dividends.** Total return rebuilt from the unadjusted close plus Yahoo's dividend events agrees with
   Yahoo's adjusted close to within 0.003%/yr (SPY) and 0.0000%/yr (QQQ).
8. **Large daily move.** The one move over 15% (QQQ +16.8% on 2001-01-03) is real: that day the Fed made
   a surprise rate cut.

Checks 3 to 8 were run during the audit with ad-hoc scripts, not as part of the test suite. Their
results are recorded here and in `docs/audits/phase2_audit.md`.
