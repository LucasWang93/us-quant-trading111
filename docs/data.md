# Market data: sources, provenance, CSV import and quality checks

## Sources

| Provider | Config name | What it returns | When to use |
|---|---|---|---|
| Yahoo Finance via `yfinance` | `yahoo` | Split-adjusted OHLC, split- and dividend-adjusted `Adj Close`, volume, cash dividends, splits | Default free source for US ETFs |
| Local CSV files | `csv` | Whatever the file contains, interpreted through `price_basis` | Offline runs, licensed vendor data, reproducing a past run |
| Yahoo, then CSV | `auto` | First source that succeeds, per symbol | Real runs that must survive a Yahoo outage |
| Random walk | `synthetic` | Deterministic fake data | Tests and CI only. Not market data |

### Why Yahoo, and what it is not

Yahoo Finance is the only free source of long, daily, dividend-adjusted SPY/QQQ
history that works without an API key. Its `Adj Close` uses the CRSP-style
multiplier method: before each ex-date, prices are multiplied by
`1 - dividend / previous close`, and by `1 / ratio` before each split. We check
this independently on every load (see *Quality checks*).

Limitations you should know:

* The endpoint is unofficial: no SLA, rate limits, occasional schema changes.
* `Close` is **split-adjusted, not the price that printed**. QQQ split 2:1 on
  2000-03-20, so 1999 QQQ closes in the file are half the traded price.
* `Adj Close` history is rewritten each time a new dividend is paid (each
  quarter, the whole history is rescaled by a tiny factor). Separately, repeated
  downloads of the same history differ by about 1e-6 in `Adj Close`
  (measured 2026-11-10: 5,710 of 6,955 SPY rows differed, by at most 0.000244 USD).
  A re-download therefore never reproduces a data checksum. See *Reproducibility*.
* Yahoo's terms restrict redistribution, so this repository does **not** commit
  raw price files. It commits checksums, provenance and derived results.

For decisions involving real money, re-run against a licensed source such as
CRSP or your broker's historical bars, imported through `usquant import-csv`.

## Provenance

Each downloaded or imported series has a sidecar `<SYMBOL>.provenance.json` next
to its CSV:

```json
{
  "symbol": "SPY",
  "source": "yahoo",
  "vendor": "Yahoo Finance (query1/query2.finance.yahoo.com) via yfinance 1.8.0",
  "price_basis": "split_adjusted_ohlc_with_total_return_adj_close",
  "retrieved_utc": "2026-11-10T08:24:18Z",
  "requested_start": "1999-03-10",
  "requested_end": "2026-10-30",
  "first_date": "1999-03-10",
  "last_date": "2026-10-30",
  "rows": 6955,
  "has_dividends": true,
  "notes": ["..."]
}
```

`usquant backtest` writes every symbol's provenance and quality report into the
run's `metadata.json`. `usquant study` also prints them in `report.md`, along with a
data checksum of each validated input frame and the SHA-256 of the file it came
from (`cache_sha256` for downloads, `original_sha256` for imported CSVs).

## Reproducibility

Downloaded data are written to the cache at 6 decimal places and then **re-read
from that file** before use. A run that downloads and a later run served from the
same cache file therefore use identical numbers. They also have identical data
checksums. The sidecar keeps the file's SHA-256, and a warning is added to the
provenance if the file changes afterwards.

To check a committed run:

```bash
usquant reproduce --summary docs/results/spy_qqq_study/summary.json
```

| Inputs | What you can expect | Exit code |
|---|---|---|
| Cache files whose SHA-256 matches the committed provenance | Exact: identical checksums, every integer identical, every float within 1e-9 relative | 0 |
| Cache files that differ, for example from another download | Reported as "input data differ". Numbers are still compared with a 1e-9 tolerance | 2, or 1 if numbers differ |
| `--refresh` (re-download now) | Inputs differ by construction. Numbers are compared with a 1e-4 tolerance (observed drift 3.3e-6) | 0 within tolerance, else 1 |

The raw Yahoo files are not committed, so anyone without the original cache
files can only do the third kind of check. For exact, shareable reproduction,
use licensed data imported with `usquant import-csv`. You can then keep or share
those files under your own licence.

`price_basis` values:

| Value | Meaning |
|---|---|
| `split_adjusted_ohlc_with_total_return_adj_close` | Yahoo: OHLC split-adjusted, `adj_close` total return |
| `ohlc_with_total_return_adj_close` | CSV with an adjusted-close column |
| `fully_adjusted_ohlc` | CSV whose OHLC are already split- and dividend-adjusted |
| `unadjusted_ohlc_no_dividends` | CSV without adjustment. **Returns exclude dividends.** |

## CSV import (fallback or licensed data)

```bash
usquant import-csv --file ~/Downloads/SPY.csv --symbol SPY --source "Vendor X daily export, 2026-11-01"
```

The importer:

1. maps common headers (`Date`, `Open`, `Adj Close`, `adjusted_close`, `Dividends`,
   `Stock Splits`, ...) to the canonical schema and parses dates (`--date-format`
   for ambiguous formats such as `%d/%m/%Y`);
2. decides the price basis: `--price-basis auto` picks `raw_with_adj_close` if
   there is an adjusted-close column, else `raw` (with a warning). Use
   `--price-basis adjusted` for files whose OHLC are already total-return adjusted;
3. validates the schema (positive prices, high ≥ low, no duplicate dates) and runs the
   quality checks;
4. writes `data/csv/<SYMBOL>.csv` and `data/csv/<SYMBOL>.provenance.json`. The
   sidecar holds the original file's SHA-256 and the `--source` text.

The `csv` provider also reads un-imported vendor files directly
(`data/csv/<symbol>.csv`, file name case-insensitive), but then provenance can only
say "local CSV file".

### Fallback

With `provider: auto` (used by `configs/study_spy_qqq.yaml`), each symbol is loaded
from Yahoo (through the cache) and, if that fails, from `data/csv/`. The
provenance notes record the failure, for example
`fallback: earlier source failed - yahoo: ConnectionError: ...`. When symbols come
from different sources, a warning is logged. Compare such runs with care.

A cached Yahoo download is already a valid CSV-provider file. To freeze a dataset,
copy `data/cache/yahoo/SPY.csv` and its `.provenance.json` into `data/csv/`.

## Quality checks

`usquant.data.quality.check_quality` runs on every series a backtest or study
loads. It never changes data:

| Check | Why |
|---|---|
| Calendar gaps > 5 days | Missing data. The only expected case for SPY/QQQ is the 2001-09-11 closure. |
| Open/close outside high-low | Corrupt bars |
| Zero-volume and stale (repeated) bars | Vendor fill-forward |
| Daily adjusted moves > 15% | Bad ticks or real events, to verify by hand. QQQ +16.8% on 2001-01-03 (surprise Fed cut) is real. |
| `adj_close / close` decreasing | Back-adjustment factors should only step up over time |
| **Dividend reconstruction** | Rebuild total return from `close` and cash dividends, then compare it with the vendor's `adj_close`. An annualised gap near 0 means no distribution is missing. |

For the committed study, the reconstruction gap was −0.003%/yr for SPY and
−0.0000%/yr for QQQ.

## Risk-free rate

Excess-return Sharpe ratios use the 13-week T-bill index (Yahoo `^IRX`, annualised
discount yield in %). It is converted to a daily rate with `(1 + y)^(1/252) - 1`, and
a missing quote takes the last earlier value. It is cached as
`data/cache/yahoo/IRX.csv` and falls back to `data/csv/IRX.csv`
(`date,close` in percent). If neither source is available, the study reports the
rf=0 Sharpe only.
