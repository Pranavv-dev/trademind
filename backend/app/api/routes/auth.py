"""Kite Connect authentication routes — handles OAuth login flow."""

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.config import settings
from app.data.cache import PriceCache
from app.data.feeds.instruments import get_instrument_master
from app.data.feeds.kite_ws import get_ticker_manager
from app.dependencies import get_redis
from app.execution.broker.kite import get_kite_broker

log = structlog.get_logger()

router = APIRouter()


@router.get("/kite/login")
async def kite_login():
    """Redirect user to Zerodha login page."""
    broker = get_kite_broker()
    try:
        login_url = broker.get_login_url()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"login_url": login_url}


@router.get("/kite/callback")
async def kite_callback(
    request_token: str = Query(...),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Handle Zerodha OAuth callback — exchange request_token for access_token.

    After successful auth:
    1. Generates session (gets access_token)
    2. Persists token to Redis so celery workers can use it
    3. Loads instrument master
    4. Connects WebSocket ticker
    """
    broker = get_kite_broker()

    if not settings.kite_api_secret:
        raise HTTPException(status_code=500, detail="Kite API secret not configured")

    try:
        session_data = await broker.generate_session(request_token)
    except Exception as e:
        log.error("kite_auth_failed", error=str(e))
        raise HTTPException(status_code=400, detail=f"Authentication failed: {e}")

    access_token = session_data["access_token"]
    user_id = session_data.get("user_id", "unknown")

    # Persist token to Redis so celery workers can pick it up
    cache = PriceCache(redis)
    await cache.set_kite_token(access_token)
    log.info("kite_token_persisted_to_redis", user_id=user_id)

    log.info("kite_auth_success", user_id=user_id)

    # Load instrument master with the authenticated client
    instrument_master = get_instrument_master()
    if not instrument_master.loaded:
        count = await instrument_master.load(broker.kite)
        log.info("instruments_loaded_after_auth", count=count)

    # Connect WebSocket ticker and subscribe to NIFTY 50 tokens
    ticker = get_ticker_manager()
    if not ticker.connected:
        await ticker.connect(
            api_key=broker.api_key,
            access_token=access_token,
        )

    # Subscribe ticker to NIFTY 50 tokens.
    # Note: we don't gate on ticker.connected because the WS handshake happens
    # in a background thread and may not be complete yet. ticker.subscribe()
    # populates _subscribed_tokens synchronously; on_connect resubscribes from it.
    if instrument_master.loaded:
        from app.api.routes.market import NIFTY50_SYMBOLS

        tokens = instrument_master.get_tokens([("NSE", s) for s in NIFTY50_SYMBOLS])
        if tokens:
            token_list = list(tokens.values())
            try:
                await ticker.subscribe(token_list)
                log.info("ticker_subscribed", symbols=len(tokens), tokens=len(token_list))
            except Exception:
                # WS may not be ready yet; tokens are cached in _subscribed_tokens
                # and will be picked up by on_connect when the WS opens.
                log.info("ticker_subscribe_queued", symbols=len(tokens))

    # Redirect to frontend dashboard on success
    return RedirectResponse(url=f"{settings.kite_redirect_url.rsplit('/api', 1)[0]}/?auth=success")


@router.post("/kite/subscribe")
async def kite_subscribe():
    """Subscribe ticker to NIFTY 50 tokens."""
    instrument_master = get_instrument_master()
    ticker = get_ticker_manager()

    if not ticker.connected:
        raise HTTPException(status_code=400, detail="Ticker not connected")
    if not instrument_master.loaded:
        raise HTTPException(status_code=400, detail="Instruments not loaded")

    from app.api.routes.market import NIFTY50_SYMBOLS

    tokens = instrument_master.get_tokens([("NSE", s) for s in NIFTY50_SYMBOLS])
    token_list = list(tokens.values())
    await ticker.subscribe(token_list)
    return {"subscribed": len(token_list), "symbols": list(tokens.keys())}


@router.post("/kite/reconnect")
async def kite_reconnect(redis: aioredis.Redis = Depends(get_redis)):
    """Reconnect the WS ticker using the current Redis token.

    Called by the auto-auth Celery task after it stores a fresh token, so the
    backend's WebSocket ticker (which lives in this process, separate from the
    worker) comes up for the intraday agent. Idempotent and best-effort.
    """
    cache = PriceCache(redis)
    token = await cache.get_kite_token()
    if not token:
        raise HTTPException(status_code=400, detail="No Kite token in Redis")

    broker = get_kite_broker()
    broker.set_access_token(token)

    instrument_master = get_instrument_master()
    if not instrument_master.loaded:
        await instrument_master.load(broker.kite)

    ticker = get_ticker_manager()
    if not ticker.connected:
        ticker._cache = cache
        try:
            await ticker.connect(api_key=settings.kite_api_key, access_token=token)
            log.info("ticker_reconnected_via_auto_auth")
        except Exception:
            log.exception("ticker_reconnect_failed")

    subscribed = 0
    if instrument_master.loaded:
        from app.data.universe import NIFTY50

        tokens = instrument_master.get_tokens([("NSE", s) for s in NIFTY50])
        if tokens:
            try:
                await ticker.subscribe(list(tokens.values()))
                subscribed = len(tokens)
            except Exception:
                log.info("ticker_reconnect_subscribe_queued", symbols=len(tokens))
                subscribed = len(tokens)

    return {"reconnected": True, "ticker_connected": ticker.connected, "subscribed": subscribed}


@router.get("/kite/status")
async def kite_status():
    """Check Kite Connect authentication status."""
    broker = get_kite_broker()
    ticker = get_ticker_manager()
    instruments = get_instrument_master()

    return {
        "authenticated": bool(broker.access_token),
        "ticker_connected": ticker.connected,
        "instruments_loaded": instruments.loaded,
        "instruments_count": instruments.count,
    }
