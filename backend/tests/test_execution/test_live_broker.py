"""Tests for the LiveBroker wrapper around KiteBroker."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.execution.live import LiveBroker


@pytest.fixture
def mock_kite_broker():
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value={"order_id": "220001", "status": "filled", "fill_price": 2500.0})
    broker.get_order_status = AsyncMock(return_value={"order_id": "220001", "status": "filled"})
    broker.cancel_order = AsyncMock(return_value=True)
    broker.get_positions = AsyncMock(return_value=[{"symbol": "RELIANCE", "quantity": 10}])
    broker.get_holdings = AsyncMock(return_value=[{"symbol": "INFY", "quantity": 50}])
    broker.set_access_token = MagicMock()
    return broker


@pytest.fixture
def live_broker(mock_kite_broker):
    return LiveBroker(kite_broker=mock_kite_broker)


class TestLiveBroker:
    @pytest.mark.asyncio
    async def test_place_order_delegates(self, live_broker, mock_kite_broker):
        result = await live_broker.place_order(
            symbol="RELIANCE",
            exchange="NSE",
            side="BUY",
            quantity=10,
            price=Decimal("2500"),
            order_type="MARKET",
            product="CNC",
        )
        assert result["order_id"] == "220001"
        mock_kite_broker.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_order_status_delegates(self, live_broker, mock_kite_broker):
        result = await live_broker.get_order_status("220001")
        assert result["status"] == "filled"
        mock_kite_broker.get_order_status.assert_called_once_with("220001")

    @pytest.mark.asyncio
    async def test_cancel_order_delegates(self, live_broker, mock_kite_broker):
        result = await live_broker.cancel_order("220001")
        assert result is True
        mock_kite_broker.cancel_order.assert_called_once_with("220001")

    @pytest.mark.asyncio
    async def test_get_positions_delegates(self, live_broker, mock_kite_broker):
        result = await live_broker.get_positions()
        assert len(result) == 1
        mock_kite_broker.get_positions.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_holdings_delegates(self, live_broker, mock_kite_broker):
        result = await live_broker.get_holdings()
        assert len(result) == 1
        mock_kite_broker.get_holdings.assert_called_once()

    def test_set_access_token(self, live_broker, mock_kite_broker):
        live_broker.set_access_token("new_token")
        mock_kite_broker.set_access_token.assert_called_once_with("new_token")

    def test_kite_property(self, live_broker, mock_kite_broker):
        assert live_broker.kite is mock_kite_broker
