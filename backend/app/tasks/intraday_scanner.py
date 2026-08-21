"""Intraday scanner — independent Celery task for intraday technical agents.

Runs every 5 min during market hours. Completely isolated from the daily pipeline:
- No ensemble combination
- No Gemini/reasoning validation
- Signals go straight from agent.analyze() → RiskManager → ExecutionEngine
"""

import asyncio

import structlog
from celery import shared_task

from app.tasks.holidays import is_market_holiday

log = structlog.get_logger()


@shared_task(name="app.tasks.intraday_scanner.run_intraday_scan")
def run_intraday_scan():
    """Intraday scan — fires every 5 min during market hours."""
    if is_market_holiday():
        return {"status": "skipped", "reason": "market_holiday"}

    log.info("intraday_scan_started")
    signals = asyncio.run(_run_intraday_scan())
    log.info("intraday_scan_completed", signals=len(signals))
    return {"status": "completed", "signals": len(signals)}


async def _run_intraday_scan() -> list[dict]:
    import uuid
    from datetime import datetime, timezone
    from decimal import Decimal

    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.agents.signals import MarketSnapshot
    from app.config import settings
    from app.data.cache import PriceCache
    from app.db.models import Agent
    from app.db.repositories import AgentRepository, CandleRepository
    from app.notifications.telegram import get_bot

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    cache = PriceCache(redis)
    bot = get_bot()

    # Dynamic holiday check
    from app.tasks.holidays import is_market_holiday_async

    if await is_market_holiday_async(redis):
        log.info("intraday_scan_skipped", reason="market_holiday_dynamic")
        await redis.close()
        return []

    kite_token = await cache.get_kite_token()
    if not kite_token:
        log.warning("intraday_scan_skipped", reason="kite_not_authenticated")
        await bot.close()
        await redis.close()
        return []

    db_engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    results: list[dict] = []

    async with session_factory() as session:
        agent_repo = AgentRepository(session)
        candle_repo = CandleRepository(session)

        # Find only intraday_technical agents
        active = await agent_repo.get_active()
        intraday_agents = [a for a in active if a.strategy_type == "intraday_technical"]
        if not intraday_agents:
            log.info("intraday_scan_no_agents")
            await bot.close()
            await redis.close()
            await db_engine.dispose()
            return []

        # Instantiate agents
        from app.agents.orchestrator import AGENT_CLASSES

        agent_instances = {}
        for db_agent in intraday_agents:
            cls = AGENT_CLASSES.get(db_agent.strategy_type)
            if not cls:
                continue
            agent_instances[str(db_agent.id)] = cls(
                agent_id=str(db_agent.id),
                name=db_agent.name,
                config=db_agent.config or {},
            )

        # Collect universe across all intraday agents
        from app.data.universe import get_universe

        all_symbols: set[str] = set()
        for db_agent in intraday_agents:
            uni = db_agent.universe or {}
            if isinstance(uni, list):
                all_symbols.update(uni)
            elif isinstance(uni, dict):
                if "index" in uni:
                    all_symbols.update(get_universe(uni["index"]))
                elif "symbols" in uni:
                    all_symbols.update(uni["symbols"])

        # Build snapshots: live quote (Redis) + intraday candles (DB)
        snapshots: dict[str, MarketSnapshot] = {}
        for symbol in all_symbols:
            quote = await cache.get_quote("NSE", symbol)
            if not quote:
                continue
            try:
                ltp = float(quote.get("ltp", 0))
                if ltp <= 0:
                    continue
                candles_5m_db = await candle_repo.get_candles(
                    symbol=symbol,
                    exchange="NSE",
                    timeframe="5m",
                    limit=150,
                )
                candles_15m_db = await candle_repo.get_candles(
                    symbol=symbol,
                    exchange="NSE",
                    timeframe="15m",
                    limit=80,
                )
                candles_5m = [_candle_to_dict(c) for c in reversed(candles_5m_db)]
                candles_15m = [_candle_to_dict(c) for c in reversed(candles_15m_db)]
                snapshots[symbol] = MarketSnapshot(
                    symbol=symbol,
                    exchange="NSE",
                    ltp=ltp,
                    open=float(quote.get("open", ltp)),
                    high=float(quote.get("high", ltp)),
                    low=float(quote.get("low", ltp)),
                    close=float(quote.get("close", ltp)),
                    volume=int(quote.get("volume", 0)),
                    candles_5m=candles_5m,
                    candles_15m=candles_15m,
                    candles_1d=[],
                    timestamp=datetime.now(timezone.utc),
                )
            except Exception:
                log.exception("intraday_snapshot_build_error", symbol=symbol)

        log.info("intraday_snapshots_built", count=len(snapshots))

        # Run each intraday agent on its own universe
        from app.execution.interface import ExecutionEngine
        from app.risk.manager import RiskManager

        risk_mgr = RiskManager(session, cache)
        is_paper = settings.trading_mode == "paper"
        engine = ExecutionEngine(session, cache, is_paper=is_paper)

        for db_agent in intraday_agents:
            agent = agent_instances.get(str(db_agent.id))
            if not agent:
                continue
            uni = db_agent.universe or {}
            if isinstance(uni, list):
                symbols = uni
            elif isinstance(uni, dict):
                if "index" in uni:
                    symbols = get_universe(uni["index"])
                elif "symbols" in uni:
                    symbols = uni["symbols"]
                else:
                    symbols = []
            else:
                symbols = []

            for symbol in symbols:
                snap = snapshots.get(symbol)
                if not snap:
                    continue
                try:
                    signal = await agent.analyze(snap)
                except Exception:
                    log.exception("intraday_analyze_error", symbol=symbol, agent=db_agent.name)
                    continue
                if signal is None:
                    continue

                # Direct to risk manager — no ensemble, no reasoning
                db_agent_row = await session.get(Agent, uuid.UUID(signal.agent_id))
                agent_capital = db_agent_row.capital_allocated if db_agent_row else Decimal("0")
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
                    await bot.notify_trade_approved(
                        symbol=signal.symbol,
                        action=signal.action,
                        quantity=proposal.quantity,
                        price=signal.entry_price,
                        stop_loss=proposal.stop_loss,
                        agent_name=signal.agent_name,
                    )
                    exec_result = await engine.execute(proposal)
                    entry["trade_id"] = exec_result.get("trade_id")
                    entry["fill_price"] = exec_result.get("fill_price")
                    entry["pnl"] = exec_result.get("pnl")
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
                    await bot.notify_trade_rejected(
                        symbol=signal.symbol,
                        action=signal.action,
                        agent_name=signal.agent_name,
                        reasons=check.rejections,
                    )

                # Cache last-signal for dashboard
                await cache.set_agent_last_signal(
                    signal.agent_id,
                    {
                        "symbol": signal.symbol,
                        "action": signal.action,
                        "confidence": signal.confidence,
                        "timestamp": signal.timestamp.isoformat(),
                    },
                )

                results.append(entry)

    await bot.close()
    await redis.close()
    await db_engine.dispose()
    return results


def _candle_to_dict(c) -> dict:
    return {
        "time": c.time,
        "open": float(c.open),
        "high": float(c.high),
        "low": float(c.low),
        "close": float(c.close),
        "volume": c.volume,
    }
