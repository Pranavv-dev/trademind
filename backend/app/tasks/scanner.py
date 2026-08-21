import asyncio

import structlog
from celery import shared_task

from app.tasks.holidays import is_market_holiday

log = structlog.get_logger()


def _within_alert_window() -> bool:
    """True only during the IST trading window (Mon-Fri 9:00-15:30).

    Outside it there's legitimately no Kite token (it expires overnight; auto-auth
    refreshes at 8 AM), so a "re-auth required" alert would just be overnight noise.
    The genuine auto-auth-failure alert (app/tasks/auto_auth.py) is separate and
    still fires.
    """
    import zoneinfo
    from datetime import datetime

    now = datetime.now(zoneinfo.ZoneInfo("Asia/Kolkata"))
    if now.weekday() >= 5:  # Sat/Sun
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60) <= minutes <= (15 * 60 + 30)


@shared_task(name="app.tasks.scanner.run_scan_cycle")
def run_scan_cycle():
    """Run market scan across all active agents. Triggered every 15 min during market hours."""
    # Quick weekend/known-holiday check (sync, no Redis needed)
    if is_market_holiday():
        log.info("scan_skipped", reason="market_holiday")
        return {"status": "skipped", "reason": "market_holiday"}

    log.info("scan_cycle_started")
    signals = asyncio.run(_run_scan())
    log.info("scan_cycle_completed", signals=len(signals))
    return {"status": "completed", "signals": len(signals)}


async def _run_scan() -> list[dict]:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.agents.orchestrator import AgentOrchestrator
    from app.config import settings
    from app.data.cache import PriceCache
    from app.notifications.telegram import get_bot

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    cache = PriceCache(redis)
    bot = get_bot()

    # Dynamic holiday check (NSE API → Redis cache → hardcoded fallback)
    from app.tasks.holidays import is_market_holiday_async

    if await is_market_holiday_async(redis):
        log.info("scan_skipped", reason="market_holiday_dynamic")
        await redis.close()
        return []

    # Live market check — verify NIFTY is actually trading and token is valid
    from app.execution.broker.kite import get_kite_broker

    kite_token = await cache.get_kite_token()

    async def _alert_auth_required():
        if not _within_alert_window():
            return  # suppress off-hours noise; token isn't expected yet
        already_alerted = await redis.get("alert:auth_required")
        if not already_alerted:
            login_url = f"https://kite.trade/connect/login?api_key={settings.kite_api_key}&v=3"
            await bot.notify_auth_required(login_url)
            await redis.set("alert:auth_required", "1", ex=3600)

    if not kite_token:
        await _alert_auth_required()
        log.warning("scan_skipped", reason="kite_not_authenticated")
        await bot.close()
        await redis.close()
        return []

    # Live market check + NIFTY-regime bias capture.
    # Use kite.quote() (not .ltp) so we get prev_close too, then compute today's
    # % change. We write `market:nifty_bias` to Redis so the risk manager can
    # scale or block new entries on risk-off days.
    try:
        broker = get_kite_broker()
        # Always sync the (per-process, singleton) broker to the authoritative
        # Redis token. A stale in-process token from a prior day would otherwise
        # never refresh and would fail with "Incorrect access_token".
        if broker.access_token != kite_token:
            broker.set_access_token(kite_token)
        import asyncio as _aio
        from functools import partial

        loop = _aio.get_event_loop()
        nifty_quote = await loop.run_in_executor(None, partial(broker.kite.quote, "NSE:NIFTY 50"))
        nifty_data = nifty_quote.get("NSE:NIFTY 50", {})
        nifty_ltp = float(nifty_data.get("last_price", 0))
        nifty_prev_close = float((nifty_data.get("ohlc", {}) or {}).get("close", 0))

        # Movement liveness check (unchanged behavior)
        prev_ltp = await redis.get("market:nifty_ltp")
        if prev_ltp and float(prev_ltp) == nifty_ltp and nifty_ltp > 0:
            log.info("scan_skipped", reason="market_not_moving", nifty_ltp=nifty_ltp)
            await bot.close()
            await redis.close()
            return []
        if nifty_ltp > 0:
            await redis.set("market:nifty_ltp", str(nifty_ltp), ex=900)

        # Compute NIFTY day-change and persist a coarse regime bias.
        # Thresholds: <-1.5% = risk_off_strong (no new entries),
        #             <-1.0% = risk_off (halve position sizes),
        #             >+1.0% = risk_on (normal, slight tailwind),
        #             else  = neutral.
        if nifty_ltp > 0 and nifty_prev_close > 0:
            nifty_pct = (nifty_ltp - nifty_prev_close) / nifty_prev_close * 100
            if nifty_pct <= -1.5:
                bias = "risk_off_strong"
            elif nifty_pct <= -1.0:
                bias = "risk_off"
            elif nifty_pct >= 1.0:
                bias = "risk_on"
            else:
                bias = "neutral"
            # 30-min TTL so a stale Redis state doesn't keep affecting decisions
            # after market hours / next session.
            await redis.set("market:nifty_bias", bias, ex=1800)
            await redis.set("market:nifty_pct_change", f"{nifty_pct:.3f}", ex=1800)
            log.info(
                "nifty_regime",
                nifty_ltp=nifty_ltp,
                nifty_prev_close=nifty_prev_close,
                pct_change=round(nifty_pct, 3),
                bias=bias,
            )
    except Exception as e:
        err = str(e)
        log.warning("live_market_check_failed", error=err[:100])
        if any(kw in err.lower() for kw in ("token", "403", "invalid", "unauthori")):
            await cache.set_kite_token("")  # Clear bad token
            await _alert_auth_required()
            log.warning("scan_skipped", reason="kite_token_invalid")
            await bot.close()
            await redis.close()
            return []

    # Create a fresh engine per scan to avoid event loop conflicts in Celery workers
    db_engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        orchestrator = AgentOrchestrator(session, cache)
        signals = await orchestrator.run_scan_cycle()

        # Pass signals through risk manager
        from decimal import Decimal

        from app.db.models import Agent
        from app.risk.manager import RiskManager

        risk_mgr = RiskManager(session, cache)
        result = []

        for signal in signals:
            import uuid

            db_agent = await session.get(Agent, uuid.UUID(signal.agent_id))
            agent_capital = db_agent.capital_allocated if db_agent else Decimal("0")
            total_capital = Decimal(str(settings.default_capital))

            check, proposal = await risk_mgr.evaluate(signal, agent_capital, total_capital)
            entry = {
                "symbol": signal.symbol,
                "action": signal.action,
                "confidence": signal.confidence,
                "agent": signal.agent_name,
                "approved": check.approved,
                "rejections": check.rejections,
            }

            if check.approved and proposal:
                # Notify trade approved
                await bot.notify_trade_approved(
                    symbol=signal.symbol,
                    action=signal.action,
                    quantity=proposal.quantity,
                    price=signal.entry_price,
                    stop_loss=proposal.stop_loss,
                    agent_name=signal.agent_name,
                )

                # Execute through paper/live engine
                from app.execution.interface import ExecutionEngine

                is_paper = settings.trading_mode == "paper"
                engine = ExecutionEngine(session, cache, is_paper=is_paper)
                exec_result = await engine.execute(proposal)
                entry["trade_id"] = exec_result.get("trade_id")
                entry["fill_price"] = exec_result.get("fill_price")
                entry["pnl"] = exec_result.get("pnl")

                # Notify trade execution
                if exec_result.get("status") == "filled":
                    await bot.notify_trade_executed(
                        symbol=signal.symbol,
                        action=signal.action,
                        quantity=proposal.quantity,
                        fill_price=exec_result.get("fill_price", signal.entry_price),
                        pnl=exec_result.get("pnl"),
                        is_paper=is_paper,
                    )
            else:
                # Notify trade rejected
                await bot.notify_trade_rejected(
                    symbol=signal.symbol,
                    action=signal.action,
                    agent_name=signal.agent_name,
                    reasons=check.rejections,
                )

            result.append(entry)

    await bot.close()
    await redis.close()
    await db_engine.dispose()
    return result


