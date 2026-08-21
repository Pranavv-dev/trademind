"""Zerodha brokerage and statutory charges calculator.

Calculates all-in trading costs for NSE equity trades:
- Brokerage (₹0 delivery, ₹20 or 0.03% intraday)
- STT (Securities Transaction Tax)
- Exchange transaction charges
- GST on brokerage + txn charges
- SEBI turnover fee
- Stamp duty
"""

from decimal import ROUND_HALF_UP, Decimal

# Zerodha charge rates (as of 2026)
BROKERAGE_INTRADAY_PCT = Decimal("0.03")  # 0.03% or ₹20, whichever is lower
BROKERAGE_INTRADAY_CAP = Decimal("20")

STT_DELIVERY_PCT = Decimal("0.1")  # 0.1% on both buy and sell
STT_INTRADAY_PCT = Decimal("0.025")  # 0.025% on sell side only

NSE_TXN_CHARGE_PCT = Decimal("0.00345")  # NSE equity transaction charge
BSE_TXN_CHARGE_PCT = Decimal("0.00375")  # BSE equity transaction charge

GST_PCT = Decimal("18")  # 18% on brokerage + txn charges

SEBI_CHARGE_PER_CRORE = Decimal("10")  # ₹10 per crore of turnover

STAMP_DUTY_BUY_PCT = Decimal("0.015")  # 0.015% on buy side only


def calculate_charges(
    price: Decimal,
    quantity: int,
    side: str,
    product: str = "CNC",
    exchange: str = "NSE",
) -> dict:
    """Calculate all trading charges for a single leg.

    Args:
        price: Fill price per share
        quantity: Number of shares
        side: "BUY" or "SELL"
        product: "CNC" (delivery) or "MIS" (intraday)
        exchange: "NSE" or "BSE"

    Returns:
        dict with breakdown and total charges
    """
    turnover = price * quantity

    # 1. Brokerage
    if product == "CNC":
        brokerage = Decimal("0")  # Free for delivery
    else:
        brokerage = min(
            turnover * BROKERAGE_INTRADAY_PCT / 100,
            BROKERAGE_INTRADAY_CAP,
        )

    # 2. STT
    if product == "CNC":
        stt = turnover * STT_DELIVERY_PCT / 100
    else:
        # Intraday: STT only on sell side
        stt = (turnover * STT_INTRADAY_PCT / 100) if side == "SELL" else Decimal("0")

    # 3. Exchange transaction charges
    txn_pct = NSE_TXN_CHARGE_PCT if exchange == "NSE" else BSE_TXN_CHARGE_PCT
    txn_charge = turnover * txn_pct / 100

    # 4. GST (on brokerage + txn charge)
    gst = (brokerage + txn_charge) * GST_PCT / 100

    # 5. SEBI charges
    sebi = turnover * SEBI_CHARGE_PER_CRORE / Decimal("10000000")

    # 6. Stamp duty (buy side only)
    stamp = (turnover * STAMP_DUTY_BUY_PCT / 100) if side == "BUY" else Decimal("0")

    total = brokerage + stt + txn_charge + gst + sebi + stamp

    return {
        "turnover": _round(turnover),
        "brokerage": _round(brokerage),
        "stt": _round(stt),
        "txn_charge": _round(txn_charge),
        "gst": _round(gst),
        "sebi": _round(sebi),
        "stamp_duty": _round(stamp),
        "total": _round(total),
    }


def calculate_round_trip(
    buy_price: Decimal,
    sell_price: Decimal,
    quantity: int,
    product: str = "CNC",
    exchange: str = "NSE",
) -> Decimal:
    """Calculate total charges for a buy+sell round trip."""
    buy_charges = calculate_charges(buy_price, quantity, "BUY", product, exchange)
    sell_charges = calculate_charges(sell_price, quantity, "SELL", product, exchange)
    return buy_charges["total"] + sell_charges["total"]


def _round(val: Decimal) -> Decimal:
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
