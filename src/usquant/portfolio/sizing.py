"""Convert strategy signals into target portfolio weights."""

from __future__ import annotations

import pandas as pd

SIZING_METHODS = ("equal_active", "fixed_slot")


def signals_to_weights(signals: pd.DataFrame, method: str = "equal_active") -> pd.DataFrame:
    """Translate ``[0, 1]`` signals into target weights (fractions of equity).

    ``equal_active``
        Split 100% of equity equally across symbols with a non-zero signal
        (one active symbol -> 100%, two -> 50% each). Fully invested whenever any
        signal is on.
    ``fixed_slot``
        Each symbol owns a fixed ``1/N`` slot of equity scaled by its signal; an
        inactive symbol's slot stays in cash.

    Risk limits (position caps, gross exposure) are applied afterwards by
    :func:`usquant.risk.limits.apply_weight_limits`.
    """
    sig = signals.clip(lower=0.0, upper=1.0).fillna(0.0)
    if method == "equal_active":
        active = (sig > 0).sum(axis=1)
        weights = sig.div(active.where(active > 0), axis=0).fillna(0.0)
    elif method == "fixed_slot":
        weights = sig / sig.shape[1]
    else:
        raise ValueError(f"unknown sizing method {method!r}; available: {SIZING_METHODS}")
    return weights
