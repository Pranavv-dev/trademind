"""Notifier for TradeMind — trade alerts, daily summaries, interactive commands.

Despite the module/class name (kept for import stability), this is a multi-channel
notifier. It sends to Telegram and/or Discord depending on what's configured in
settings. Discord is the supported channel where Telegram is unavailable; it's
send-only (no inbound command polling). All alert methods funnel through
send_message(), so configuring a Discord webhook routes every alert to Discord.
"""

import asyncio
import re
from datetime import datetime, timezone

import httpx
import structlog

from app.config import settings

log = structlog.get_logger()

TELEGRAM_API = "https://api.telegram.org/bot{token}"
DISCORD_MAX_CONTENT = 2000


def _html_to_discord(text: str) -> str:
    """Convert the Telegram-flavored HTML alerts into Discord markdown."""
    t = text
    # <a href="URL">LABEL</a> -> "LABEL: URL" (Discord plain content doesn't render <a>)
    t = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"\2: \1", t)
    t = t.replace("<b>", "**").replace("</b>", "**")
    t = t.replace("<i>", "_").replace("</i>", "_")
    t = t.replace("<code>", "`").replace("</code>", "`")
    t = re.sub(r"<[^>]+>", "", t)  # strip any remaining tags
    return t[:DISCORD_MAX_CONTENT]


