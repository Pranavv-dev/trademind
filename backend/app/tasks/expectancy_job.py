"""Nightly expectancy snapshot — Pattern #1 from STRATEGY_RESEARCH.md.

Computes rolling 30 / 60 / 90-day expectancy, hit rate, profit factor, payoff
ratio per agent from signal_outcomes and caches the result in Redis for the
dashboard. Runs after EOD data sync.

Output Redis key:
    expectancy:snapshot  =  {
        "computed_at": ISO timestamp,
        "windows": {
            "30": {"system": {...}, "by_agent": [...]},
            "60": {"system": {...}, "by_agent": [...]},
            "90": {"system": {...}, "by_agent": [...]},
        }
    }

Dashboard reads this key directly — no DB load on every page hit.
"""

import asyncio
import json
from datetime import datetime, timezone

import structlog
from celery import shared_task

log = structlog.get_logger()


@shared_task(name="app.tasks.expectancy_job.compute_expectancy_snapshot")
def compute_expectancy_snapshot():
    """Nightly job — compute rolling expectancy windows and cache to Redis."""
    return asyncio.run(_compute_and_cache())


async def _compute_and_cache() -> dict:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.backtest.expectancy import compute_aggregate_expectancy, compute_expectancy
    from app.config import settings

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    db_engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    payload = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "windows": {},
    }

    try:
        async with session_factory() as session:
            for window_days in (30, 60, 90):
                system = await compute_aggregate_expectancy(session, days=window_days)
                by_agent = await compute_expectancy(session, days=window_days)
                payload["windows"][str(window_days)] = {
                    "system": system.to_dict() if system else None,
                    "by_agent": [a.to_dict() for a in by_agent],
                }
        await redis.set("expectancy:snapshot", json.dumps(payload, default=str), ex=86400 * 2)
        log.info(
            "expectancy_snapshot_cached",
            windows=list(payload["windows"].keys()),
            agents_30d=len(payload["windows"]["30"]["by_agent"]),
        )
    finally:
        await redis.aclose()
        await db_engine.dispose()

    return {
        "status": "ok",
        "windows_cached": list(payload["windows"].keys()),
    }
