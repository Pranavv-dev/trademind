"""NSE-realistic slippage model for paper-trading and backtesting.

Pattern #10 from STRATEGY_RESEARCH.md. Square-root market impact model:

    slippage_bps = c × σ × sqrt(Q / ADV)

where
    c   = calibration constant (0.5–1.0 for retail-scale orders, default 0.5)
    σ   = stock's realized volatility expressed in bps (e.g., 1.5% daily → 150)
    Q   = order quantity (shares)
    ADV = 20-day average daily volume (shares)

For typical NIFTY 50 retail orders with Q/ADV well below 0.1%, this yields
slippage of ~5–15 bps. We floor at a minimum half-spread (1.0 bps for ultra-
liquid NIFTY 50) to capture the bid-ask cost that exists even at infinitesimal
order sizes. We cap at 100 bps so a misconfiguration can't blow up a backtest.

The intent is two-fold:
  1. Paper-trading realism — without this, P&L mysteriously trails live P&L
     by 10–30 bps/trade as the system "fills at mid".
  2. Backtest cost honesty — pattern #10 specifically called out cost
     misspecification as the dominant reason for backtest-to-live blow-ups.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

DEFAULT_IMPACT_C = 0.5  # square-root impact coefficient
MIN_SLIPPAGE_BPS = 1.0  # half-spread floor for liquid NIFTY 50
MAX_SLIPPAGE_BPS = 100.0  # safety cap (1%)


@dataclass
class SlippageInputs:
    """All the per-trade context the slippage model needs.

    Use:
        s = SlippageInputs(price=2620.0, quantity=3,
                           realized_vol_pct=1.6, adv_shares=4_500_000)
        bps = compute_slippage_bps(s)
        adjusted = apply_slippage(2620.0, "BUY", bps)
    """

    price: float  # mid / LTP
    quantity: int  # order size in shares
    realized_vol_pct: float  # daily realized vol in PERCENT (e.g., 1.5 for 1.5%)
    adv_shares: int  # 20-day average daily volume
    impact_c: float = DEFAULT_IMPACT_C


def compute_slippage_bps(inp: SlippageInputs) -> float:
    """Square-root market impact, clamped to [MIN, MAX] bps."""
    if inp.quantity <= 0 or inp.adv_shares <= 0 or inp.price <= 0:
        return MIN_SLIPPAGE_BPS
    vol_bps = max(inp.realized_vol_pct, 0.0) * 100.0  # 1.5% → 150 bps
    ratio = inp.quantity / inp.adv_shares
    raw = inp.impact_c * vol_bps * math.sqrt(ratio)
    return max(MIN_SLIPPAGE_BPS, min(MAX_SLIPPAGE_BPS, raw))


def apply_slippage(price: float, side: str, slippage_bps: float) -> float:
    """Adjust a fill price for slippage. BUY fills HIGHER, SELL fills LOWER.

    Returns the simulated fill price.
    """
    adjustment = price * slippage_bps / 10_000.0
    if side == "BUY":
        return price + adjustment
    if side == "SELL":
        return max(price - adjustment, 0.01)
    return price


def apply_slippage_decimal(price: Decimal, side: str, slippage_bps: float) -> Decimal:
    """Decimal variant for the execution path. Same semantics as apply_slippage."""
    adj = price * Decimal(str(slippage_bps)) / Decimal("10000")
    if side == "BUY":
        return price + adj
    if side == "SELL":
        out = price - adj
        return out if out > 0 else Decimal("0.01")
    return price


def estimate_round_trip_slippage_pct(
    price: float,
    quantity: int,
    realized_vol_pct: float,
    adv_shares: int,
    impact_c: float = DEFAULT_IMPACT_C,
) -> float:
    """Convenience: total round-trip slippage as a fraction (not bps).

    Used by the backtest's expectancy check — strategies whose edge is smaller
    than 2×slippage are immediately suspect and should be rejected pre-deploy.
    """
    inp = SlippageInputs(
        price=price,
        quantity=quantity,
        realized_vol_pct=realized_vol_pct,
        adv_shares=adv_shares,
        impact_c=impact_c,
    )
    bps_one_leg = compute_slippage_bps(inp)
    return 2 * bps_one_leg / 10_000.0
