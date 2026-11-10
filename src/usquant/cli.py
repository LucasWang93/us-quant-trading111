"""Command-line interface.

Examples::

    usquant download --symbols SPY QQQ --start 2005-01-01
    usquant backtest --config configs/ma_crossover_spy_qqq.yaml
    usquant backtest --config configs/momentum_spy_qqq.yaml --provider synthetic
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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
