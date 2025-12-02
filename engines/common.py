"""Shared helpers for trading engines."""
from typing import List, Tuple


def compute_ma_std_window(prices: List[float], window: int) -> Tuple[float, float]:
    """
    Compute mean/std over the last `window` prices.
    If window >= len(prices), uses all prices.
    Behavior is aligned with the original helper used by both bots.
    """
    if not prices:
        return 0.0, 0.0

    if window <= 0 or window > len(prices):
        window = len(prices)

    subset = prices[-window:]
    mean = sum(subset) / len(subset)
    var = sum((p - mean) ** 2 for p in subset) / len(subset)
    std = var ** 0.5 if var > 0 else 0.0
    return mean, std
