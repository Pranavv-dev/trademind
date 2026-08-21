import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.trade import Trade


class TradeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, trade_id: uuid.UUID) -> Trade | None:
        result = await self.session.execute(select(Trade).where(Trade.id == trade_id))
        return result.scalar_one_or_none()

    async def list_trades(
        self,
        agent_id: uuid.UUID | None = None,
        status: str | None = None,
        side: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Trade]:
        stmt: Select = (
            select(Trade).options(selectinload(Trade.agent)).order_by(Trade.created_at.desc())
        )
        if agent_id:
            stmt = stmt.where(Trade.agent_id == agent_id)
        if status:
            stmt = stmt.where(Trade.status == status)
        if side:
            stmt = stmt.where(Trade.side == side)
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **kwargs) -> Trade:
        trade = Trade(**kwargs)
        self.session.add(trade)
        await self.session.commit()
        await self.session.refresh(trade)
        return trade

    async def update_status(self, trade_id: uuid.UUID, status: str, **kwargs) -> Trade | None:
        trade = await self.get_by_id(trade_id)
        if trade:
            trade.status = status
            for key, value in kwargs.items():
                setattr(trade, key, value)
            await self.session.commit()
            await self.session.refresh(trade)
        return trade

    async def get_today_summary(self, agent_id: uuid.UUID | None = None) -> dict:
        today_start = datetime.combine(date.today(), time.min, tzinfo=timezone.utc)
        stmt = select(
            func.count(Trade.id).label("total_trades"),
            func.count(Trade.id).filter(Trade.side == "BUY").label("buy_count"),
            func.count(Trade.id).filter(Trade.side == "SELL").label("sell_count"),
            func.coalesce(func.sum(Trade.pnl), 0).label("total_pnl"),
            func.count(Trade.id).filter(Trade.pnl > 0).label("win_count"),
            func.count(Trade.id).filter(Trade.pnl < 0).label("loss_count"),
        ).where(Trade.created_at >= today_start)
        if agent_id:
            stmt = stmt.where(Trade.agent_id == agent_id)
        result = await self.session.execute(stmt)
        row = result.one()
        wins = row.win_count
        losses = row.loss_count
        total_with_pnl = wins + losses
        return {
            "total_trades": row.total_trades,
            "buy_count": row.buy_count,
            "sell_count": row.sell_count,
            "total_pnl": row.total_pnl,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total_with_pnl) if total_with_pnl > 0 else 0.0,
        }
