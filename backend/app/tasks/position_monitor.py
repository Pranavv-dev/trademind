"""Position monitor — checks open positions against SL/TP and closes when triggered.

Also enforces a per-strategy MAX HOLDING TIME at end-of-day (15:00-15:25 IST):
positions held longer than the limit for their strategy type are force-closed.
This prevents stale positions (like a sentiment-originated TATASTEEL bleeding
for 22 days before finally hitting SL) from quietly draining capital.
"""

import asyncio
import zoneinfo
from datetime import datetime
from decimal import Decimal
from functools import partial

import structlog
from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import joinedload

from app.tasks.holidays import is_market_holiday

log = structlog.get_logger()

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# Maximum calendar days a position can be held by strategy type before
# end-of-day force close. Picked to match each strategy's natural cadence:
#   sentiment   : noisy / news-driven — close after ~2 weeks if no TP
#   proactive   : trend-following on D1 — give it 3 weeks for a leg to play out
#   technical   : regime-aware, can hold longer trends
#   intraday    : MUST close same day (no overnight intraday risk)
#   reasoning   : LLM-validated, treat like technical
#   ensemble    : same as technical
# Override per-agent via agents.config["max_holding_days"].
MAX_HOLD_DAYS_BY_STRATEGY: dict[str, int] = {
    "sentiment": 15,
    "proactive": 21,
    "technical": 30,
    "intraday_technical": 1,
    "reasoning": 30,
    "ensemble": 30,
}
DEFAULT_MAX_HOLD_DAYS = 30


def _is_eod_window(now_ist: datetime) -> bool:
    """True if we're in the 3:00-3:25 PM IST window (last 30 min of NSE session).

    Position monitor runs every 5 min in this window, so the first run after 15:00
    will pick up time-expired positions. Using EOD avoids closing at noise prices
    earlier in the day and gives positions a full day to potentially recover.
    """
    return now_ist.hour == 15 and now_ist.minute >= 0 and now_ist.minute <= 25


@shared_task(name="app.tasks.position_monitor.check_positions")
def check_positions():
    """Check open positions vs current prices, close at SL/TP."""
    if is_market_holiday():
        return {"status": "skipped", "reason": "market_holiday"}

    result = asyncio.run(_check_positions())
    return result


