"""Command-line interface.

Examples::

    usquant download --symbols SPY QQQ --start 2005-01-01
    usquant backtest --config configs/ma_crossover_spy_qqq.yaml
    usquant backtest --config configs/momentum_spy_qqq.yaml --provider synthetic
    usquant study --config configs/study_spy_qqq.yaml
    usquant import-csv --file ~/Downloads/SPY.csv --symbol SPY --source "Vendor X export"
"""

from __future__ import annotations

import argparse
import logging
import sys

from usquant.config import load_config
from usquant.data.providers import PROVIDERS, make_provider
from usquant.data.store import DataStore
from usquant.pipeline import run_experiment
from usquant.reporting.report import format_metrics_table


def _cmd_download(args: argparse.Namespace) -> int:
    provider = make_provider(args.provider)
    store = DataStore(args.cache_dir)
    for sym in args.symbols:
        df = store.load(provider, sym, args.start, args.end, refresh=args.refresh)
        print(f"{sym}: {len(df)} bars {df.index[0].date()} -> {df.index[-1].date()}  ({store.path(provider, sym)})")
    return 0


def _cmd_import_csv(args: argparse.Namespace) -> int:
    from usquant.data.csv_import import import_csv

    path, prov, quality = import_csv(
        args.file, args.symbol, args.dest, source=args.source, price_basis=args.price_basis,
        date_format=args.date_format,
    )
    print(f"{args.symbol}: {prov.rows} bars {prov.first_date} -> {prov.last_date}  ({path})")
    print(f"price basis: {prov.price_basis}")
    for w in quality.warnings:
        print(f"WARNING: {w}")
    return 0


def _cmd_study(args: argparse.Namespace) -> int:
    from usquant.study import load_study_config, run_study, write_study

    cfg = load_study_config(args.config)
    if args.provider:
        cfg.data.provider = args.provider
        cfg.data.provider_options = {"directory": args.csv_dir} if args.provider == "csv" else {}
    if args.refresh:
        cfg.data.refresh = True
    if args.provider == "synthetic":
        cfg.risk_free_symbol = None
    sr = run_study(cfg)
    out = write_study(sr, out_root=args.output, run_dir=args.run_dir, plots=not args.no_plots)
    for label, m in sr.metrics.items():
        print(
            f"{label:32s} CAGR {m['cagr']:7.2%}  Sharpe {m['sharpe_rf0']:5.2f}  MaxDD {m['max_drawdown']:8.2%}  "
            f"round trips {m['round_trips']:4d}  fills {m['fills']:4d}"
        )
    print(f"\nReport written to {out / 'report.md'}")
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.provider:
        cfg.data.provider = args.provider
        if args.provider != "csv":
            cfg.data.provider_options = {}
    if args.no_plots:
        cfg.output.plots = False
    out = run_experiment(cfg, write=not args.no_write, out_dir=args.output)
    print(f"# {cfg.name}  (provider={cfg.data.provider}, strategy={cfg.strategy.name} {cfg.strategy.params})\n")
    print(format_metrics_table(out.metrics))
    if out.run_dir:
        print(f"\nResults written to {out.run_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="usquant", description="US equity quant research & backtesting")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("download", help="download and cache historical daily bars")
    d.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    d.add_argument("--provider", default="yahoo", choices=sorted(PROVIDERS))
    d.add_argument("--start", default=None)
    d.add_argument("--end", default=None)
    d.add_argument("--cache-dir", default="data/cache")
    d.add_argument("--refresh", action="store_true", help="ignore cache and re-download")
    d.set_defaults(func=_cmd_download)

    b = sub.add_parser("backtest", help="run a backtest from a YAML config")
    b.add_argument("--config", required=True)
    b.add_argument("--provider", choices=sorted(PROVIDERS), help="override the config's data provider")
    b.add_argument("--output", help="override output root directory")
    b.add_argument("--no-plots", action="store_true")
    b.add_argument("--no-write", action="store_true", help="print metrics only")
    b.set_defaults(func=_cmd_backtest)

    st = sub.add_parser("study", help="run a multi-strategy / benchmark study and write a Markdown report")
    st.add_argument("--config", required=True)
    st.add_argument("--provider", choices=sorted(PROVIDERS), help="override the config's data provider")
    st.add_argument("--csv-dir", default="data/csv", help="directory for --provider csv")
    st.add_argument("--refresh", action="store_true", help="re-download instead of using the cache")
    st.add_argument("--output", help="override output root directory")
    st.add_argument("--run-dir", help="write into exactly this folder (no timestamp)")
    st.add_argument("--no-plots", action="store_true")
    st.set_defaults(func=_cmd_study)

    ic = sub.add_parser("import-csv", help="validate a vendor CSV and store it for the csv / auto providers")
    ic.add_argument("--file", required=True, help="vendor CSV with a date column and OHLC(V) columns")
    ic.add_argument("--symbol", required=True)
    ic.add_argument("--dest", default="data/csv", help="destination directory (default data/csv)")
    ic.add_argument("--source", required=True, help="where the file came from, recorded in provenance")
    ic.add_argument("--price-basis", default="auto", choices=["auto", "raw_with_adj_close", "adjusted", "raw"])
    ic.add_argument("--date-format", default=None, help="strptime format if dates are ambiguous, e.g. %%d/%%m/%%Y")
    ic.set_defaults(func=_cmd_import_csv)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
