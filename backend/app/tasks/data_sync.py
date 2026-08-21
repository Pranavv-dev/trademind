import asyncio
from datetime import datetime, timedelta

import structlog
from celery import shared_task

from app.tasks.holidays import is_market_holiday

log = structlog.get_logger()


@shared_task(name="app.tasks.data_sync.sync_daily_candles")
def sync_daily_candles():
    """Sync daily OHLCV candles at 4:30 PM IST after market close."""
    if is_market_holiday():
        log.info("data_sync_skipped", reason="market_holiday")
        return {"status": "skipped", "reason": "market_holiday"}

    log.info("data_sync_started")
    asyncio.run(_sync())
    log.info("data_sync_completed")
    return {"status": "completed"}


async def _sync():
    import redis.asyncio as aioredis

    from app.config import settings
    from app.data.cache import PriceCache
    from app.data.downloader import DataDownloader
    from app.data.feeds.instruments import get_instrument_master
    from app.data.universe import get_all_symbols
    from app.db.session import async_session_factory
    from app.execution.broker.kite import get_kite_broker

    # Load instrument master using Kite token from Redis (set after user auth)
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    cache = PriceCache(redis)
    kite_token = await cache.get_kite_token()

    if not kite_token:
        log.warning("data_sync_skipped", reason="kite_not_authenticated")
        await redis.close()
        return

    broker = get_kite_broker()
    if broker.access_token != kite_token:
        broker.set_access_token(kite_token)

    master = get_instrument_master()
    if not master.loaded:
        count = await master.load(broker.kite)
        if count == 0:
            log.error("data_sync_skipped", reason="instrument_master_load_failed")
            await redis.close()
            return
        log.info("instrument_master_loaded", count=count)

    await redis.close()

    symbols = get_all_symbols()
    today = datetime.now()
    start = today - timedelta(days=5)  # Fetch last 5 days to fill gaps

    async with async_session_factory() as session:
        downloader = DataDownloader(session)
        results = await downloader.download_universe(symbols, start=start, end=today)
        total = sum(results.values())
        log.info("data_sync_results", symbols_synced=len(results), candles_total=total)