class TelegramBot:
    """Async Telegram bot for sending notifications and handling commands."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        discord_webhook: str | None = None,
    ):
        self.token = token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id
        self.discord_webhook = discord_webhook or settings.discord_webhook_url
        self._client: httpx.AsyncClient | None = None
        self._polling = False

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.token and self.chat_id and not self.token.startswith("your_"))

    @property
    def discord_enabled(self) -> bool:
        return bool(
            self.discord_webhook
            and self.discord_webhook.startswith("https://")
            and not self.discord_webhook.startswith("your_")
        )

    @property
    def enabled(self) -> bool:
        """True if ANY channel is configured."""
        return self.telegram_enabled or self.discord_enabled

    @property
    def base_url(self) -> str:
        return TELEGRAM_API.format(token=self.token)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Core send ──

    async def send_message(
        self,
        text: str,
        chat_id: str | None = None,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> dict | None:
        """Send an alert to all configured channels (Telegram and/or Discord)."""
        if not self.enabled:
            log.debug("notify_disabled", reason="no_channel_configured")
            return None

        client = await self._get_client()

        # Telegram
        if self.telegram_enabled:
            try:
                resp = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": chat_id or self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                        "disable_notification": disable_notification,
                    },
                )
                data = resp.json()
                if not data.get("ok"):
                    log.error("telegram_send_error", error=data.get("description"))
            except Exception:
                log.exception("telegram_send_failed")

        # Discord
        if self.discord_enabled:
            await self._send_discord(_html_to_discord(text), client)

        return None

    async def _send_discord(self, content: str, client: httpx.AsyncClient) -> None:
        """Post a message to the Discord webhook."""
        try:
            resp = await client.post(
                self.discord_webhook,
                json={"content": content or "(empty)"},
            )
            # Discord returns 204 No Content on success
            if resp.status_code not in (200, 204):
                log.error("discord_send_error", status=resp.status_code, body=resp.text[:200])
        except Exception:
            log.exception("discord_send_failed")

    # ── Trade Notifications ──

    async def notify_signal(
        self,
        symbol: str,
        action: str,
        confidence: float,
        agent_name: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        reasoning: str = "",
    ) -> None:
        """Send a new signal alert."""
        emoji = "\U0001f7e2" if action == "BUY" else "\U0001f534"  # green/red circle
        text = (
            f"{emoji} <b>Signal: {action} {symbol}</b>\n"
            f"\n"
            f"Agent: {agent_name}\n"
            f"Confidence: {confidence:.0%}\n"
            f"Entry: \u20b9{entry_price:,.2f}\n"
            f"SL: \u20b9{stop_loss:,.2f}\n"
            f"TP: \u20b9{take_profit:,.2f}\n"
        )
        if reasoning:
            # Truncate reasoning for Telegram
            short = reasoning[:300].rsplit("\n", 1)[0] if len(reasoning) > 300 else reasoning
            text += f"\n<i>{short}</i>"

        await self.send_message(text)

    async def notify_trade_approved(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float,
        stop_loss: float,
        agent_name: str,
    ) -> None:
        """Notify that a trade passed risk checks and will be executed."""
        emoji = "\U0001f7e2" if action == "BUY" else "\U0001f534"
        text = (
            f"{emoji} <b>Trade Approved: {action} {symbol}</b>\n"
            f"\n"
            f"Qty: {quantity}\n"
            f"Price: \u20b9{price:,.2f}\n"
            f"SL: \u20b9{stop_loss:,.2f}\n"
            f"Agent: {agent_name}\n"
        )
        await self.send_message(text)

    async def notify_trade_rejected(
        self,
        symbol: str,
        action: str,
        agent_name: str,
        reasons: list[str],
    ) -> None:
        """Notify that a trade was rejected by risk checks."""
        text = f"\u26d4 <b>Trade Rejected: {action} {symbol}</b>\n\nAgent: {agent_name}\nReasons:\n"
        for reason in reasons:
            text += f"  \u2022 {reason}\n"

        await self.send_message(text, disable_notification=True)

    async def notify_trade_executed(
        self,
        symbol: str,
        action: str,
        quantity: int,
        fill_price: float,
        pnl: float | None,
        is_paper: bool,
    ) -> None:
        """Notify that an order was filled."""
        mode = "[PAPER]" if is_paper else "[LIVE]"
        emoji = "\u2705"
        text = (
            f"{emoji} <b>Order Filled {mode}: {action} {symbol}</b>\n"
            f"\n"
            f"Qty: {quantity}\n"
            f"Fill: \u20b9{fill_price:,.2f}\n"
        )
        if pnl is not None:
            pnl_emoji = "\U0001f4b0" if pnl >= 0 else "\U0001f4c9"
            text += f"P&L: {pnl_emoji} \u20b9{pnl:+,.2f}\n"

        await self.send_message(text)

    # ── Auth Alerts ──

    async def notify_auth_required(self, login_url: str) -> None:
        """Alert that Kite re-auth is needed (token expired overnight)."""
        text = (
            "\u26a0\ufe0f <b>TradeMind: Kite Re-Auth Required</b>\n"
            "\n"
            "Your Zerodha access token expired overnight.\n"
            "<b>No trades will execute until you log in.</b>\n"
            "\n"
            f'<a href="{login_url}">Click here to login to Kite</a>\n'
            "\n"
            "<i>Tokens expire every day at midnight — this alert fires every morning.</i>"
        )
        await self.send_message(text)

    async def notify_system_ready(self) -> None:
        """Confirm system is authenticated and ready to trade.

        Computes the real time-to-open in IST so the message is honest whenever it
        fires (8:00 auto-auth, 8:30 pre-market, or a manual test at any hour).
        """
        import zoneinfo
        from datetime import timedelta

        ist = zoneinfo.ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        open_t = now.replace(hour=9, minute=15, second=0, microsecond=0)
        close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)

        if open_t <= now <= close_t:
            status = "Market is OPEN \u2014 scanning live."
        else:
            nxt = open_t if now < open_t else (open_t + timedelta(days=1))
            mins = int((nxt - now).total_seconds() // 60)
            hrs, m = divmod(mins, 60)
            when = f"{hrs}h {m}m" if hrs else f"{m}m"
            status = f"Market opens in ~{when} (09:15 IST)."

        text = f"\u2705 <b>TradeMind: System Ready</b>\nKite auth valid. {status}"
        await self.send_message(text, disable_notification=True)

    # ── Risk Alerts ──

    async def notify_circuit_breaker(
        self,
        drawdown_pct: float,
        equity_peak: float,
        current_equity: float,
    ) -> None:
        """CRITICAL: Circuit breaker triggered — all trading halted."""
        text = (
            f"\U0001f6a8\U0001f6a8 <b>CIRCUIT BREAKER TRIGGERED</b> \U0001f6a8\U0001f6a8\n"
            f"\n"
            f"All trading has been halted.\n"
            f"\n"
            f"Drawdown: {drawdown_pct:.1f}%\n"
            f"Peak Equity: \u20b9{equity_peak:,.0f}\n"
            f"Current: \u20b9{current_equity:,.0f}\n"
            f"\n"
            f"<i>Manual intervention required.</i>"
        )
        await self.send_message(text)

    async def notify_circuit_breaker_reset(self, drawdown_pct: float) -> None:
        """Circuit breaker has been reset — trading resumed."""
        text = (
            f"\u2705 <b>Circuit Breaker Reset</b>\n"
            f"\n"
            f"Trading has resumed.\n"
            f"Current drawdown: {drawdown_pct:.1f}%"
        )
        await self.send_message(text)

    async def notify_daily_loss_warning(
        self,
        daily_loss: float,
        daily_limit: float,
        pct_used: float,
    ) -> None:
        """Warn when approaching daily loss limit."""
        text = (
            f"\u26a0\ufe0f <b>Daily Loss Warning</b>\n"
            f"\n"
            f"Loss: \u20b9{abs(daily_loss):,.0f} / \u20b9{daily_limit:,.0f} ({pct_used:.0f}%)\n"
            f"\n"
            f"<i>Risk limits may halt trading soon.</i>"
        )
        await self.send_message(text)

    # ── Daily Summary ──

    async def send_daily_summary(
        self,
        total_pnl: float,
        trades_count: int,
        wins: int,
        losses: int,
        active_agents: int,
        top_gainer: dict | None = None,
        top_loser: dict | None = None,
        open_positions: int = 0,
    ) -> None:
        """End-of-day summary report."""
        pnl_emoji = "\U0001f4b0" if total_pnl >= 0 else "\U0001f4c9"
        win_rate = (wins / trades_count * 100) if trades_count > 0 else 0

        text = (
            f"\U0001f4ca <b>Daily Report — {datetime.now(timezone.utc).strftime('%d %b %Y')}</b>\n"
            f"\n"
            f"P&L: {pnl_emoji} \u20b9{total_pnl:+,.0f}\n"
            f"Trades: {trades_count} ({wins}W / {losses}L)\n"
            f"Win Rate: {win_rate:.0f}%\n"
            f"Active Agents: {active_agents}\n"
            f"Open Positions: {open_positions}\n"
        )

        if top_gainer:
            text += f"\nTop Gainer: {top_gainer['symbol']} \u20b9{top_gainer['pnl']:+,.0f}"
        if top_loser:
            text += f"\nTop Loser: {top_loser['symbol']} \u20b9{top_loser['pnl']:+,.0f}"

        await self.send_message(text)

    # ── Agent Status ──

    async def notify_agent_started(self, name: str, strategy: str) -> None:
        text = f"\u25b6\ufe0f Agent started: <b>{name}</b> ({strategy})"
        await self.send_message(text, disable_notification=True)

    async def notify_agent_stopped(self, name: str, reason: str = "manual") -> None:
        text = f"\u23f8\ufe0f Agent stopped: <b>{name}</b> (reason: {reason})"
        await self.send_message(text, disable_notification=True)

    async def notify_agent_error(self, name: str, error: str) -> None:
        text = f"\u274c <b>Agent Error: {name}</b>\n\n<code>{error[:500]}</code>"
        await self.send_message(text)

    # ── Command Polling ──

    async def start_polling(self, command_handler=None) -> None:
        """Long-poll for incoming Telegram commands. Runs in background.

        Telegram-only — Discord webhooks are send-only, so this no-ops when only
        Discord is configured.
        """
        if not self.telegram_enabled:
            return

        self._polling = True
        offset = 0
        client = await self._get_client()
        log.info("telegram_polling_started")

        while self._polling:
            try:
                resp = await client.get(
                    f"{self.base_url}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                    timeout=35,
                )
                data = resp.json()
                if not data.get("ok"):
                    await asyncio.sleep(5)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    text = message.get("text", "")
                    chat_id = str(message.get("chat", {}).get("id", ""))

                    if text.startswith("/") and command_handler:
                        await command_handler(text.strip(), chat_id)

            except httpx.ReadTimeout:
                continue
            except Exception:
                log.exception("telegram_poll_error")
                await asyncio.sleep(5)

    def stop_polling(self) -> None:
        self._polling = False


# ── Singleton ──

_bot: TelegramBot | None = None


def get_bot() -> TelegramBot:
    """Get or create the global TelegramBot instance."""
    global _bot
    if _bot is None:
        _bot = TelegramBot()
    return _bot
