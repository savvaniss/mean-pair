"""Shared helpers for trading engines."""
from decimal import Decimal, ROUND_DOWN, InvalidOperation
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


def clamp_to_step(qty: float, step_size: str, min_qty: str) -> float:
    """Floor ``qty`` to the Binance ``LOT_SIZE`` filter using decimal math."""

    try:
        step_dec = Decimal(str(step_size))
        min_dec = Decimal(str(min_qty))
        qty_dec = Decimal(str(qty))
    except InvalidOperation:
        return 0.0

    if step_dec <= 0:
        return float(qty_dec)

    steps = (qty_dec / step_dec).to_integral_value(rounding=ROUND_DOWN)
    adjusted = (steps * step_dec).quantize(step_dec, rounding=ROUND_DOWN)

    if adjusted < min_dec:
        return 0.0

    return float(adjusted)
