"""Pre-trade risk limits applied to target weights, plus a drawdown circuit breaker."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class RiskLimits:
    """Portfolio-level risk constraints.

    max_position_weight:
        Cap on any single position as a fraction of equity (e.g. ``0.5``).
    max_gross_exposure:
        Cap on the sum of absolute weights (``1.0`` = no leverage).
    max_drawdown:
        Optional circuit breaker. When the close-to-peak drawdown of equity exceeds
        this fraction (e.g. ``0.2``), the engine liquidates at the next bar and stays
        flat for ``drawdown_cooldown`` bars, after which the peak resets to current
        equity and normal trading resumes. ``None`` disables it.
    """

    max_position_weight: float = 1.0
    max_gross_exposure: float = 1.0
    max_drawdown: float | None = None
    drawdown_cooldown: int = 21

    def __post_init__(self) -> None:
        if not 0 < self.max_position_weight <= 10:
            raise ValueError("max_position_weight must be in (0, 10]")
        if not 0 < self.max_gross_exposure <= 10:
            raise ValueError("max_gross_exposure must be in (0, 10]")
        if self.max_drawdown is not None and not 0 < self.max_drawdown < 1:
            raise ValueError("max_drawdown must be in (0, 1)")
        if self.drawdown_cooldown < 0:
            raise ValueError("drawdown_cooldown must be >= 0")


def apply_weight_limits(weights: pd.DataFrame, limits: RiskLimits) -> pd.DataFrame:
    """Clip each position to ``max_position_weight`` then scale rows whose gross
    exposure exceeds ``max_gross_exposure``. Excess weight goes to cash (it is not
    redistributed), which is the conservative choice."""
    cap = limits.max_position_weight
    w = weights.clip(lower=-cap, upper=cap)
    gross = w.abs().sum(axis=1)
    scale = (limits.max_gross_exposure / gross).where(gross > limits.max_gross_exposure, 1.0)
    return w.mul(scale, axis=0)


class DrawdownGuard:
    """Stateful helper used by the backtest loop for the drawdown circuit breaker."""

    def __init__(self, limits: RiskLimits):
        self.max_drawdown = limits.max_drawdown
        self.cooldown = limits.drawdown_cooldown
        self.peak: float | None = None
        self.bars_left = 0
        self.trip_count = 0

    @property
    def halted(self) -> bool:
        return self.bars_left > 0

    def update(self, equity: float) -> bool:
        """Feed end-of-bar equity; return True if trading is halted for the next bar."""
        if self.max_drawdown is None:
            return False
        if self.bars_left > 0:
            self.bars_left -= 1
            if self.bars_left == 0:
                self.peak = equity  # reset high-water mark after the cooldown
            return True
        self.peak = equity if self.peak is None else max(self.peak, equity)
        if equity < self.peak * (1 - self.max_drawdown):
            self.bars_left = max(self.cooldown, 1)
            self.trip_count += 1
            return True
        return False
