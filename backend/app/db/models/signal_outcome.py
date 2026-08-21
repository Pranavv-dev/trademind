"""Signal outcome learning table — one row per position close.

Purpose: provide a data-driven feedback loop. Without this, every parameter
decision (which agents to keep, what confidence thresholds work, what SL widths
fit) is hindsight-driven. With this, we can answer:

    SELECT agent_id, AVG(actual_pnl), AVG(actual_pnl/expected_pnl)
    FROM signal_outcomes WHERE close_reason = 'take_profit' GROUP BY agent_id;

    -- which agents actually hit their targets?

    SELECT context_score_bucket, COUNT(*),
           AVG(actual_pnl_pct), STDDEV(actual_pnl_pct)
    FROM signal_outcomes GROUP BY context_score_bucket;

    -- do higher context scores actually produce higher returns?

This is the foundation for every future parameter decision.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, UUIDMixin


class SignalOutcome(Base, UUIDMixin):
    """One row per closed position; captures the full lifecycle for learning."""

    __tablename__ = "signal_outcomes"
    __table_args__ = (
        Index("ix_signal_outcomes_agent_strategy", "agent_id", "strategy_type", "closed_at"),
        Index("ix_signal_outcomes_close_reason", "close_reason", "closed_at"),
    )

    # FKs
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id"), nullable=False
    )

    # Identity
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(50), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Lifecycle timestamps + duration
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    days_held: Mapped[int] = mapped_column(Integer, nullable=False)
    close_reason: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # stop_loss, take_profit, max_holding_days, trailing_stop, manual, other

    # Prices
    entry_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stop_loss_set: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    take_profit_set: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    highest_price_seen: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Sizing
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # P&L breakdown
    gross_pnl: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )  # exit - entry, no charges
    total_charges: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    net_pnl: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )  # what actually hit account
    net_pnl_pct: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)

    # Signal-quality features at entry (JSONB blob — flexible per agent type)
    # Examples: {context_score: 70, rs_score: 1.24, sector_momentum: 0.105,
    #            proximity_52w: 0.91, volume_zscore: 2.3, tightness: 0.025,
    #            regime_at_entry: "trending_up", confidence_at_entry: 0.78,
    #            confirmations: {technical: true, sentiment: false}}
    signal_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Quality metrics
    confidence_at_entry: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    expected_pnl_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    # R-multiple: realized_pnl_pct / planned_risk_pct (planned_risk = entry → static SL distance)
    r_multiple: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)

    # One-line human-readable post-mortem of the trade. Generated at close (rule-based
    # today, LLM-upgradeable later). This is the "reflection" half of the learning loop:
    # the SignalMemory helper surfaces recent reflections back into future decisions.
    reflection: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=__import__("sqlalchemy").text("now()"),
        nullable=False,
    )

    agent = relationship("Agent")
