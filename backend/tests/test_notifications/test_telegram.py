"""Tests for the Telegram notification bot."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.notifications.telegram import TelegramBot


@pytest.fixture(autouse=True)
def no_ambient_config():
    """Pin `settings` empty for every test in this module.

    TelegramBot resolves each ctor arg as `arg or settings.<field>`, so an empty
    string does NOT mean "off" — it falls through to the environment. Without this,
    a developer with DISCORD_WEBHOOK_URL or TELEGRAM_BOT_TOKEN in their .env gets
    extra live channels and these tests fail for them while passing in CI.
    """
    with patch("app.notifications.telegram.settings") as mock_settings:
        mock_settings.telegram_bot_token = ""
        mock_settings.telegram_chat_id = ""
        mock_settings.discord_webhook_url = ""
        yield mock_settings


@pytest.fixture
def bot():
    """Telegram-only bot — Discord off, so `post` has exactly one destination."""
    return TelegramBot(token="test-token", chat_id="12345")


@pytest.fixture
def disabled_bot():
    """No channel configured at all."""
    return TelegramBot(token="", chat_id="")


def _mock_client():
    """An AsyncMock http client that `_get_client` will actually hand back.

    `is_closed` must be set explicitly: on a bare AsyncMock it is a truthy Mock,
    so `_get_client` would decide the client is closed and silently replace it
    with a real httpx.AsyncClient that makes live network calls.
    """
    client = AsyncMock()
    client.is_closed = False
    return client


class TestTelegramBot:
    def test_enabled(self, bot):
        assert bot.enabled is True

    def test_disabled_when_no_token(self, disabled_bot):
        assert disabled_bot.enabled is False
        assert disabled_bot.telegram_enabled is False
        assert disabled_bot.discord_enabled is False

    def test_discord_only_is_enabled(self):
        """A Discord webhook alone is enough — Telegram is optional."""
        b = TelegramBot(discord_webhook="https://discord.com/api/webhooks/1/x")
        assert b.enabled is True
        assert b.telegram_enabled is False
        assert b.discord_enabled is True

    def test_placeholder_token_is_not_enabled(self):
        """An unfilled `.env.example` placeholder must not count as configured."""
        b = TelegramBot(token="your_bot_token", chat_id="123")
        assert b.telegram_enabled is False

    def test_base_url(self, bot):
        assert bot.base_url == "https://api.telegram.org/bottest-token"

    @pytest.mark.asyncio
    async def test_send_message_disabled(self, disabled_bot):
        result = await disabled_bot.send_message("test")
        assert result is None

    @pytest.mark.asyncio
    async def test_send_message_success(self, bot):
        mock_client = _mock_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": {}}
        mock_client.post.return_value = mock_response
        bot._client = mock_client

        await bot.send_message("Hello")

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["text"] == "Hello"
        assert call_args[1]["json"]["chat_id"] == "12345"
        assert call_args[1]["json"]["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_send_message_api_error(self, bot):
        """An API-level rejection is logged and swallowed, not raised or returned."""
        mock_client = _mock_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": False, "description": "Bad Request"}
        mock_client.post.return_value = mock_response
        bot._client = mock_client

        result = await bot.send_message("Hello")
        # Multi-channel fan-out: send_message always returns None.
        assert result is None
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_network_error(self, bot):
        mock_client = _mock_client()
        mock_client.post.side_effect = Exception("Connection error")
        bot._client = mock_client

        result = await bot.send_message("Hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_notify_signal(self, bot):
        bot.send_message = AsyncMock()

        await bot.notify_signal(
            symbol="RELIANCE",
            action="BUY",
            confidence=0.85,
            agent_name="Technical Agent",
            entry_price=2450.50,
            stop_loss=2380.00,
            take_profit=2550.00,
            reasoning="RSI oversold, MACD bullish crossover",
        )

        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][0]
        assert "RELIANCE" in text
        assert "BUY" in text
        assert "85%" in text
        assert "Technical Agent" in text

    @pytest.mark.asyncio
    async def test_notify_trade_approved(self, bot):
        bot.send_message = AsyncMock()

        await bot.notify_trade_approved(
            symbol="TCS",
            action="SELL",
            quantity=10,
            price=3500.00,
            stop_loss=3600.00,
            agent_name="Sentiment Agent",
        )

        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][0]
        assert "Trade Approved" in text
        assert "SELL" in text
        assert "TCS" in text

    @pytest.mark.asyncio
    async def test_notify_trade_rejected(self, bot):
        bot.send_message = AsyncMock()

        await bot.notify_trade_rejected(
            symbol="INFY",
            action="BUY",
            agent_name="Technical Agent",
            reasons=["Circuit breaker active", "Daily loss limit exceeded"],
        )

        text = bot.send_message.call_args[0][0]
        assert "Rejected" in text
        assert "Circuit breaker" in text
        assert "Daily loss" in text

    @pytest.mark.asyncio
    async def test_notify_trade_executed(self, bot):
        bot.send_message = AsyncMock()

        await bot.notify_trade_executed(
            symbol="RELIANCE",
            action="BUY",
            quantity=5,
            fill_price=2452.30,
            pnl=1250.00,
            is_paper=True,
        )

        text = bot.send_message.call_args[0][0]
        assert "PAPER" in text
        assert "Filled" in text
        assert "RELIANCE" in text

    @pytest.mark.asyncio
    async def test_notify_trade_executed_live(self, bot):
        bot.send_message = AsyncMock()

        await bot.notify_trade_executed(
            symbol="TCS",
            action="SELL",
            quantity=3,
            fill_price=3510.00,
            pnl=None,
            is_paper=False,
        )

        text = bot.send_message.call_args[0][0]
        assert "LIVE" in text

    @pytest.mark.asyncio
    async def test_notify_circuit_breaker(self, bot):
        bot.send_message = AsyncMock()

        await bot.notify_circuit_breaker(
            drawdown_pct=15.3,
            equity_peak=200000,
            current_equity=169400,
        )

        text = bot.send_message.call_args[0][0]
        assert "CIRCUIT BREAKER" in text
        assert "15.3%" in text

    @pytest.mark.asyncio
    async def test_notify_circuit_breaker_reset(self, bot):
        bot.send_message = AsyncMock()

        await bot.notify_circuit_breaker_reset(drawdown_pct=6.5)

        text = bot.send_message.call_args[0][0]
        assert "Reset" in text
        assert "6.5%" in text

    @pytest.mark.asyncio
    async def test_send_daily_summary(self, bot):
        bot.send_message = AsyncMock()

        await bot.send_daily_summary(
            total_pnl=12500,
            trades_count=15,
            wins=10,
            losses=5,
            active_agents=3,
            top_gainer={"symbol": "RELIANCE", "pnl": 5000},
            top_loser={"symbol": "TCS", "pnl": -1200},
            open_positions=4,
        )

        text = bot.send_message.call_args[0][0]
        assert "Daily Report" in text
        assert "15" in text
        assert "10W" in text
        assert "RELIANCE" in text
        assert "TCS" in text

    @pytest.mark.asyncio
    async def test_send_daily_summary_no_trades(self, bot):
        bot.send_message = AsyncMock()

        await bot.send_daily_summary(
            total_pnl=0,
            trades_count=0,
            wins=0,
            losses=0,
            active_agents=2,
        )

        text = bot.send_message.call_args[0][0]
        assert "0%" in text  # win rate

    @pytest.mark.asyncio
    async def test_notify_agent_started(self, bot):
        bot.send_message = AsyncMock()

        await bot.notify_agent_started("Alpha Bot", "technical")

        text = bot.send_message.call_args[0][0]
        assert "Alpha Bot" in text
        assert "technical" in text

    @pytest.mark.asyncio
    async def test_notify_daily_loss_warning(self, bot):
        bot.send_message = AsyncMock()

        await bot.notify_daily_loss_warning(
            daily_loss=-4500,
            daily_limit=6000,
            pct_used=75,
        )

        text = bot.send_message.call_args[0][0]
        assert "Warning" in text
        assert "75%" in text

    @pytest.mark.asyncio
    async def test_custom_chat_id(self, bot):
        bot.send_message = AsyncMock()

        await bot.notify_signal(
            symbol="HDFC",
            action="BUY",
            confidence=0.7,
            agent_name="Test",
            entry_price=1500,
            stop_loss=1450,
            take_profit=1600,
        )

        # Default chat_id used
        bot.send_message.assert_called_once()

    def test_stop_polling(self, bot):
        bot._polling = True
        bot.stop_polling()
        assert bot._polling is False


class TestDiscordChannel:
    @pytest.mark.asyncio
    async def test_discord_receives_converted_markdown(self):
        """HTML alerts are converted to Discord markdown before posting."""
        b = TelegramBot(discord_webhook="https://discord.com/api/webhooks/1/x")
        mock_client = _mock_client()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_client.post.return_value = mock_response
        b._client = mock_client

        await b.send_message("<b>BUY</b> RELIANCE")

        mock_client.post.assert_called_once()
        assert mock_client.post.call_args[1]["json"]["content"] == "**BUY** RELIANCE"

    @pytest.mark.asyncio
    async def test_both_channels_receive_the_alert(self):
        b = TelegramBot(
            token="test-token",
            chat_id="12345",
            discord_webhook="https://discord.com/api/webhooks/1/x",
        )
        mock_client = _mock_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.status_code = 204
        mock_client.post.return_value = mock_response
        b._client = mock_client

        await b.send_message("Hello")

        assert mock_client.post.call_count == 2