@shared_task(name="app.tasks.scanner.run_pre_market")
def run_pre_market():
    """Pre-market analysis at 8:30 AM IST — primary job is auth check + Telegram alert."""
    if is_market_holiday():
        log.info("pre_market_skipped", reason="market_holiday")
        return {"status": "skipped", "reason": "market_holiday"}

    log.info("pre_market_analysis_started")
    result = asyncio.run(_run_pre_market())
    log.info("pre_market_analysis_completed", auth_ok=result.get("auth_ok"))
    return result


async def _run_pre_market() -> dict:
    from functools import partial

    import redis.asyncio as aioredis

    from app.config import settings
    from app.data.cache import PriceCache
    from app.execution.broker.kite import get_kite_broker
    from app.notifications.telegram import get_bot

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    cache = PriceCache(redis)
    bot = get_bot()

    kite_token = await cache.get_kite_token()

    auth_ok = False
    if kite_token:
        broker = get_kite_broker()
        if broker.access_token != kite_token:
            broker.set_access_token(kite_token)
        try:
            import asyncio as _aio

            loop = _aio.get_event_loop()
            await loop.run_in_executor(None, partial(broker.kite.ltp, "NSE:NIFTY 50"))
            auth_ok = True
        except Exception as e:
            log.warning("pre_market_token_invalid", error=str(e)[:100])
            await cache.set_kite_token("")  # Clear stale token

    if not auth_ok:
        # Only alert during the trading window — pre-market runs before the 8 AM
        # auto-auth on some schedules, and off-hours runs would just be noise.
        if _within_alert_window():
            login_url = f"https://kite.trade/connect/login?api_key={settings.kite_api_key}&v=3"
            await bot.notify_auth_required(login_url)
        log.warning("pre_market_auth_required")
    else:
        await bot.notify_system_ready()

    await bot.close()
    await redis.close()
    return {"auth_ok": auth_ok}
