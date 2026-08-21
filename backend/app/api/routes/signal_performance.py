"""Signal performance analytics — reads from the signal_outcomes learning table.

Exposes endpoints that let the user (or future auto-tuner) answer concrete
questions like "is the sentiment agent profitable?" or "do context_score≥80 picks
beat 60-70 picks?" with data, not hindsight.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.signal_outcome import SignalOutcome
from app.dependencies import get_session

router = APIRouter()


@router.get("/summary")
async def signal_performance_summary(
    days: int = Query(30, ge=1, le=365, description="Lookback window in days"),
    session: AsyncSession = Depends(get_session),
):
    """Per-strategy aggregate stats over the last N days.

    Returns rows like:
        {strategy_type: "proactive", agent_name: "Proactive-NIFTY50",
         trades: 14, wins: 8, losses: 6, win_rate: 0.571,
         total_pnl: 1240.50, avg_pnl: 88.6, avg_r_multiple: 0.72,
         total_charges: 95.30, gross_pnl: 1335.80}
    """
    cutoff_ts = datetime.now(timezone.utc) - timedelta(days=days)

    # SQLAlchemy doesn't expose CAST(boolean → bigint) cleanly in core, so the
    # aggregate below is a raw query to keep it portable + simple.
    raw_q = """
        SELECT
          strategy_type,
          agent_name,
          COUNT(*) AS trades,
          SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
          SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) AS losses,
          COALESCE(SUM(net_pnl), 0) AS total_net_pnl,
          COALESCE(SUM(gross_pnl), 0) AS total_gross_pnl,
          COALESCE(SUM(total_charges), 0) AS total_charges,
          COALESCE(AVG(net_pnl), 0) AS avg_net_pnl,
          AVG(r_multiple) AS avg_r_multiple,
          AVG(days_held) AS avg_days_held
        FROM signal_outcomes
        WHERE closed_at >= :cutoff
        GROUP BY strategy_type, agent_name
        ORDER BY total_net_pnl DESC
    """
    from sqlalchemy import text

    result = await session.execute(text(raw_q), {"cutoff": cutoff_ts})
    rows = result.mappings().all()
    out = []
    for r in rows:
        trades = int(r["trades"] or 0)
        wins = int(r["wins"] or 0)
        losses = int(r["losses"] or 0)
        win_rate = (wins / trades) if trades else 0.0
        out.append(
            {
                "strategy_type": r["strategy_type"],
                "agent_name": r["agent_name"],
                "trades": trades,
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate, 4),
                "total_net_pnl": float(r["total_net_pnl"] or 0),
                "total_gross_pnl": float(r["total_gross_pnl"] or 0),
                "total_charges": float(r["total_charges"] or 0),
                "avg_net_pnl": float(r["avg_net_pnl"] or 0),
                "avg_r_multiple": float(r["avg_r_multiple"])
                if r["avg_r_multiple"] is not None
                else None,
                "avg_days_held": float(r["avg_days_held"])
                if r["avg_days_held"] is not None
                else None,
            }
        )
    return {"days": days, "by_agent": out}


@router.get("/by-reason")
async def signal_performance_by_reason(
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    """Break down closes by reason.

    Reasons: stop_loss / take_profit / trailing_stop / max_holding_days / other.
    """
    cutoff_ts = datetime.now(timezone.utc) - timedelta(days=days)
    from sqlalchemy import text

    raw_q = """
        SELECT
          close_reason,
          COUNT(*) AS trades,
          COALESCE(SUM(net_pnl), 0) AS total_net_pnl,
          COALESCE(AVG(net_pnl), 0) AS avg_net_pnl,
          AVG(r_multiple) AS avg_r_multiple,
          AVG(days_held) AS avg_days_held
        FROM signal_outcomes
        WHERE closed_at >= :cutoff
        GROUP BY close_reason
        ORDER BY trades DESC
    """
    result = await session.execute(text(raw_q), {"cutoff": cutoff_ts})
    rows = result.mappings().all()
    return {
        "days": days,
        "by_reason": [
            {
                "close_reason": r["close_reason"],
                "trades": int(r["trades"]),
                "total_net_pnl": float(r["total_net_pnl"]),
                "avg_net_pnl": float(r["avg_net_pnl"]),
                "avg_r_multiple": float(r["avg_r_multiple"])
                if r["avg_r_multiple"] is not None
                else None,
                "avg_days_held": float(r["avg_days_held"])
                if r["avg_days_held"] is not None
                else None,
            }
            for r in rows
        ],
    }


@router.get("/expectancy")
async def signal_performance_expectancy(
    days: int = Query(30, ge=1, le=365),
    use_cache: bool = Query(
        True, description="Read nightly-cached snapshot from Redis if available"
    ),
    session: AsyncSession = Depends(get_session),
):
    """R-multiple expectancy + hit rate + profit factor per agent.

    By default reads the nightly snapshot from Redis (computed at 4:00 PM IST).
    Pass `use_cache=false` to force a live recompute.
    """
    if use_cache:
        try:
            import json as _json

            from app.dependencies import get_redis

            redis = await anext(get_redis())  # type: ignore
            cached = await redis.get("expectancy:snapshot")
            if cached:
                payload = _json.loads(cached) if isinstance(cached, (str, bytes)) else cached
                key = str(days) if str(days) in payload.get("windows", {}) else "30"
                window = payload.get("windows", {}).get(key)
                if window:
                    return {
                        "days": days,
                        "computed_at": payload.get("computed_at"),
                        "source": "cached_snapshot",
                        **window,
                    }
        except Exception:
            pass  # fall through to live compute

    from app.backtest.expectancy import compute_aggregate_expectancy, compute_expectancy

    system = await compute_aggregate_expectancy(session, days=days)
    by_agent = await compute_expectancy(session, days=days)
    return {
        "days": days,
        "source": "live",
        "system": system.to_dict() if system else None,
        "by_agent": [a.to_dict() for a in by_agent],
    }


@router.get("/memory")
async def signal_memory(
    session: AsyncSession = Depends(get_session),
):
    """Per-symbol learning-loop memory: recent trades, loss streaks, cumulative R,
    the resulting score multiplier, and the latest reflection. Shows WHY the
    scorer is down/up-weighting each symbol.
    """
    from app.agents.memory import SignalMemory

    mem = await SignalMemory(session).get_symbol_memory()
    rows = sorted(mem.values(), key=lambda m: m.multiplier)
    return {
        "count": len(rows),
        "symbols": [
            {
                "symbol": m.symbol,
                "trades_30d": m.trades_30d,
                "losses_10d": m.losses_10d,
                "net_r_30d": round(m.net_r_30d, 3),
                "score_multiplier": m.multiplier,
                "last_reflection": m.last_reflection,
            }
            for m in rows
        ],
    }


@router.get("/recent")
async def signal_performance_recent(
    limit: int = Query(50, ge=1, le=500),
    agent_name: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Recent N closed trades with full outcome detail."""
    q = select(SignalOutcome).order_by(SignalOutcome.closed_at.desc()).limit(limit)
    if agent_name:
        q = q.where(SignalOutcome.agent_name == agent_name)
    result = await session.execute(q)
    rows = result.scalars().all()
    return {
        "count": len(rows),
        "outcomes": [
            {
                "symbol": r.symbol,
                "agent_name": r.agent_name,
                "strategy_type": r.strategy_type,
                "opened_at": r.opened_at.isoformat(),
                "closed_at": r.closed_at.isoformat(),
                "days_held": r.days_held,
                "close_reason": r.close_reason,
                "entry_price": float(r.entry_price),
                "exit_price": float(r.exit_price),
                "highest_price_seen": float(r.highest_price_seen) if r.highest_price_seen else None,
                "quantity": r.quantity,
                "gross_pnl": float(r.gross_pnl),
                "total_charges": float(r.total_charges),
                "net_pnl": float(r.net_pnl),
                "net_pnl_pct": float(r.net_pnl_pct),
                "r_multiple": float(r.r_multiple) if r.r_multiple is not None else None,
                "confidence_at_entry": float(r.confidence_at_entry)
                if r.confidence_at_entry is not None
                else None,
                "reflection": r.reflection,
                "signal_metadata": r.signal_metadata or {},
            }
            for r in rows
        ],
    }
