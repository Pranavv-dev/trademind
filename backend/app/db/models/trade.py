import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDMixin


class Trade(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "trades"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)
    product: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    broker_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    brokerage: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    ai_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk_check: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent = relationship("Agent", back_populates="trades")

    @property
    def agent_name(self) -> str:
        return self.agent.name if self.agent else ""
