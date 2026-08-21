"""Paper trading engine — simulates order fills at last traded price + slippage.

Updated 2026-05-30 (Pattern #10 / STRATEGY_RESEARCH.md): fills now apply a
square-root market-impact slippage model. Without this, paper P&L tracks mid
price exactly while live P&L would trail by 10-30 bps/trade — making paper
results overstate real edge.
"""

import os
import uuid
from decimal import Decimal

import structlog

from app.execution.broker.base import BaseBroker
from app.execution.slippage import (
    DEFAULT_IMPACT_C,
    SlippageInputs,
    apply_slippage_decimal,
    compute_slippage_bps,
)

log = structlog.get_logger()

# Tunable via env; set PAPER_SLIPPAGE_DISABLED=1 to revert to old behavior for tests.
SLIPPAGE_DISABLED = os.environ.get("PAPER_SLIPPAGE_DISABLED") == "1"


class PaperBroker(BaseBroker):
    """Simulates broker execution for paper trading.

    Fills happen at LTP +/- slippage to match what live execution would deliver.
    No partial fills (NIFTY 50 retail orders are tiny relative to ADV).
    """

    def __init__(self):
        self._orders: dict[str, dict] = {}
        self._positions: dict[str, dict] = {}  # keyed by symbol

    async def place_order(
        self,
        symbol: str,
        exchange: str,
        side: str,
        quantity: int,
        price: Decimal,
        order_type: str,
        product: str,
        realized_vol_pct: float | None = None,
        adv_shares: int | None = None,
    ) -> dict:
        """Simulate order placement — fills at price ± slippage.

        Args:
            realized_vol_pct: daily realized vol in PERCENT (e.g. 1.5 for 1.5%).
                If None, defaults to a conservative 2.0% for NIFTY large-caps.
            adv_shares: 20-day average daily volume. If None, defaults to
                5,000,000 — a conservative liquid-NIFTY-50 estimate.
        """
        order_id = f"PAPER-{uuid.uuid4().hex[:12].upper()}"

        if SLIPPAGE_DISABLED:
            fill_price = price
            slippage_bps = 0.0
        else:
            slip = SlippageInputs(
                price=float(price),
                quantity=quantity,
                realized_vol_pct=realized_vol_pct if realized_vol_pct is not None else 2.0,
                adv_shares=adv_shares if adv_shares is not None else 5_000_000,
                impact_c=DEFAULT_IMPACT_C,
            )
            slippage_bps = compute_slippage_bps(slip)
            fill_price = apply_slippage_decimal(price, side, slippage_bps)

        order = {
            "order_id": order_id,
            "symbol": symbol,
            "exchange": exchange,
            "side": side,
            "quantity": quantity,
            "price": float(price),
            "fill_price": float(fill_price),
            "slippage_bps": round(slippage_bps, 2),
            "order_type": order_type,
            "product": product,
            "status": "filled",
        }
        self._orders[order_id] = order

        # Update simulated positions at the SLIPPAGE-ADJUSTED fill price
        self._update_position(symbol, exchange, side, quantity, fill_price)

        log.info(
            "paper_order_filled",
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            mid_price=float(price),
            fill_price=float(fill_price),
            slippage_bps=round(slippage_bps, 2),
        )
        return order

    async def get_order_status(self, order_id: str) -> dict:
        order = self._orders.get(order_id)
        if not order:
            return {"order_id": order_id, "status": "not_found"}
        return order

    async def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order and order["status"] not in ("filled", "cancelled"):
            order["status"] = "cancelled"
            return True
        return False

    async def get_positions(self) -> list[dict]:
        return [pos for pos in self._positions.values() if pos.get("quantity", 0) != 0]

    async def get_holdings(self) -> list[dict]:
        return await self.get_positions()

    def _update_position(
        self,
        symbol: str,
        exchange: str,
        side: str,
        quantity: int,
        price: Decimal,
    ) -> None:
        """Update the in-memory position tracker."""
        key = f"{exchange}:{symbol}"
        pos = self._positions.get(key)

        if pos is None:
            direction = quantity if side == "BUY" else -quantity
            self._positions[key] = {
                "symbol": symbol,
                "exchange": exchange,
                "quantity": direction,
                "avg_price": float(price),
                "realized_pnl": 0.0,
            }
            return

        if side == "BUY":
            if pos["quantity"] >= 0:
                # Adding to long position — average up
                total_cost = pos["avg_price"] * pos["quantity"] + float(price) * quantity
                pos["quantity"] += quantity
                pos["avg_price"] = total_cost / pos["quantity"] if pos["quantity"] > 0 else 0
            else:
                # Closing short position
                close_qty = min(quantity, abs(pos["quantity"]))
                pnl = (pos["avg_price"] - float(price)) * close_qty
                pos["realized_pnl"] += pnl
                pos["quantity"] += quantity
                if pos["quantity"] > 0:
                    pos["avg_price"] = float(price)
        else:  # SELL
            if pos["quantity"] <= 0:
                # Adding to short position — weighted average of existing and new
                existing_qty = abs(pos["quantity"])
                total_cost = pos["avg_price"] * existing_qty + float(price) * quantity
                pos["quantity"] -= quantity
                pos["avg_price"] = total_cost / (existing_qty + quantity)
            else:
                # Closing long position
                close_qty = min(quantity, pos["quantity"])
                pnl = (float(price) - pos["avg_price"]) * close_qty
                pos["realized_pnl"] += pnl
                pos["quantity"] -= quantity
                if pos["quantity"] < 0:
                    pos["avg_price"] = float(price)
