"""Telegram command handler — interactive bot commands for TradeMind."""

import redis.asyncio as aioredis
import structlog

from app.config import settings
from app.notifications.telegram import TelegramBot

log = structlog.get_logger()


async def handle_command(text: str, chat_id: str, bot: TelegramBot) -> None:
    """Route incoming /commands to their handlers."""
    parts = text.split()
    command = parts[0].lower().split("@")[0]  # strip @botname suffix

    handlers = {
        "/start": cmd_start,
        "/help": cmd_help,
        "/status": cmd_status,
        "/agents": cmd_agents,
        "/trades": cmd_trades,
        "/risk": cmd_risk,
        "/pnl": cmd_pnl,
        "/pause": cmd_pause_all,
    }

    handler = handlers.get(command, cmd_unknown)
    await handler(bot, chat_id, parts[1:])


async def cmd_start(bot: TelegramBot, chat_id: str, _args: list[str]) -> None:
    text = (
        "\U0001f4b9 <b>TradeMind Bot</b>\n"
        "\n"
        "Your AI-powered trading assistant for Indian markets.\n"
        "\n"
        "Commands:\n"
        "/status — System status\n"
        "/agents — Active agents\n"
        "/trades — Today's trades\n"
        "/risk — Risk dashboard\n"
        "/pnl — Today's P&L\n"
        "/pause — Pause all agents\n"
        "/help — Show this help"
    )
    await bot.send_message(text, chat_id=chat_id)


async def cmd_help(bot: TelegramBot, chat_id: str, _args: list[str]) -> None:
    await cmd_start(bot, chat_id, _args)


async def cmd_status(bot: TelegramBot, chat_id: str, _args: list[str]) -> None:
    """Show system status: mode, agents, connections."""
    try:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        await redis.ping()
        redis_status = "\u2705 Connected"
        await redis.close()
    except Exception:
        redis_status = "\u274c Disconnected"

    mode = "\U0001f4dd Paper" if settings.trading_mode == "paper" else "\U0001f534 LIVE"

    text = (
        f"\U0001f4ca <b>System Status</b>\n"
        f"\n"
        f"Mode: {mode}\n"
        f"Redis: {redis_status}\n"
        f"Gemini: {'configured' if settings.gemini_api_key else 'not set'}\n"
        f"Zerodha: {'configured' if settings.kite_api_key else 'not set'}\n"
    )
    await bot.send_message(text, chat_id=chat_id)


async def cmd_agents(bot: TelegramBot, chat_id: str, _args: list[str]) -> None:
    """List active agents and their last signals."""
    from app.db.repositories import AgentRepository
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        repo = AgentRepository(session)
        agents = await repo.get_active()

    if not agents:
        await bot.send_message("\U0001f916 No active agents.", chat_id=chat_id)
        return

    text = f"\U0001f916 <b>Active Agents ({len(agents)})</b>\n\n"
    for a in agents:
        status_emoji = "\u2705" if a.status == "active" else "\u23f8\ufe0f"
        text += (
            f"{status_emoji} <b>{a.name}</b>\n"
            f"   Strategy: {a.strategy_type}\n"
            f"   Capital: \u20b9{a.capital_allocated:,.0f}\n"
            f"\n"
        )

    await bot.send_message(text, chat_id=chat_id)


async def cmd_trades(bot: TelegramBot, chat_id: str, _args: list[str]) -> None:
    """Show today's trades."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.db.models import Trade
    from app.db.session import async_session_factory

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Trade)
            .where(Trade.created_at >= today_start)
            .order_by(Trade.created_at.desc())
            .limit(10)
        )
        trades = result.scalars().all()

    if not trades:
        await bot.send_message("\U0001f4e6 No trades today.", chat_id=chat_id)
        return

    text = f"\U0001f4e6 <b>Today's Trades ({len(trades)})</b>\n\n"
    for t in trades:
        emoji = "\U0001f7e2" if t.side == "BUY" else "\U0001f534"
        pnl_str = f"\u20b9{t.pnl:+,.0f}" if t.pnl else "—"
        text += (
            (
                f"{emoji} {t.side} {t.symbol} x{t.quantity}\n"
                f"   Fill: \u20b9{t.fill_price:,.2f}  P&L: {pnl_str}\n"
            )
            if t.fill_price
            else (f"{emoji} {t.side} {t.symbol} x{t.quantity} [{t.status}]\n")
        )

    await bot.send_message(text, chat_id=chat_id)


async def cmd_risk(bot: TelegramBot, chat_id: str, _args: list[str]) -> None:
    """Show risk dashboard."""
    try:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        from app.data.cache import PriceCache

        cache = PriceCache(redis)

        daily_pnl = await cache.get_daily_pnl()
        circuit_breaker = await cache.is_circuit_breaker_active()
        await redis.close()
    except Exception:
        await bot.send_message("\u274c Could not fetch risk data.", chat_id=chat_id)
        return

    from decimal import Decimal

    from app.risk.limits import RiskLimits

    limits = RiskLimits()
    daily_limit = float(Decimal(str(settings.default_capital)) * limits.max_daily_loss_pct / 100)
    pct_used = (abs(daily_pnl) / daily_limit * 100) if daily_limit > 0 else 0

    cb_text = "\U0001f6a8 ACTIVE" if circuit_breaker else "\u2705 Inactive"

    text = (
        f"\U0001f6e1\ufe0f <b>Risk Dashboard</b>\n"
        f"\n"
        f"Circuit Breaker: {cb_text}\n"
        f"Daily Loss: \u20b9{abs(daily_pnl):,.0f} / \u20b9{daily_limit:,.0f} ({pct_used:.0f}%)\n"
        f"Max Drawdown Limit: {float(limits.max_drawdown_pct)}%\n"
        f"Max Positions: {limits.max_open_positions}\n"
        f"Max Position Size: {float(limits.max_position_size_pct)}%\n"
    )
    await bot.send_message(text, chat_id=chat_id)


async def cmd_pnl(bot: TelegramBot, chat_id: str, _args: list[str]) -> None:
    """Show today's P&L."""
    try:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        from app.data.cache import PriceCache

        cache = PriceCache(redis)
        daily_pnl = await cache.get_daily_pnl()
        await redis.close()
    except Exception:
        await bot.send_message("\u274c Could not fetch P&L data.", chat_id=chat_id)
        return

    emoji = "\U0001f4b0" if daily_pnl >= 0 else "\U0001f4c9"
    text = f"{emoji} <b>Today's P&L:</b> \u20b9{daily_pnl:+,.0f}"
    await bot.send_message(text, chat_id=chat_id)


async def cmd_pause_all(bot: TelegramBot, chat_id: str, _args: list[str]) -> None:
    """Pause all active agents."""
    from app.db.repositories import AgentRepository
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        repo = AgentRepository(session)
        agents = await repo.get_active()
        count = 0
        for a in agents:
            await repo.update_status(a.id, "paused")
            count += 1
        await session.commit()

    text = f"\u23f8\ufe0f Paused {count} agent(s)."
    await bot.send_message(text, chat_id=chat_id)


async def cmd_unknown(bot: TelegramBot, chat_id: str, _args: list[str]) -> None:
    await bot.send_message(
        "Unknown command. Type /help for available commands.",
        chat_id=chat_id,
    )
