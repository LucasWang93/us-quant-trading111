"""Static matplotlib charts for backtest results (PNG, headless-safe)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display needed; safe in CI and servers
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from usquant.metrics.performance import drawdown_series  # noqa: E402

# Fixed categorical order (never cycled); text/grid stay neutral.
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_MUTED = "#52514e"
GRID = "#e4e3df"


def _style(ax, title: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=11, color=TEXT, fontweight="bold")
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    ax.grid(False, axis="x")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_MUTED, labelsize=9)


def _new_fig(height: float = 4.0):
    fig, ax = plt.subplots(figsize=(10, height), dpi=120)
    fig.patch.set_facecolor(SURFACE)
    return fig, ax


def plot_equity(result, log_scale: bool = True):
    fig, ax = _new_fig()
    series = [(result.strategy_name, result.equity_curve)]
    if result.benchmark is not None:
        series.append((f"{result.benchmark_symbol} buy & hold", result.benchmark))
    for i, (label, s) in enumerate(series):
        ax.plot(s.index, s.to_numpy(), color=SERIES_COLORS[i], linewidth=2, label=label)
        ax.annotate(
            f"{s.iloc[-1]:,.0f}", (s.index[-1], s.iloc[-1]), xytext=(4, 0), textcoords="offset points",
            fontsize=8, color=TEXT_MUTED, va="center",
        )
    if log_scale:
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(matplotlib.ticker.LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
        ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    _style(ax, "Equity (USD" + (", log scale)" if log_scale else ")"))
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT_MUTED, loc="upper left")
    fig.tight_layout()
    return fig


def plot_drawdown(result):
    fig, ax = _new_fig(3.0)
    series = [(result.strategy_name, result.equity_curve)]
    if result.benchmark is not None:
        series.append((f"{result.benchmark_symbol} buy & hold", result.benchmark))
    for i, (label, s) in enumerate(series):
        dd = drawdown_series(s) * 100
        ax.plot(dd.index, dd.to_numpy(), color=SERIES_COLORS[i], linewidth=1.5, label=label)
        if i == 0:
            ax.fill_between(dd.index, dd.to_numpy(), 0, color=SERIES_COLORS[i], alpha=0.15, linewidth=0)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    _style(ax, "Drawdown from peak")
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT_MUTED, loc="lower left")
    fig.tight_layout()
    return fig


def plot_weights(result):
    fig, ax = _new_fig(3.0)
    w = result.weights.clip(lower=0) * 100
    colors = [SERIES_COLORS[i % len(SERIES_COLORS)] for i in range(w.shape[1])]
    ax.stackplot(w.index, w.T.to_numpy(), labels=list(w.columns), colors=colors, alpha=0.85,
                 edgecolor=SURFACE, linewidth=0.5)
    ax.set_ylim(0, max(100, float(w.sum(axis=1).max()) * 1.05))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    _style(ax, "Portfolio weights at close (remainder in cash)")
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT_MUTED, loc="lower right", bbox_to_anchor=(1.0, 1.0),
              ncol=len(w.columns))
    fig.tight_layout()
    return fig


def save_all_plots(result, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, fn in (("equity.png", plot_equity), ("drawdown.png", plot_drawdown), ("weights.png", plot_weights)):
        fig = fn(result)
        path = out / name
        fig.savefig(path, facecolor=fig.get_facecolor())
        plt.close(fig)
        paths.append(path)
    return paths


def rolling_sharpe(returns: pd.Series, window: int = 252) -> pd.Series:
    """Helper for notebooks: annualised rolling Sharpe (rf = 0)."""
    return returns.rolling(window).mean() / returns.rolling(window).std() * (252**0.5)
