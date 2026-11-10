"""Trading strategies and the name -> class registry used by configs."""

from usquant.strategies.base import Strategy
from usquant.strategies.ma_crossover import MACrossover
from usquant.strategies.momentum import Momentum

STRATEGIES: dict[str, type[Strategy]] = {
    MACrossover.name: MACrossover,
    Momentum.name: Momentum,
}


def make_strategy(name: str, **params) -> Strategy:
    try:
        cls = STRATEGIES[name]
    except KeyError:
        raise ValueError(f"unknown strategy {name!r}; available: {sorted(STRATEGIES)}") from None
    return cls(**params)


__all__ = ["STRATEGIES", "MACrossover", "Momentum", "Strategy", "make_strategy"]
