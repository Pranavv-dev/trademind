import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, UUIDMixin


class DailySnapshot(Base, UUIDMixin):
    __tablename__ = "daily_snapshots"
    __table_args__ = (UniqueConstraint("agent_id", "date", name="uq_agent_date"),)

    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    portfolio_value: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    daily_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total_pnl_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    trades_count: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, default=0)
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    sharpe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)

    agent = relationship("Agent", back_populates="snapshots")
