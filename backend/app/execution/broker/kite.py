"""Zerodha Kite Connect broker adapter — live order placement, status, and positions."""

import asyncio
from decimal import Decimal
from functools import partial

import structlog
from kiteconnect import KiteConnect

from app.config import settings
from app.execution.broker.base import BaseBroker

log = structlog.get_logger()

# Kite order variety mapping
VARIETY_MAP = {
    "MARKET": "regular",
    "LIMIT": "regular",
    "SL": "regular",
    "SL-M": "regular",
}

# Kite order type mapping
ORDER_TYPE_MAP = {
    "MARKET": "MARKET",
    "LIMIT": "LIMIT",
    "SL": "SL",
    "SL-M": "SL-M",
}

# Kite product mapping
PRODUCT_MAP = {
    "CNC": "CNC",  # Delivery (equity)
    "MIS": "MIS",  # Intraday
    "NRML": "NRML",  # Carry forward (F&O)
}

# Kite transaction type mapping
SIDE_MAP = {
    "BUY": "BUY",
    "SELL": "SELL",
}

# Kite status to our status mapping
STATUS_MAP = {
    "COMPLETE": "filled",
    "REJECTED": "rejected",
    "CANCELLED": "cancelled",
    "OPEN": "placed",
    "TRIGGER PENDING": "placed",
    "AMO REQ RECEIVED": "placed",
}


