"""Unattended daily Kite authentication — runs at 8:00 AM IST on trading days.

This is what lets the system run 9:15-15:30 IST without anyone clicking the
Kite login URL each morning. It:
  1. Performs the TOTP auto-login (app/execution/broker/auto_auth.py)
  2. Stores the fresh access_token in Redis (TTL until 11:55 PM IST)
  3. Loads the instrument master with the new token
  4. Pings the backend so its WebSocket ticker reconnects (for the intraday agent)
  5. Sends a Telegram confirmation (or an alert if it failed → manual fallback)

Retries internally a few times because the Zerodha login endpoint occasionally
flakes early in the morning.
"""

import asyncio

import structlog
from celery import shared_task

from app.tasks.holidays import is_market_holiday

log = structlog.get_logger()

MAX_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 20


@shared_task(name="app.tasks.auto_auth.run_auto_auth")
def run_auto_auth():
    """Daily auto-login. Fires at 8:00 AM IST, Mon-Fri (before pre-market at 8:30)."""
    if is_market_holiday():
        return {"status": "skipped", "reason": "market_holiday"}
    return asyncio.run(_run_auto_auth())


async def _run_auto_auth() -> dict:
    import redis.asyncio as aioredis

    from app.config import settings
    from app.data.cache import PriceCache
    from app.execution.broker.auto_auth import auto_login
    from app.execution.broker.kite import get_kite_broker
    from app.notifications.telegram import get_bot

    if not settings.kite_auto_auth_enabled:
        log.info("auto_auth_disabled")
        return {"status": "disabled"}

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    cache = PriceCache(redis)
    bot = get_bot()

    access_token = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        log.info("auto_auth_attempt", attempt=attempt)
        access_token = await auto_login()
        if access_token:
            break
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(RETRY_SLEEP_SECONDS)

    if not access_token:
        log.error("auto_auth_failed_all_attempts")
        # Fall back to the manual-login alert so the user can rescue the day.
        login_url = f"https://kite.trade/connect/login?api_key={settings.kite_api_key}&v=3"
        try:
            await bot.notify_auth_required(login_url)
        except Exception:
            log.exception("auto_auth_alert_failed")
        await bot.close()
        await redis.aclose()
        return {"status": "failed", "fallback": "manual_alert_sent"}

    # Store token (TTL until 11:55 PM IST handled inside set_kite_token)
    await cache.set_kite_token(access_token)
    log.info("auto_auth_token_stored")

    # Prime the broker + instrument master in this worker process
    broker = get_kite_broker()
    broker.set_access_token(access_token)
    try:
        from app.data.feeds.instruments import get_instrument_master

        master = get_instrument_master()
        if not master.loaded:
            count = await master.load(broker.kite)
            log.info("auto_auth_instruments_loaded", count=count)
    except Exception:
        log.exception("auto_auth_instrument_load_failed")

    # Wake the backend's WS ticker (separate process) so the intraday agent gets ticks.
    # Best-effort — the daily proactive pipeline works off the Redis token + REST quotes
    # regardless, so a failure here does not block the trading day.
    try:
        import httpx

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(f"{settings.backend_internal_url}/api/auth/kite/reconnect")
            log.info("auto_auth_ticker_reconnect_pinged", status=resp.status_code)
    except Exception:
        log.warning("auto_auth_ticker_reconnect_failed", exc_info=False)

    # Confirm via Telegram
    try:
        await bot.notify_system_ready()
    except Exception:
        log.exception("auto_auth_confirm_failed")

    await bot.close()
    await redis.aclose()
    return {"status": "ok", "token_stored": True}
