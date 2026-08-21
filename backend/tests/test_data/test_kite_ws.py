"""Tests for the Kite WebSocket ticker manager."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.feeds.kite_ws import KiteTickerManager


@pytest.fixture
def manager():
    return KiteTickerManager()


@pytest.fixture
def manager_with_cache():
    cache = AsyncMock()
    return KiteTickerManager(cache=cache)


class TestKiteTickerInit:
    def test_initial_state(self, manager):
        assert manager.connected is False
        assert manager._ticker is None
        assert len(manager._subscribed_tokens) == 0

    def test_init_with_cache(self, manager_with_cache):
        assert manager_with_cache._cache is not None


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_without_credentials(self, manager):
        with patch("app.data.feeds.kite_ws.settings") as mock_settings:
            mock_settings.kite_api_key = ""
            mock_settings.kite_access_token = ""
            await manager.connect()
            assert manager._ticker is None

    @pytest.mark.asyncio
    async def test_connect_creates_ticker(self, manager):
        with patch("app.data.feeds.kite_ws.KiteTicker") as MockTicker:
            mock_ticker = MagicMock()
            MockTicker.return_value = mock_ticker

            with patch("app.data.feeds.kite_ws.Thread") as MockThread:
                mock_thread = MagicMock()
                MockThread.return_value = mock_thread

                await manager.connect(api_key="key", access_token="token")

                MockTicker.assert_called_once_with("key", "token")
                mock_thread.start.assert_called_once()
                assert manager._ticker is mock_ticker


class TestSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_without_ticker(self, manager):
        """Should silently return if not connected."""
        await manager.subscribe([738561])
        assert len(manager._subscribed_tokens) == 0

    @pytest.mark.asyncio
    async def test_subscribe_tokens(self, manager):
        manager._ticker = MagicMock()

        await manager.subscribe([738561, 408065], mode="full")
        assert 738561 in manager._subscribed_tokens
        assert 408065 in manager._subscribed_tokens
        manager._ticker.subscribe.assert_called_once_with([738561, 408065])

    @pytest.mark.asyncio
    async def test_subscribe_deduplicates(self, manager):
        manager._ticker = MagicMock()

        await manager.subscribe([738561])
        await manager.subscribe([738561, 408065])

        # Second call should only subscribe the new token
        second_call_args = manager._ticker.subscribe.call_args_list[1][0][0]
        assert second_call_args == [408065]

    @pytest.mark.asyncio
    async def test_subscribe_modes(self, manager):
        manager._ticker = MagicMock()

        await manager.subscribe([100], mode="ltp")
        manager._ticker.set_mode.assert_called_with(manager._ticker.MODE_LTP, [100])

        await manager.subscribe([200], mode="quote")
        manager._ticker.set_mode.assert_called_with(manager._ticker.MODE_QUOTE, [200])


class TestUnsubscribe:
    @pytest.mark.asyncio
    async def test_unsubscribe_tokens(self, manager):
        manager._ticker = MagicMock()
        manager._subscribed_tokens = {738561, 408065}

        await manager.unsubscribe([738561])
        assert 738561 not in manager._subscribed_tokens
        assert 408065 in manager._subscribed_tokens

    @pytest.mark.asyncio
    async def test_unsubscribe_without_ticker(self, manager):
        await manager.unsubscribe([738561])  # Should not raise


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect(self, manager):
        manager._ticker = MagicMock()
        manager._connected = True

        await manager.disconnect()
        manager._ticker.close.assert_called_once()
        assert manager.connected is False


class TestOnTicks:
    def test_on_ticks_parses_data(self, manager_with_cache):
        manager_with_cache._loop = asyncio.new_event_loop()

        raw_ticks = [
            {
                "instrument_token": 738561,
                "tradingsymbol": "RELIANCE",
                "last_price": 2550.0,
                "ohlc": {"open": 2520.0, "high": 2560.0, "low": 2510.0, "close": 2530.0},
                "volume_traded": 5000000,
                "change": 0.79,
                "exchange_timestamp": "2025-01-15 10:30:00",
                "depth": {"buy": [], "sell": []},
            }
        ]

        with patch("asyncio.run_coroutine_threadsafe") as mock_run:
            manager_with_cache._on_ticks(None, raw_ticks)
            # Should be called for cache push
            assert mock_run.call_count >= 1

        manager_with_cache._loop.close()

    def test_on_ticks_empty(self, manager):
        """Should not crash on empty ticks."""
        manager._on_ticks(None, [])

    def test_on_ticks_calls_callback(self, manager):
        manager._loop = asyncio.new_event_loop()
        manager._tick_callback = AsyncMock()

        raw_ticks = [
            {
                "instrument_token": 738561,
                "last_price": 2550.0,
                "ohlc": {"open": 2520.0, "high": 2560.0, "low": 2510.0, "close": 2530.0},
                "volume_traded": 5000000,
                "change": 0.79,
                "depth": {"buy": [], "sell": []},
            }
        ]

        with patch("asyncio.run_coroutine_threadsafe") as mock_run:
            manager._on_ticks(None, raw_ticks)
            assert mock_run.call_count >= 1

        manager._loop.close()


class TestCallbacks:
    def test_on_connect(self, manager):
        manager._on_connect(None, {})
        assert manager.connected is True

    def test_on_connect_resubscribes(self, manager):
        ws = MagicMock()
        manager._subscribed_tokens = {738561, 408065}

        manager._on_connect(ws, {})
        ws.subscribe.assert_called_once()
        ws.set_mode.assert_called_once()

    def test_on_close(self, manager):
        manager._connected = True
        manager._on_close(None, 1000, "Normal closure")
        assert manager.connected is False

    def test_on_error(self, manager):
        # Should not raise
        manager._on_error(None, 500, "Server error")

    def test_on_reconnect(self, manager):
        # Should not raise
        manager._on_reconnect(None, 3)


class TestTickCallback:
    def test_set_tick_callback(self, manager):
        callback = AsyncMock()
        manager.set_tick_callback(callback)
        assert manager._tick_callback is callback


class TestPushTicksToCache:
    @pytest.mark.asyncio
    async def test_push_ticks(self, manager_with_cache):
        ticks = [
            {
                "symbol": "RELIANCE",
                "ltp": 2550.0,
                "open": 2520.0,
                "high": 2560.0,
                "low": 2510.0,
                "close": 2530.0,
                "volume": 5000000,
                "change": 0.79,
            }
        ]

        await manager_with_cache._push_ticks_to_cache(ticks)
        manager_with_cache._cache.set_bulk_quotes.assert_called_once()

    @pytest.mark.asyncio
    async def test_push_empty_ticks(self, manager_with_cache):
        await manager_with_cache._push_ticks_to_cache([])
        manager_with_cache._cache.set_bulk_quotes.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_without_cache(self, manager):
        # Should not crash
        await manager._push_ticks_to_cache([{"symbol": "REL", "ltp": 100}])
