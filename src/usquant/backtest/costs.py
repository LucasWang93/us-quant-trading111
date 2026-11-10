"""Transaction cost models: commissions and slippage."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommissionModel:
    """Per-order commission.

    ``commission = |qty| * per_share + |notional| * bps / 1e4``, then floored at
    ``min_per_order`` and capped at ``max_pct_of_value * |notional|`` (if set).

    The defaults approximate Interactive Brokers' US "Fixed" pricing tier
    (USD 0.005/share, USD 1.00 minimum, max 1% of trade value). They exclude
    exchange/regulatory pass-through fees (SEC fee, FINRA TAF), which are small for
    ETFs; set ``bps`` to model them or a percentage-based broker.
    """

    per_share: float = 0.005
    min_per_order: float = 1.0
    max_pct_of_value: float | None = 0.01
    bps: float = 0.0

    def __post_init__(self) -> None:
        if self.per_share < 0 or self.min_per_order < 0 or self.bps < 0:
            raise ValueError("commission parameters must be non-negative")

    def __call__(self, quantity: float, price: float) -> float:
        if quantity == 0:
            return 0.0
        notional = abs(quantity * price)
        fee = abs(quantity) * self.per_share + notional * self.bps / 1e4
        fee = max(fee, self.min_per_order)
        if self.max_pct_of_value is not None:
            fee = min(fee, self.max_pct_of_value * notional)
        return fee

    @classmethod
    def zero(cls) -> "CommissionModel":
        return cls(per_share=0.0, min_per_order=0.0, max_pct_of_value=None, bps=0.0)


@dataclass(frozen=True)
class SlippageModel:
    """Fixed adverse slippage in basis points of the reference price.

    Buys fill at ``price * (1 + bps/1e4)`` and sells at ``price * (1 - bps/1e4)``.
    Covers half the bid-ask spread plus a little market impact; for liquid ETFs such
    as SPY/QQQ, 1-5 bps is a reasonable range for modest order sizes.
    """

    bps: float = 2.0

    def __post_init__(self) -> None:
        if self.bps < 0:
            raise ValueError("slippage bps must be non-negative")

    def fill_price(self, quantity: float, price: float) -> float:
        if quantity > 0:
            return price * (1 + self.bps / 1e4)
        if quantity < 0:
            return price * (1 - self.bps / 1e4)
        return price
