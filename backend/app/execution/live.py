"""Live trading execution — routes orders to Zerodha Kite Connect."""

from decimal import Decimal

import structlog

from app.execution.broker.base import BaseBroker
from app.execution.broker.kite import KiteBroker, get_kite_broker

log = structlog.get_logger()


class LiveBroker(BaseBroker):
    """Thin wrapper around KiteBroker that satisfies the BaseBroker interface.

    Delegates all operations to the underlying KiteBroker singleton.
    """

    def __init__(self, kite_broker: KiteBroker | None = None):
        self._kite = kite_broker or get_kite_broker()

    @property
    def kite(self) -> KiteBroker:
        return self._kite

    def set_access_token(self, token: str) -> None:
        """Update access token after login flow."""
        self._kite.set_access_token(token)

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
        return await self._kite.place_order(
            symbol=symbol,
            exchange=exchange,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            product=product,
        )

    async def get_order_status(self, order_id: str) -> dict:
        return await self._kite.get_order_status(order_id)

    async def cancel_order(self, order_id: str) -> bool:
        return await self._kite.cancel_order(order_id)

    async def get_positions(self) -> list[dict]:
        return await self._kite.get_positions()

    async def get_holdings(self) -> list[dict]:
        return await self._kite.get_holdings()