class KiteBroker(BaseBroker):
    """Zerodha Kite Connect broker — places real orders on NSE/BSE/NFO."""

    def __init__(
        self,
        api_key: str | None = None,
        access_token: str | None = None,
    ):
        self.api_key = api_key or settings.kite_api_key
        self.access_token = access_token or settings.kite_access_token
        self._kite: KiteConnect | None = None

    @property
    def kite(self) -> KiteConnect:
        """Lazy-init Kite Connect client."""
        if self._kite is None:
            if not self.api_key:
                raise RuntimeError("Kite API key not configured")
            self._kite = KiteConnect(api_key=self.api_key)
            if self.access_token:
                self._kite.set_access_token(self.access_token)
        return self._kite

    def set_access_token(self, token: str) -> None:
        """Update access token (called after login flow)."""
        self.access_token = token
        self.kite.set_access_token(token)

    def get_login_url(self) -> str:
        """Get the Kite Connect login URL for user authentication."""
        return self.kite.login_url()

    async def generate_session(self, request_token: str) -> dict:
        """Exchange request_token for access_token after login redirect."""
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None,
            partial(self.kite.generate_session, request_token, api_secret=settings.kite_api_secret),
        )
        self.access_token = data["access_token"]
        self.kite.set_access_token(self.access_token)
        log.info("kite_session_created", user_id=data.get("user_id"))
        return data

    # ── BaseBroker interface ──

    async def place_order(
        self,
        symbol: str,
        exchange: str,
        side: str,
        quantity: int,
        price: Decimal,
        order_type: str,
        product: str,
    ) -> dict:
        """Place an order via Kite Connect API."""
        if not self.access_token:
            raise RuntimeError("Kite access token not set. Complete login flow first.")

        kite_exchange = self._map_exchange(exchange)
        kite_side = SIDE_MAP.get(side, side)
        kite_order_type = ORDER_TYPE_MAP.get(order_type, "MARKET")
        kite_product = PRODUCT_MAP.get(product, "CNC")
        kite_variety = VARIETY_MAP.get(order_type, "regular")

        params = {
            "tradingsymbol": symbol,
            "exchange": kite_exchange,
            "transaction_type": kite_side,
            "quantity": quantity,
            "order_type": kite_order_type,
            "product": kite_product,
            "variety": kite_variety,
        }

        # Only set price for LIMIT orders
        if kite_order_type == "LIMIT":
            params["price"] = float(price)
        elif kite_order_type == "SL":
            params["trigger_price"] = float(price)
            params["price"] = float(price)

        log.info(
            "kite_placing_order",
            symbol=symbol,
            side=kite_side,
            quantity=quantity,
            order_type=kite_order_type,
        )

        loop = asyncio.get_running_loop()
        try:
            order_id = await loop.run_in_executor(
                None,
                partial(self.kite.place_order, **params),
            )
        except Exception as e:
            log.error("kite_order_failed", error=str(e), symbol=symbol)
            raise

        log.info("kite_order_placed", order_id=order_id, symbol=symbol)

        # Fetch order status to get fill details
        status = await self.get_order_status(str(order_id))
        return {
            "order_id": str(order_id),
            "status": status.get("status", "placed"),
            "fill_price": status.get("fill_price"),
        }

    async def get_order_status(self, order_id: str) -> dict:
        """Get order status from Kite Connect."""
        loop = asyncio.get_running_loop()
        try:
            history = await loop.run_in_executor(
                None,
                partial(self.kite.order_history, order_id),
            )
        except Exception as e:
            log.error("kite_order_status_error", order_id=order_id, error=str(e))
            return {"order_id": order_id, "status": "unknown", "error": str(e)}

        if not history:
            return {"order_id": order_id, "status": "unknown"}

        # Last entry is the most recent status
        latest = history[-1]
        kite_status = latest.get("status", "")
        our_status = STATUS_MAP.get(kite_status, "placed")

        result = {
            "order_id": order_id,
            "status": our_status,
            "kite_status": kite_status,
            "fill_price": latest.get("average_price"),
            "filled_quantity": latest.get("filled_quantity", 0),
            "pending_quantity": latest.get("pending_quantity", 0),
            "status_message": latest.get("status_message", ""),
        }

        if kite_status == "REJECTED":
            log.warning(
                "kite_order_rejected",
                order_id=order_id,
                reason=latest.get("status_message", ""),
            )

        return result

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                partial(self.kite.cancel_order, variety="regular", order_id=order_id),
            )
            log.info("kite_order_cancelled", order_id=order_id)
            return True
        except Exception as e:
            log.error("kite_cancel_failed", order_id=order_id, error=str(e))
            return False

    async def get_positions(self) -> list[dict]:
        """Get all current positions (day + net)."""
        loop = asyncio.get_running_loop()
        try:
            positions = await loop.run_in_executor(None, self.kite.positions)
        except Exception as e:
            log.error("kite_positions_error", error=str(e))
            return []

        result = []
        for pos in positions.get("net", []):
            if pos.get("quantity", 0) == 0:
                continue
            result.append(
                {
                    "symbol": pos["tradingsymbol"],
                    "exchange": pos["exchange"],
                    "quantity": pos["quantity"],
                    "avg_price": pos["average_price"],
                    "last_price": pos["last_price"],
                    "pnl": pos["pnl"],
                    "product": pos["product"],
                    "side": "LONG" if pos["quantity"] > 0 else "SHORT",
                }
            )

        return result

    async def get_holdings(self) -> list[dict]:
        """Get CNC (delivery) holdings."""
        loop = asyncio.get_running_loop()
        try:
            holdings = await loop.run_in_executor(None, self.kite.holdings)
        except Exception as e:
            log.error("kite_holdings_error", error=str(e))
            return []

        return [
            {
                "symbol": h["tradingsymbol"],
                "exchange": h["exchange"],
                "quantity": h["quantity"],
                "avg_price": h["average_price"],
                "last_price": h["last_price"],
                "pnl": h["pnl"],
                "day_change_pct": h.get("day_change_percentage", 0),
            }
            for h in holdings
            if h.get("quantity", 0) > 0
        ]

    # ── Additional Kite-specific methods ──

    async def get_orders(self) -> list[dict]:
        """Get all orders for today."""
        loop = asyncio.get_running_loop()
        try:
            orders = await loop.run_in_executor(None, self.kite.orders)
        except Exception as e:
            log.error("kite_orders_error", error=str(e))
            return []

        return [
            {
                "order_id": o["order_id"],
                "symbol": o["tradingsymbol"],
                "exchange": o["exchange"],
                "side": o["transaction_type"],
                "quantity": o["quantity"],
                "price": o.get("price"),
                "fill_price": o.get("average_price"),
                "status": STATUS_MAP.get(o.get("status", ""), "unknown"),
                "kite_status": o.get("status"),
                "order_type": o.get("order_type"),
                "product": o.get("product"),
                "placed_at": o.get("order_timestamp"),
                "filled_at": o.get("exchange_timestamp"),
            }
            for o in orders
        ]

    async def get_margins(self) -> dict:
        """Get account margins (equity + commodity)."""
        loop = asyncio.get_running_loop()
        try:
            margins = await loop.run_in_executor(None, self.kite.margins)
        except Exception as e:
            log.error("kite_margins_error", error=str(e))
            return {}

        equity = margins.get("equity", {})
        return {
            "available_cash": equity.get("available", {}).get("cash", 0),
            "available_margin": equity.get("available", {}).get("live_balance", 0),
            "used_margin": equity.get("utilised", {}).get("debits", 0),
            "net": equity.get("net", 0),
        }

    async def get_quote(self, exchange: str, symbol: str) -> dict | None:
        """Get a single quote from Kite."""
        instrument = f"{self._map_exchange(exchange)}:{symbol}"
        loop = asyncio.get_running_loop()
        try:
            quotes = await loop.run_in_executor(
                None,
                partial(self.kite.quote, instrument),
            )
        except Exception as e:
            log.error("kite_quote_error", instrument=instrument, error=str(e))
            return None

        q = quotes.get(instrument)
        if not q:
            return None

        return {
            "symbol": symbol,
            "exchange": exchange,
            "ltp": q["last_price"],
            "open": q["ohlc"]["open"],
            "high": q["ohlc"]["high"],
            "low": q["ohlc"]["low"],
            "close": q["ohlc"]["close"],
            "volume": q.get("volume", 0),
            "change": q["last_price"] - q["ohlc"]["close"],
            "change_pct": (
                ((q["last_price"] - q["ohlc"]["close"]) / q["ohlc"]["close"] * 100)
                if q["ohlc"]["close"] > 0
                else 0
            ),
        }

    @staticmethod
    def _map_exchange(exchange: str) -> str:
        """Map our exchange names to Kite exchange constants."""
        mapping = {
            "NSE": "NSE",
            "BSE": "BSE",
            "NFO": "NFO",
            "MCX": "MCX",
            "CDS": "CDS",
            "BFO": "BFO",
        }
        return mapping.get(exchange.upper(), "NSE")


# ── Singleton ──

_broker: KiteBroker | None = None


def get_kite_broker() -> KiteBroker:
    """Get or create the global KiteBroker instance."""
    global _broker
    if _broker is None:
        _broker = KiteBroker()
    return _broker
