from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AgentSummary, DashboardOverview, EquityCurvePoint
from app.db.models import Agent, DailySnapshot, Position, Trade
from app.db.session import get_session

router = APIRouter()


@router.get("/overview", response_model=DashboardOverview)
async def get_overview(session: AsyncSession = Depends(get_session)):
    # Total capital from all agents
    capital_result = await session.execute(select(func.sum(Agent.capital_allocated)))
    total_capital = capital_result.scalar() or Decimal("0")

    # Deployed capital = sum of open position values
    deployed_result = await session.execute(
        select(func.coalesce(func.sum(Position.avg_price * Position.quantity), 0)).where(
            Position.closed_at.is_(None)
        )
    )
    deployed_capital = deployed_result.scalar() or Decimal("0")
    available_capital = total_capital - deployed_capital

    # Overall PnL from all trades
    pnl_result = await session.execute(
        select(func.coalesce(func.sum(Trade.pnl), 0)).where(Trade.status == "filled")
    )
    total_pnl_overall = pnl_result.scalar() or Decimal("0")

    # Today's PnL
    today = date.today()
    today_pnl_result = await session.execute(
        select(func.coalesce(func.sum(Trade.pnl), 0)).where(
            Trade.status == "filled", func.date(Trade.executed_at) == today
        )
    )
    total_pnl_today = today_pnl_result.scalar() or Decimal("0")

    # Active agents count
    active_result = await session.execute(
        select(func.count(Agent.id)).where(Agent.status == "active")
    )
    active_agents = active_result.scalar() or 0

    # Today's trade stats
    today_stats = await session.execute(
        select(
            func.count(Trade.id),
            func.count(Trade.id).filter(Trade.pnl > 0),
        ).where(Trade.status == "filled", func.date(Trade.created_at) == today)
    )
    row = today_stats.one()
    total_trades_today = row[0]

    # Overall win rate
    all_stats = await session.execute(
        select(
            func.count(Trade.id),
            func.count(Trade.id).filter(Trade.pnl > 0),
        ).where(Trade.status == "filled", Trade.pnl.isnot(None))
    )
    all_row = all_stats.one()
    total_trades_with_pnl = all_row[0]
    win_count = all_row[1]
    win_rate = (
        Decimal(str(win_count / total_trades_with_pnl * 100))
        if total_trades_with_pnl > 0
        else Decimal("0")
    )

    # Latest snapshot for sharpe/drawdown
    snapshot_result = await session.execute(
        select(DailySnapshot)
        .where(DailySnapshot.agent_id.is_(None))
        .order_by(DailySnapshot.date.desc())
        .limit(1)
    )
    snapshot = snapshot_result.scalar_one_or_none()

    return DashboardOverview(
        total_capital=total_capital,
        deployed_capital=deployed_capital,
        available_capital=available_capital,
        total_pnl_today=total_pnl_today,
        total_pnl_overall=total_pnl_overall,
        active_agents=active_agents,
        total_trades_today=total_trades_today,
        win_rate=win_rate,
        sharpe_ratio=snapshot.sharpe_ratio if snapshot else None,
        max_drawdown=snapshot.max_drawdown if snapshot else None,
    )


@router.get("/equity-curve", response_model=list[EquityCurvePoint])
async def get_equity_curve(
    days: int = Query(90, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(DailySnapshot)
        .where(DailySnapshot.agent_id.is_(None))
        .order_by(DailySnapshot.date.desc())
        .limit(days)
    )
    snapshots = result.scalars().all()
    return [
        EquityCurvePoint(
            date=s.date,
            portfolio_value=s.portfolio_value,
            daily_pnl=s.daily_pnl,
        )
        for s in reversed(list(snapshots))
    ]


@router.get("/agent-summary", response_model=list[AgentSummary])
async def get_agent_summary(session: AsyncSession = Depends(get_session)):
    agents_result = await session.execute(select(Agent).order_by(Agent.created_at.desc()))
    agents = agents_result.scalars().all()

    today = date.today()
    summaries = []
    for agent in agents:
        # Per-agent today's trade stats
        stats = await session.execute(
            select(
                func.coalesce(func.sum(Trade.pnl), 0),
                func.count(Trade.id),
                func.count(Trade.id).filter(Trade.pnl > 0),
            ).where(
                Trade.agent_id == agent.id,
                Trade.status == "filled",
                func.date(Trade.created_at) == today,
            )
        )
        row = stats.one()
        pnl_today = row[0]
        trades_today = row[1]
        wins = row[2]
        win_rate = Decimal(str(wins / trades_today * 100)) if trades_today > 0 else Decimal("0")

        # Last signal timestamp from most recent trade
        last_trade = await session.execute(
            select(Trade.executed_at)
            .where(Trade.agent_id == agent.id, Trade.status == "filled")
            .order_by(Trade.created_at.desc())
            .limit(1)
        )
        last_signal_row = last_trade.scalar_one_or_none()
        last_signal = last_signal_row.isoformat() if last_signal_row else None

        summaries.append(
            AgentSummary(
                id=agent.id,
                name=agent.name,
                strategy_type=agent.strategy_type,
                status=agent.status,
                pnl_today=pnl_today,
                trades_today=trades_today,
                win_rate=win_rate,
                last_signal=last_signal,
            )
        )
    return summaries
