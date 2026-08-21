import asyncio

import structlog
from celery import shared_task

from app.tasks.holidays import is_market_holiday

log = structlog.get_logger()


@shared_task(name="app.tasks.eod.run_eod_report")
def run_eod_report():
    """End-of-day report at 3:45 PM IST.

    Syncs data, creates the daily snapshot, and sends the Telegram summary.
    """
    if is_market_holiday():
        log.info("eod_skipped", reason="market_holiday")
        return {"status": "skipped", "reason": "market_holiday"}

    # Time guard: the report is scheduled for 3:45 PM IST. celery-beat re-fires due
    # tasks when it restarts, so without this a deploy/restart at any hour would send
    # a spurious "Daily Report". Only send in the genuine EOD window (>= 3:00 PM IST).
    import zoneinfo
    from datetime import datetime

    ist_hour = datetime.now(zoneinfo.ZoneInfo("Asia/Kolkata")).hour
    if ist_hour < 15:
        log.info("eod_skipped", reason="outside_eod_window", ist_hour=ist_hour)
        return {"status": "skipped", "reason": "outside_eod_window"}

    log.info("eod_report_started")
    asyncio.run(_run_eod())
    log.info("eod_report_completed")
    return {"status": "completed"}


async def _run_eod() -> None:
    from datetime import datetime, timezone

    import redis.asyncio as aioredis
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.config import settings
    from app.data.cache import PriceCache
    from app.db.models import Agent, Trade
    from app.notifications.telegram import get_bot

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    cache = PriceCache(redis)
    bot = get_bot()

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Fresh engine per task — the shared async_session_factory is bound to the
    # event loop it was created on, and reusing it under Celery's per-task
    # asyncio.run() loop raises asyncpg "another operation is in progress".
    db_engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            # Today's trades
            trades_result = await session.execute(
                select(Trade).where(Trade.created_at >= today_start)
            )
            trades = trades_result.scalars().all()

            # Agent count
            agents_result = await session.execute(
                select(func.count(Agent.id)).where(Agent.status == "active")
            )
            active_agents = agents_result.scalar() or 0
    finally:
        await db_engine.dispose()

    # Compute stats
    total_pnl = sum(float(t.pnl) for t in trades if t.pnl is not None)
    filled = [t for t in trades if t.status == "filled" and t.pnl is not None]
    wins = sum(1 for t in filled if t.pnl >= 0)
    losses = sum(1 for t in filled if t.pnl < 0)

    # Top gainer / loser
    top_gainer = None
    top_loser = None
    if filled:
        best = max(filled, key=lambda t: float(t.pnl))
        worst = min(filled, key=lambda t: float(t.pnl))
        if best.pnl > 0:
            top_gainer = {"symbol": best.symbol, "pnl": float(best.pnl)}
        if worst.pnl < 0:
            top_loser = {"symbol": worst.symbol, "pnl": float(worst.pnl)}

    await bot.send_daily_summary(
        total_pnl=total_pnl,
        trades_count=len(trades),
        wins=wins,
        losses=losses,
        active_agents=active_agents,
        top_gainer=top_gainer,
        top_loser=top_loser,
    )

    # Reset daily P&L for next day
    await cache.reset_daily_pnl()

    await bot.close()
    await redis.close()
