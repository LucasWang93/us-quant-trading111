"""Broker abstraction (design placeholder for a future phase).

Phase 1 contains **no broker connectivity**. This module only fixes the interface
that a future paper-trading adapter (e.g. Interactive Brokers TWS/IB Gateway on
the *paper* port) must implement, so research code and execution code stay
decoupled.

Safety rules for any future implementation:

* Paper trading only. An adapter must refuse to start unless ``mode`` is
  :attr:`TradingMode.PAPER`; there is deliberately no LIVE member in the enum.
* Credentials/host/port come from environment variables, never from the repo.
* Orders are generated from the same target-weight output as the backtester and
  must pass :mod:`usquant.risk` limits before submission.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class TradingMode(Enum):
    PAPER = "paper"


@dataclass(frozen=True)
class Order:
    symbol: str
    quantity: float  # positive = buy, negative = sell
    order_type: str = "MKT"
    limit_price: float | None = None


class Broker(ABC):
    mode: TradingMode = TradingMode.PAPER

    def __init__(self, mode: TradingMode = TradingMode.PAPER):
        if mode is not TradingMode.PAPER:
            raise PermissionError("only paper trading is permitted")
        self.mode = mode

    @abstractmethod
    def positions(self) -> dict[str, float]: ...

    @abstractmethod
    def cash(self) -> float: ...

    @abstractmethod
    def submit(self, order: Order) -> str: ...
