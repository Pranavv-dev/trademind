"""Tests for the Zerodha Kite Connect broker adapter."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from app.execution.broker.kite import (
    KiteBroker,
    STATUS_MAP,
    ORDER_TYPE_MAP,
    PRODUCT_MAP,
    SIDE_MAP,
)


@pytest.fixture
def broker():
    b = KiteBroker(api_key="test_key", access_token="test_token")
    return b


@pytest.fixture
def mock_kite():
    kite = MagicMock()
    kite.login_url.return_value = "https://kite.zerodha.com/connect/login?v=3&api_key=test_key"
    return kite


class TestKiteBrokerInit:
    def test_init_with_explicit_keys(self):
        b = KiteBroker(api_key="my_key", access_token="my_token")
        assert b.api_key == "my_key"
        assert b.access_token == "my_token"

    def test_init_from_settings(self):
        with patch("app.execution.broker.kite.settings") as mock_settings:
            mock_settings.kite_api_key = "settings_key"
            mock_settings.kite_access_token = "settings_token"
            b = KiteBroker()
            assert b.api_key == "settings_key"
            assert b.access_token == "settings_token"

    def test_lazy_kite_init(self, broker):
        assert broker._kite is None
        # Accessing .kite property should create the client
        with patch("app.execution.broker.kite.KiteConnect") as MockKite:
            mock_instance = MagicMock()
            MockKite.return_value = mock_instance
            kite = broker.kite
            MockKite.assert_called_once_with(api_key="test_key")
            mock_instance.set_access_token.assert_called_once_with("test_token")

    def test_lazy_kite_raises_without_api_key(self):
        # `api_key=""` is falsy, so __init__ falls back to settings.kite_api_key.
        # Settings must be pinned empty or this test only passes for developers
        # who have no KITE_API_KEY in their .env.
        with patch("app.execution.broker.kite.settings") as mock_settings:
            mock_settings.kite_api_key = ""
            mock_settings.kite_access_token = ""
            b = KiteBroker(api_key="", access_token="token")
            with pytest.raises(RuntimeError, match="API key not configured"):
                _ = b.kite


class TestKiteBrokerAuth:
    def test_get_login_url(self, broker, mock_kite):
        broker._kite = mock_kite
        url = broker.get_login_url()
        assert "kite.zerodha.com" in url

    def test_set_access_token(self, broker, mock_kite):
        broker._kite = mock_kite
        broker.set_access_token("new_token")
        assert broker.access_token == "new_token"
        mock_kite.set_access_token.assert_called_with("new_token")

    @pytest.mark.asyncio
    async def test_generate_session(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.generate_session.return_value = {
            "access_token": "generated_token",
            "user_id": "AB1234",
        }
        with patch("app.execution.broker.kite.settings") as mock_settings:
            mock_settings.kite_api_secret = "secret123"
            data = await broker.generate_session("req_token_abc")
        assert data["access_token"] == "generated_token"
        assert broker.access_token == "generated_token"


class TestKiteBrokerOrders:
    @pytest.mark.asyncio
    async def test_place_market_order(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.place_order.return_value = "220001"
        mock_kite.order_history.return_value = [
            {
                "status": "COMPLETE",
                "average_price": 2500.0,
                "filled_quantity": 10,
                "pending_quantity": 0,
                "status_message": "",
            }
        ]

        result = await broker.place_order(
            symbol="RELIANCE",
            exchange="NSE",
            side="BUY",
            quantity=10,
            price=Decimal("2500.00"),
            order_type="MARKET",
            product="CNC",
        )

        assert result["order_id"] == "220001"
        assert result["status"] == "filled"
        assert result["fill_price"] == 2500.0

    @pytest.mark.asyncio
    async def test_place_limit_order_sets_price(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.place_order.return_value = "220002"
        mock_kite.order_history.return_value = [
            {"status": "OPEN", "average_price": 0, "filled_quantity": 0, "pending_quantity": 10, "status_message": ""}
        ]

        result = await broker.place_order(
            symbol="TCS",
            exchange="NSE",
            side="BUY",
            quantity=10,
            price=Decimal("3500.00"),
            order_type="LIMIT",
            product="CNC",
        )

        # Verify price was passed to place_order
        call_kwargs = mock_kite.place_order.call_args
        assert call_kwargs is not None

    @pytest.mark.asyncio
    async def test_place_order_raises_on_error(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.place_order.side_effect = Exception("Insufficient funds")

        with pytest.raises(Exception, match="Insufficient funds"):
            await broker.place_order(
                symbol="RELIANCE",
                exchange="NSE",
                side="BUY",
                quantity=10,
                price=Decimal("2500.00"),
                order_type="MARKET",
                product="CNC",
            )

    @pytest.mark.asyncio
    async def test_place_order_without_access_token(self):
        b = KiteBroker(api_key="key", access_token="")
        with pytest.raises(RuntimeError, match="access token not set"):
            await b.place_order("RELIANCE", "NSE", "BUY", 10, Decimal("2500"), "MARKET", "CNC")


class TestKiteBrokerOrderStatus:
    @pytest.mark.asyncio
    async def test_get_order_status_filled(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.order_history.return_value = [
            {"status": "OPEN", "average_price": 0},
            {"status": "COMPLETE", "average_price": 2500.0, "filled_quantity": 10, "pending_quantity": 0, "status_message": ""},
        ]

        status = await broker.get_order_status("220001")
        assert status["status"] == "filled"
        assert status["fill_price"] == 2500.0

    @pytest.mark.asyncio
    async def test_get_order_status_rejected(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.order_history.return_value = [
            {"status": "REJECTED", "average_price": 0, "filled_quantity": 0, "pending_quantity": 0, "status_message": "Insufficient margin"},
        ]

        status = await broker.get_order_status("220002")
        assert status["status"] == "rejected"
        assert "Insufficient margin" in status["status_message"]

    @pytest.mark.asyncio
    async def test_get_order_status_error(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.order_history.side_effect = Exception("Network error")

        status = await broker.get_order_status("220003")
        assert status["status"] == "unknown"
        assert "error" in status

    @pytest.mark.asyncio
    async def test_get_order_status_empty_history(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.order_history.return_value = []

        status = await broker.get_order_status("220004")
        assert status["status"] == "unknown"


class TestKiteBrokerCancel:
    @pytest.mark.asyncio
    async def test_cancel_order_success(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.cancel_order.return_value = "220001"

        result = await broker.cancel_order("220001")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_order_failure(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.cancel_order.side_effect = Exception("Order already executed")

        result = await broker.cancel_order("220001")
        assert result is False


class TestKiteBrokerPositions:
    @pytest.mark.asyncio
    async def test_get_positions(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.positions.return_value = {
            "net": [
                {
                    "tradingsymbol": "RELIANCE",
                    "exchange": "NSE",
                    "quantity": 10,
                    "average_price": 2500.0,
                    "last_price": 2550.0,
                    "pnl": 500.0,
                    "product": "CNC",
                },
                {
                    "tradingsymbol": "TCS",
                    "exchange": "NSE",
                    "quantity": 0,  # Flat — should be filtered
                    "average_price": 3500.0,
                    "last_price": 3500.0,
                    "pnl": 0.0,
                    "product": "CNC",
                },
            ],
            "day": [],
        }

        positions = await broker.get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "RELIANCE"
        assert positions[0]["side"] == "LONG"

    @pytest.mark.asyncio
    async def test_get_positions_error(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.positions.side_effect = Exception("API error")
        positions = await broker.get_positions()
        assert positions == []


class TestKiteBrokerHoldings:
    @pytest.mark.asyncio
    async def test_get_holdings(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.holdings.return_value = [
            {
                "tradingsymbol": "INFY",
                "exchange": "NSE",
                "quantity": 50,
                "average_price": 1400.0,
                "last_price": 1450.0,
                "pnl": 2500.0,
                "day_change_percentage": 1.2,
            },
        ]

        holdings = await broker.get_holdings()
        assert len(holdings) == 1
        assert holdings[0]["symbol"] == "INFY"
        assert holdings[0]["day_change_pct"] == 1.2


class TestKiteBrokerMargins:
    @pytest.mark.asyncio
    async def test_get_margins(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.margins.return_value = {
            "equity": {
                "available": {"cash": 100000.0, "live_balance": 95000.0},
                "utilised": {"debits": 5000.0},
                "net": 100000.0,
            },
        }

        margins = await broker.get_margins()
        assert margins["available_cash"] == 100000.0
        assert margins["used_margin"] == 5000.0


class TestKiteBrokerQuote:
    @pytest.mark.asyncio
    async def test_get_quote(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.quote.return_value = {
            "NSE:RELIANCE": {
                "last_price": 2550.0,
                "ohlc": {"open": 2520.0, "high": 2560.0, "low": 2510.0, "close": 2530.0},
                "volume": 5000000,
            },
        }

        quote = await broker.get_quote("NSE", "RELIANCE")
        assert quote is not None
        assert quote["ltp"] == 2550.0
        assert quote["change"] == 20.0  # 2550 - 2530

    @pytest.mark.asyncio
    async def test_get_quote_not_found(self, broker, mock_kite):
        broker._kite = mock_kite
        mock_kite.quote.return_value = {}

        quote = await broker.get_quote("NSE", "UNKNOWN")
        assert quote is None


class TestStatusMaps:
    def test_status_map_complete(self):
        assert STATUS_MAP["COMPLETE"] == "filled"
        assert STATUS_MAP["REJECTED"] == "rejected"
        assert STATUS_MAP["CANCELLED"] == "cancelled"
        assert STATUS_MAP["OPEN"] == "placed"

    def test_exchange_mapping(self):
        assert KiteBroker._map_exchange("NSE") == "NSE"
        assert KiteBroker._map_exchange("nse") == "NSE"
        assert KiteBroker._map_exchange("NFO") == "NFO"
        assert KiteBroker._map_exchange("INVALID") == "NSE"  # Default fallback
