from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (Index("ix_candles_lookup", "symbol", "exchange", "timeframe", "time"),)

    # Composite primary key: symbol + exchange + timeframe + time
    symbol: Mapped[str] = mapped_column(String(30), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(10), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(5), primary_key=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    open: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    high: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    low: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    close: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    volume: Mapped[int] = mapped_column(BigInteger)
