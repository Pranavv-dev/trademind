from decimal import Decimal

from sqlalchemy import Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDMixin


class Agent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="paused")
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    universe: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    capital_allocated: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))

    trades = relationship("Trade", back_populates="agent", lazy="selectin")
    positions = relationship("Position", back_populates="agent", lazy="selectin")
    snapshots = relationship("DailySnapshot", back_populates="agent", lazy="selectin")
