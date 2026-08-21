import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, UUIDMixin


class Position(Base, UUIDMixin):
    __tablename__ = "positions"
    __table_args__ = (
        # Partial unique index: only one open position per agent/symbol/exchange/side
        Index(
            "uq_open_position",
            "agent_id",
            "symbol",
            "exchange",
            "side",
            unique=True,
            postgresql_where="closed_at IS NULL",
        ),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Entry-leg brokerage + STT + GST + SEBI + stamp duty + exchange fees.
    # Captured at position open so closed P&L deducts BOTH legs honestly.
    # Default 0 for backfill compatibility with positions opened pre-migration.
    entry_charges: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    # Trailing-stop high-water mark. Updated each position_monitor cycle to
    # max(highest_price, current_price). Once price moves +1R above entry,
    # the trailing stop activates; SL becomes max(static_sl, highest_price * (1 - trail_pct)).
    highest_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Snapshot of the OPENING signal's metadata (context_score, rs_score, sector_momentum,
    # regime, etc.). Persisted at open so the learning-loop reflection at close can
    # recall WHY we entered — the closing signal (from position_monitor) doesn't carry it.
    entry_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent = relationship("Agent", back_populates="positions")
