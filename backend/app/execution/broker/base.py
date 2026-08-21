"""Abstract broker interface."""

from abc import ABC, abstractmethod
from decimal import Decimal


class BaseBroker(ABC):
    """All broker adapters (paper, Zerodha, etc.) implement this interface."""

    @abstractmethod
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
        """Place an order. Returns dict with at least {'order_id': str, 'status': str}."""

    @abstractmethod
    async def get_order_status(self, order_id: str) -> dict:
        """Get current order status. Returns dict with at least {'status': str}."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if successful."""

    @abstractmethod
    async def get_positions(self) -> list[dict]:
        """Get all current positions."""

    @abstractmethod
    async def get_holdings(self) -> list[dict]:
        """Get all holdings (delivery positions)."""
