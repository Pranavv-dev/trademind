"""Tests for Kite Connect auth routes."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestAuthRoutes:
    @pytest.mark.asyncio
    async def test_kite_login_returns_url(self):
        with patch("app.api.routes.auth.get_kite_broker") as mock_get:
            mock_broker = MagicMock()
            mock_broker.get_login_url.return_value = "https://kite.zerodha.com/connect/login?v=3&api_key=test"
            mock_get.return_value = mock_broker

            from app.api.routes.auth import kite_login
            result = await kite_login()
            assert "login_url" in result
            assert "kite.zerodha.com" in result["login_url"]

    @pytest.mark.asyncio
    async def test_kite_status(self):
        with patch("app.api.routes.auth.get_kite_broker") as mock_broker_fn:
            mock_broker = MagicMock()
            mock_broker.access_token = "test_token"
            mock_broker_fn.return_value = mock_broker

            with patch("app.api.routes.auth.get_ticker_manager") as mock_ticker_fn:
                mock_ticker = MagicMock()
                mock_ticker.connected = True
                mock_ticker_fn.return_value = mock_ticker

                with patch("app.api.routes.auth.get_instrument_master") as mock_inst_fn:
                    mock_inst = MagicMock()
                    mock_inst.loaded = True
                    mock_inst.count = 50000
                    mock_inst_fn.return_value = mock_inst

                    from app.api.routes.auth import kite_status
                    result = await kite_status()
                    assert result["authenticated"] is True
                    assert result["ticker_connected"] is True
                    assert result["instruments_loaded"] is True
                    assert result["instruments_count"] == 50000

    @pytest.mark.asyncio
    async def test_kite_status_unauthenticated(self):
        with patch("app.api.routes.auth.get_kite_broker") as mock_broker_fn:
            mock_broker = MagicMock()
            mock_broker.access_token = ""
            mock_broker_fn.return_value = mock_broker

            with patch("app.api.routes.auth.get_ticker_manager") as mock_ticker_fn:
                mock_ticker = MagicMock()
                mock_ticker.connected = False
                mock_ticker_fn.return_value = mock_ticker

                with patch("app.api.routes.auth.get_instrument_master") as mock_inst_fn:
                    mock_inst = MagicMock()
                    mock_inst.loaded = False
                    mock_inst.count = 0
                    mock_inst_fn.return_value = mock_inst

                    from app.api.routes.auth import kite_status
                    result = await kite_status()
                    assert result["authenticated"] is False
                    assert result["ticker_connected"] is False
                    assert result["instruments_loaded"] is False

    @pytest.mark.asyncio
    async def test_kite_callback_success(self):
        with patch("app.api.routes.auth.get_kite_broker") as mock_broker_fn:
            mock_broker = MagicMock()
            mock_broker.generate_session = AsyncMock(return_value={
                "access_token": "gen_token",
                "user_id": "AB1234",
            })
            mock_broker.api_key = "test_key"
            mock_broker.kite = MagicMock()
            mock_broker_fn.return_value = mock_broker

            with patch("app.api.routes.auth.get_instrument_master") as mock_inst_fn:
                mock_inst = MagicMock()
                mock_inst.loaded = False
                mock_inst.load = AsyncMock(return_value=50000)
                mock_inst_fn.return_value = mock_inst

                with patch("app.api.routes.auth.get_ticker_manager") as mock_ticker_fn:
                    mock_ticker = MagicMock()
                    mock_ticker.connected = False
                    mock_ticker.connect = AsyncMock()
                    mock_ticker_fn.return_value = mock_ticker

                    with patch("app.api.routes.auth.settings") as mock_settings:
                        mock_settings.kite_api_secret = "secret"
                        mock_settings.kite_redirect_url = "http://localhost:8000/api/auth/kite/callback"

                        from app.api.routes.auth import kite_callback

                        # Called directly, so FastAPI never resolves Depends() —
                        # the redis client has to be injected by hand.
                        mock_redis = AsyncMock()
                        result = await kite_callback("req_token_123", redis=mock_redis)

                        mock_broker.generate_session.assert_called_once_with("req_token_123")
                        mock_inst.load.assert_called_once()
                        mock_ticker.connect.assert_called_once()
