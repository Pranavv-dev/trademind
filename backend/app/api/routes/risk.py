from decimal import Decimal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import RiskConfig, RiskRejection, RiskStatus
from app.db.models import Trade
from app.db.session import get_session
from app.dependencies import get_redis

router = APIRouter()

# In-memory risk config (will be loaded from DB/Redis in production)
_risk_config = RiskConfig()


@router.get("/status", response_model=RiskStatus)
async def get_risk_status(
    redis: aioredis.Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
):
    circuit_breaker = await redis.get("risk:circuit_breaker")
    daily_pnl_str = await redis.get("risk:daily_pnl")
    daily_pnl = Decimal(daily_pnl_str) if daily_pnl_str else Decimal("0")

    # Count open positions
    from sqlalchemy import func

    from app.db.models import Agent, Position

    pos_count = await session.execute(
        select(func.count(Position.id)).where(Position.closed_at.is_(None))
    )
    open_positions = pos_count.scalar() or 0

    # Total capital for daily loss limit calculation
    capital_result = await session.execute(select(func.sum(Agent.capital_allocated)))
    total_capital = capital_result.scalar() or Decimal("0")
    daily_loss_limit = total_capital * _risk_config.max_daily_loss_pct / 100

    return RiskStatus(
        circuit_breaker_active=circuit_breaker == "true",
        daily_loss=abs(daily_pnl) if daily_pnl < 0 else Decimal("0"),
        daily_loss_limit=daily_loss_limit,
        open_positions=open_positions,
        max_positions=_risk_config.max_open_positions,
        drawdown_pct=Decimal("0"),  # TODO: calculate from equity peak
        max_drawdown_pct=_risk_config.max_drawdown_pct,
    )


@router.put("/config", response_model=RiskConfig)
async def update_risk_config(body: RiskConfig):
    global _risk_config
    _risk_config = body
    return _risk_config


@router.get("/rejections", response_model=list[RiskRejection])
async def get_rejections(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Trade)
        .where(Trade.status == "rejected")
        .order_by(Trade.created_at.desc())
        .limit(limit)
    )
    trades = result.scalars().all()
    return [
        RiskRejection(
            trade_id=t.id,
            symbol=t.symbol,
            side=t.side,
            reason=t.risk_check.get("reason", "Unknown") if t.risk_check else "Unknown",
            timestamp=t.created_at,
        )
        for t in trades
    ]
