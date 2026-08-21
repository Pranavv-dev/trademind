import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.websocket import ws_router
from app.config import settings
from app.dependencies import close_redis, init_redis


def setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


@asynccontextmanager
async def lifespan(application: FastAPI):
    import asyncio

    from app.data.aggregator import get_aggregator
    from app.data.feeds.instruments import get_instrument_master
    from app.data.feeds.kite_ws import get_ticker_manager
    from app.notifications.commands import handle_command
    from app.notifications.telegram import get_bot

    setup_logging()
    log = structlog.get_logger()
    log.info("starting_trademind", trading_mode=settings.trading_mode)
    await init_redis()
    log.info("redis_connected")

    # Wire tick-to-bar aggregator early so it's attached before ticker connects
    ticker = get_ticker_manager()
    aggregator = get_aggregator()
    ticker.set_tick_callback(aggregator.on_ticks)
    log.info("intraday_aggregator_wired")

    # Try to pick up an existing Kite token from Redis (persists across backend restarts)
    import redis.asyncio as aioredis

    from app.data.cache import PriceCache

    redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)
    cache = PriceCache(redis_conn)
    persisted_token = await cache.get_kite_token()

    # Load instrument master if we have ANY credentials (config or Redis)
    instrument_master = get_instrument_master()
    effective_token = settings.kite_access_token or persisted_token
    if settings.kite_api_key and effective_token:
        from kiteconnect import KiteConnect

        kite_client = KiteConnect(api_key=settings.kite_api_key)
        kite_client.set_access_token(effective_token)
        count = await instrument_master.load(kite_client)
        log.info("instruments_loaded", count=count)

    # Connect Kite WebSocket ticker if we have a token (paper OR live — ticks feed the aggregator)
    if effective_token and settings.kite_api_key:
        ticker._cache = cache
        try:
            await ticker.connect(api_key=settings.kite_api_key, access_token=effective_token)
            log.info("kite_ticker_started")
        except Exception:
            log.exception("kite_ticker_startup_failed")

        # Subscribe to NIFTY 50 tokens. The WS handshake runs in a background thread
        # and may not be complete yet — subscribe() populates _subscribed_tokens
        # synchronously and on_connect resubscribes when the WS actually opens.
        if instrument_master.loaded:
            from app.data.universe import NIFTY50

            tokens_map = instrument_master.get_tokens([("NSE", s) for s in NIFTY50])
            if tokens_map:
                try:
                    await ticker.subscribe(list(tokens_map.values()))
                    log.info("ticker_subscribed_on_startup", symbols=len(tokens_map))
                except Exception:
                    log.info("ticker_subscribe_queued_on_startup", symbols=len(tokens_map))

    # Start Telegram bot polling in background
    bot = get_bot()
    poll_task = None
    if bot.enabled:

        async def _command_handler(text: str, chat_id: str) -> None:
            await handle_command(text, chat_id, bot)

        poll_task = asyncio.create_task(bot.start_polling(command_handler=_command_handler))
        log.info("telegram_bot_started")

    yield

    # Shutdown
    if ticker.connected:
        await ticker.disconnect()
        log.info("kite_ticker_stopped")

    if poll_task:
        bot.stop_polling()
        poll_task.cancel()
        await bot.close()
        log.info("telegram_bot_stopped")

    await close_redis()
    log.info("trademind_shutdown")


def create_app() -> FastAPI:
    application = FastAPI(
        title="TradeMind",
        description="AI-powered trading platform for Indian stock markets",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(api_router, prefix="/api")
    application.include_router(ws_router)

    return application


app = create_app()
