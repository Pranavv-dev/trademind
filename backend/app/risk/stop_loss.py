"""Stop-loss calculation strategies."""


def fixed_percentage_stop(price: float, pct: float = 3.0, side: str = "BUY") -> float:
    """Fixed percentage stop-loss from entry price."""
    if side == "BUY":
        return round(price * (1 - pct / 100), 2)
    return round(price * (1 + pct / 100), 2)


def atr_stop(price: float, atr: float, multiplier: float = 2.0, side: str = "BUY") -> float:
    """ATR-based stop-loss."""
    distance = atr * multiplier
    if side == "BUY":
        return round(price - distance, 2)
    return round(price + distance, 2)


def trailing_stop(
    current_price: float,
    highest_since_entry: float,
    lowest_since_entry: float | None = None,
    trail_pct: float = 3.0,
    side: str = "BUY",
) -> float:
    """Trailing stop-loss that follows the price.

    For BUY: stop = highest_since_entry × (1 - trail_pct/100)
    For SELL: stop = lowest_since_entry × (1 + trail_pct/100)
    """
    if side == "BUY":
        return round(highest_since_entry * (1 - trail_pct / 100), 2)
    low = lowest_since_entry if lowest_since_entry is not None else current_price
    return round(low * (1 + trail_pct / 100), 2)
