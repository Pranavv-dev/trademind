from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.candle import Candle


class CandleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_candles(
        self,
        symbol: str,
        exchange: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        stmt = (
            select(Candle)
            .where(
                Candle.symbol == symbol,
                Candle.exchange == exchange,
                Candle.timeframe == timeframe,
            )
            .order_by(Candle.time.desc())
            .limit(limit)
        )
        if start:
            stmt = stmt.where(Candle.time >= start)
        if end:
            stmt = stmt.where(Candle.time <= end)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def upsert_candles(self, candles: list[dict]) -> int:
        if not candles:
            return 0
        stmt = insert(Candle).values(candles)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "exchange", "timeframe", "time"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return len(candles)
