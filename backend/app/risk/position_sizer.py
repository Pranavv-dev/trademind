"""Position sizing strategies."""

from decimal import Decimal

import structlog

log = structlog.get_logger()


def fixed_percentage_size(
    capital: Decimal,
    price: float,
    max_pct: Decimal = Decimal("10.0"),
) -> int:
    """Calculate position size as a fixed percentage of capital.

    Returns quantity (number of shares). Rounds down to nearest integer.
    """
    if price <= 0 or capital <= 0:
        return 0
    max_value = capital * max_pct / Decimal("100")
    quantity = int(max_value / Decimal(str(price)))
    return max(quantity, 0)


def r_based_size(
    capital: Decimal,
    entry_price: float,
    stop_loss: float,
    risk_per_trade_pct: float = 0.5,
    max_notional_pct: float = 7.0,
) -> int:
    """Risk-based position sizing — Pattern #1 from STRATEGY_RESEARCH.md.

    Risk a FIXED dollar amount per trade (default 0.5% of capital) regardless of
    the stock's price level. The quantity adjusts to whatever stop-distance the
    agent set, so volatile names get smaller positions automatically and calm
    names get larger ones — at the same dollar risk.

    Formula: quantity = (capital × risk_per_trade_pct / 100) / |entry - stop|

    A `max_notional_pct` ceiling prevents absurd sizing on stocks with tiny stops
    (e.g. a 0.2% SL on a ₹100 stock would otherwise allocate 100% of capital).

    Args:
        capital: agent capital (Decimal)
        entry_price: planned entry price
        stop_loss: planned SL price (must be on the opposite side of entry)
        risk_per_trade_pct: % of capital at risk on this trade (typical 0.25-1.0)
        max_notional_pct: absolute ceiling on position notional as % of capital

    Returns:
        Quantity (int, floored to whole shares). 0 if any input invalid.
    """
    if entry_price <= 0 or capital <= 0 or stop_loss <= 0:
        return 0
    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share <= 0:
        return 0
    risk_budget = float(capital) * risk_per_trade_pct / 100.0
    r_based_qty = int(risk_budget / risk_per_share)
    # Notional ceiling guard
    max_notional = float(capital) * max_notional_pct / 100.0
    notional_capped_qty = int(max_notional / entry_price)
    return max(min(r_based_qty, notional_capped_qty), 0)


def atr_based_size(
    capital: Decimal,
    price: float,
    atr: float,
    risk_per_trade_pct: float = 1.0,
    atr_multiplier: float = 2.0,
) -> int:
    """Size position so that a 2×ATR stop-loss risks only risk_per_trade_pct of capital.

    risk_amount = capital × risk_per_trade_pct / 100
    risk_per_share = atr × atr_multiplier
    quantity = risk_amount / risk_per_share
    """
    if price <= 0 or atr <= 0 or capital <= 0:
        return 0
    risk_amount = float(capital) * risk_per_trade_pct / 100
    risk_per_share = atr * atr_multiplier
    if risk_per_share <= 0:
        return 0
    quantity = int(risk_amount / risk_per_share)
    return max(quantity, 0)


def kelly_criterion_size(
    capital: Decimal,
    price: float,
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.25,  # Use quarter-Kelly for safety
) -> int:
    """Kelly criterion position sizing (fractional Kelly for safety).

    kelly_pct = win_rate - (1 - win_rate) / (avg_win / avg_loss)
    Capped at max 10% of capital.
    """
    if avg_loss == 0 or price <= 0 or capital <= 0:
        return 0
    win_loss_ratio = avg_win / avg_loss
    kelly_pct = win_rate - (1 - win_rate) / win_loss_ratio
    kelly_pct = max(kelly_pct, 0) * fraction
    kelly_pct = min(kelly_pct, 0.10)  # Cap at 10%

    max_value = float(capital) * kelly_pct
    quantity = int(max_value / price)
    return max(quantity, 0)
