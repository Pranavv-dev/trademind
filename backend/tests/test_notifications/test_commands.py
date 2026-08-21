"""Tests for Telegram command handler."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.notifications.commands import handle_command
from app.notifications.telegram import TelegramBot


@pytest.fixture(autouse=True)
def no_ambient_config():
    """Keep a developer's real .env channels out of these tests. See test_telegram.py."""
    with patch("app.notifications.telegram.settings") as mock_settings:
        mock_settings.telegram_bot_token = ""
        mock_settings.telegram_chat_id = ""
        mock_settings.discord_webhook_url = ""
        yield mock_settings


@pytest.fixture
def bot():
    b = TelegramBot(token="test-token", chat_id="12345")
    b.send_message = AsyncMock()
    return b


class TestCommands:
    @pytest.mark.asyncio
    async def test_start_command(self, bot):
        await handle_command("/start", "12345", bot)

        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][0]
        assert "TradeMind" in text
        assert "/status" in text

    @pytest.mark.asyncio
    async def test_help_command(self, bot):
        await handle_command("/help", "12345", bot)

        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][0]
        assert "/status" in text

    @pytest.mark.asyncio
    async def test_unknown_command(self, bot):
        await handle_command("/foobar", "12345", bot)

        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][0]
        assert "Unknown" in text

    @pytest.mark.asyncio
    async def test_status_command(self, bot):
        with patch("app.notifications.commands.aioredis") as mock_redis:
            mock_conn = AsyncMock()
            mock_redis.from_url.return_value = mock_conn

            await handle_command("/status", "12345", bot)

            bot.send_message.assert_called_once()
            text = bot.send_message.call_args[0][0]
            assert "System Status" in text
            assert "Mode" in text

    @pytest.mark.asyncio
    async def test_pnl_command(self, bot):
        with patch("app.notifications.commands.aioredis") as mock_redis:
            mock_conn = AsyncMock()
            mock_redis.from_url.return_value = mock_conn

            mock_cache = MagicMock()
            mock_cache.get_daily_pnl = AsyncMock(return_value=5000.0)

            with patch("app.data.cache.PriceCache", return_value=mock_cache):
                await handle_command("/pnl", "12345", bot)

            bot.send_message.assert_called_once()
            text = bot.send_message.call_args[0][0]
            assert "P&L" in text

    @pytest.mark.asyncio
    async def test_risk_command(self, bot):
        with patch("app.notifications.commands.aioredis") as mock_redis:
            mock_conn = AsyncMock()
            mock_redis.from_url.return_value = mock_conn

            mock_cache = MagicMock()
            mock_cache.get_daily_pnl = AsyncMock(return_value=-2000.0)
            mock_cache.is_circuit_breaker_active = AsyncMock(return_value=False)

            with patch("app.data.cache.PriceCache", return_value=mock_cache):
                await handle_command("/risk", "12345", bot)

            bot.send_message.assert_called_once()
            text = bot.send_message.call_args[0][0]
            assert "Risk Dashboard" in text
            assert "Inactive" in text

    @pytest.mark.asyncio
    async def test_risk_command_circuit_breaker_active(self, bot):
        with patch("app.notifications.commands.aioredis") as mock_redis:
            mock_conn = AsyncMock()
            mock_redis.from_url.return_value = mock_conn

            mock_cache = MagicMock()
            mock_cache.get_daily_pnl = AsyncMock(return_value=-8000.0)
            mock_cache.is_circuit_breaker_active = AsyncMock(return_value=True)

            with patch("app.data.cache.PriceCache", return_value=mock_cache):
                await handle_command("/risk", "12345", bot)

            text = bot.send_message.call_args[0][0]
            assert "ACTIVE" in text

    @pytest.mark.asyncio
    async def test_command_with_bot_suffix(self, bot):
        """Commands like /status@TradeMindBot should work."""
        with patch("app.notifications.commands.aioredis") as mock_redis:
            mock_conn = AsyncMock()
            mock_redis.from_url.return_value = mock_conn

            await handle_command("/status@TradeMindBot", "12345", bot)

            bot.send_message.assert_called_once()
            text = bot.send_message.call_args[0][0]
            assert "System Status" in text

    @pytest.mark.asyncio
    async def test_agents_no_active(self, bot):
        with patch("app.db.session.async_session_factory") as mock_session_factory:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_factory.return_value = mock_session

            with patch("app.db.repositories.AgentRepository") as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.get_active = AsyncMock(return_value=[])
                mock_repo_cls.return_value = mock_repo

                await handle_command("/agents", "12345", bot)

            text = bot.send_message.call_args[0][0]
            assert "No active agents" in text

    @pytest.mark.asyncio
    async def test_pause_all(self, bot):
        mock_agent = MagicMock()
        mock_agent.id = "agent-1"
        mock_agent.status = "active"

        with patch("app.db.session.async_session_factory") as mock_session_factory:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_factory.return_value = mock_session

            with patch("app.db.repositories.AgentRepository") as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.get_active = AsyncMock(return_value=[mock_agent])
                mock_repo.update_status = AsyncMock()
                mock_repo_cls.return_value = mock_repo

                await handle_command("/pause", "12345", bot)

            text = bot.send_message.call_args[0][0]
            assert "Paused 1" in text