async def _check_positions() -> dict:
    import redis.asyncio as aioredis

    from app.config import settings
    from app.data.cache import PriceCache
    from app.db.models.position import Position
    from app.execution.broker.kite import get_kite_broker

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    cache = PriceCache(redis)

    # Need valid Kite token for live prices
    kite_token = await cache.get_kite_token()
    if not kite_token:
        log.warning("position_monitor_skipped", reason="no_kite_token")
        await redis.close()
        return {"status": "skipped", "reason": "no_kite_token"}

    broker = get_kite_broker()
    # Sync to the authoritative Redis token (a stale per-process token would
    # otherwise persist and fail with "Incorrect access_token").
    if broker.access_token != kite_token:
        broker.set_access_token(kite_token)

    db_engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    closed = []

    async with session_factory() as session:
        # Fetch all open positions, eager-loading the agent so we can read strategy_type
        # without an N+1 query later when checking max-holding-days.
        from app.db.models import Agent  # noqa: F401 (registers the relationship)

        result = await session.execute(
            select(Position).where(Position.closed_at.is_(None)).options(joinedload(Position.agent))
        )
        positions = list(result.scalars().unique().all())

        if not positions:
            log.info("position_monitor_complete", open_positions=0)
            await redis.close()
            await db_engine.dispose()
            return {"status": "ok", "checked": 0, "closed": 0}

        # Batch fetch current prices from Kite
        symbols = list({p.symbol for p in positions})
        instruments = [f"NSE:{s}" for s in symbols]
        try:
            loop = asyncio.get_event_loop()
            ltp_data = await loop.run_in_executor(None, partial(broker.kite.ltp, *instruments))
        except Exception as e:
            log.error("position_monitor_price_error", error=str(e)[:100])
            await redis.close()
            await db_engine.dispose()
            return {"status": "error", "reason": str(e)[:100]}

        prices = {}
        for s in symbols:
            key = f"NSE:{s}"
            if key in ltp_data:
                prices[s] = float(ltp_data[key].get("last_price", 0))

        # Check each position against SL/TP
        from app.agents.signals import Signal, TradeProposal
        from app.execution.interface import ExecutionEngine
        from app.notifications.telegram import get_bot

        bot = get_bot()
        is_paper = settings.trading_mode == "paper"
        engine = ExecutionEngine(session, cache, is_paper=is_paper)

        for pos in positions:
            current = prices.get(pos.symbol)
            if not current or current <= 0:
                continue

            # Update current price and unrealized P&L
            pos.current_price = Decimal(str(round(current, 2)))
            if pos.side == "LONG":
                pos.unrealized_pnl = (pos.current_price - pos.avg_price) * pos.quantity
            else:
                pos.unrealized_pnl = (pos.avg_price - pos.current_price) * pos.quantity

            # ── Trailing stop: ratchet highest_price up, compute trailing SL ──
            # Activates after price moves +1R (entry → static_sl distance) into profit.
            # Locks intermediate gains: a runner that gives back > trail_pct from peak closes.
            # Only ratchets UP — never loosens.
            current_dec = Decimal(str(round(current, 2)))
            if pos.side == "LONG":
                if pos.highest_price is None or current_dec > pos.highest_price:
                    pos.highest_price = current_dec
                trail_pct = Decimal("0.04")  # 4% trail from peak (could be ATR-based later)
                static_sl = pos.stop_loss
                # +2R activation: let winners run before trailing kicks in. Backtest
                # (docs/SIGNAL_EDGE_FINDINGS.md) showed arming at +2R vs +1R lifts
                # avg-win from ~0.77R to ~1.0R+ and is the single biggest exit improvement.
                trail_arm_level = None
                if pos.stop_loss and pos.avg_price:
                    static_risk = pos.avg_price - pos.stop_loss
                    trail_arm_level = pos.avg_price + Decimal("2") * static_risk
                trailing_sl = None
                if (
                    pos.highest_price is not None
                    and trail_arm_level is not None
                    and pos.highest_price >= trail_arm_level
                ):
                    trailing_sl = pos.highest_price * (Decimal("1") - trail_pct)
                # Effective SL is the HIGHER of static and trailing (ratchet up only).
                effective_sl = static_sl
                if trailing_sl is not None:
                    effective_sl = max(static_sl or trailing_sl, trailing_sl)
            else:
                # SHORT logic: highest_price is actually lowest_price in spirit, but
                # we don't trade short today so keep the column LONG-only for now.
                effective_sl = pos.stop_loss

            sl_eff = float(effective_sl) if effective_sl else None
            sl_static = float(pos.stop_loss) if pos.stop_loss else None
            tp = float(pos.take_profit) if pos.take_profit else None
            reason = None

            if sl_eff and pos.side == "LONG" and current <= sl_eff:
                # Distinguish trailing from static for analytics
                if sl_static and sl_eff > sl_static:
                    reason = "trailing_stop"
                else:
                    reason = "stop_loss"
            elif tp and pos.side == "LONG" and current >= tp:
                reason = "take_profit"
            elif sl_static and pos.side == "SHORT" and current >= sl_static:
                reason = "stop_loss"
            elif tp and pos.side == "SHORT" and current <= tp:
                reason = "take_profit"

            # Max-holding-days check — fires on every cycle (was previously gated
            # to the 15:00-15:25 IST EOD window, which created a +24h bleed if the
            # window was missed due to a Celery crash, Redis flake, or deploy).
            # For intraday agents (max_days=1) we explicitly force-close ANY
            # position from a prior trading day at the first opportunity — the
            # design promise is "no intraday overnight risk".
            now_ist = datetime.now(IST)
            if not reason and pos.agent is not None:
                strategy = pos.agent.strategy_type
                agent_config = pos.agent.config or {}
                max_days = int(
                    agent_config.get(
                        "max_holding_days",
                        MAX_HOLD_DAYS_BY_STRATEGY.get(strategy, DEFAULT_MAX_HOLD_DAYS),
                    )
                )
                opened_ist_date = pos.opened_at.astimezone(IST).date()
                days_held = (now_ist.date() - opened_ist_date).days

                # Intraday agents: any position from a prior date must close immediately.
                if strategy == "intraday_technical" and days_held >= 1:
                    reason = "max_holding_days"
                # Other strategies: still prefer to close near EOD (less slippage,
                # gives positions a full session to recover), but allow earlier-cycle
                # firing as soon as max_days is exceeded — defends against a missed
                # EOD window.
                elif days_held > max_days:
                    reason = "max_holding_days"
                elif days_held == max_days and _is_eod_window(now_ist):
                    reason = "max_holding_days"

                if reason == "max_holding_days":
                    log.info(
                        "max_hold_days_exit_triggered",
                        symbol=pos.symbol,
                        strategy=strategy,
                        days_held=days_held,
                        max_days=max_days,
                        in_eod_window=_is_eod_window(now_ist),
                    )

            if reason:
                log.info(
                    "position_close_triggered",
                    symbol=pos.symbol,
                    reason=reason,
                    current=current,
                    sl_static=sl_static,
                    sl_effective=sl_eff,
                    tp=tp,
                    avg_price=float(pos.avg_price),
                    highest_price=float(pos.highest_price) if pos.highest_price else None,
                    quantity=pos.quantity,
                )

                # Create a SELL signal to close the position
                close_action = "SELL" if pos.side == "LONG" else "BUY"
                signal = Signal(
                    symbol=pos.symbol,
                    exchange=pos.exchange,
                    action=close_action,
                    confidence=1.0,
                    entry_price=current,
                    stop_loss=current,
                    take_profit=current,
                    reasoning=f"Position closed: {reason} triggered at {current:.2f}",
                    agent_id=str(pos.agent_id),
                    agent_name="PositionMonitor",
                    metadata={"close_reason": reason},
                )
                proposal = TradeProposal(
                    signal=signal,
                    order_type="MARKET",
                    product="CNC",
                    quantity=pos.quantity,
                    stop_loss=current,
                    take_profit=current,
                )

                try:
                    exec_result = await engine.execute(proposal)
                    pnl = exec_result.get("pnl")
                    closed.append(
                        {
                            "symbol": pos.symbol,
                            "reason": reason,
                            "price": current,
                            "pnl": pnl,
                        }
                    )
                    log.info(
                        "position_closed_by_monitor",
                        symbol=pos.symbol,
                        reason=reason,
                        pnl=pnl,
                    )

                    # Telegram notification
                    pnl_str = f"₹{pnl:+,.2f}" if pnl else "N/A"
                    if reason == "take_profit":
                        emoji = "🟢"
                    elif reason == "stop_loss":
                        emoji = "🔴"
                    elif reason == "trailing_stop":
                        emoji = "🟡"  # locked gains via trailing
                    else:  # max_holding_days
                        emoji = "⏰"
                    await bot.send_message(
                        f"{emoji} <b>Position Closed: {pos.symbol}</b>\n\n"
                        f"Reason: {reason.replace('_', ' ').title()}\n"
                        f"Entry: ₹{float(pos.avg_price):,.2f}\n"
                        f"Exit: ₹{current:,.2f}\n"
                        f"Qty: {pos.quantity}\n"
                        f"P&L: {pnl_str}"
                    )
                except Exception:
                    log.exception("position_close_error", symbol=pos.symbol)

        await session.commit()

    await bot.close()
    await redis.close()
    await db_engine.dispose()

    log.info("position_monitor_complete", checked=len(positions), closed=len(closed))
    return {"status": "ok", "checked": len(positions), "closed": len(closed), "details": closed}
