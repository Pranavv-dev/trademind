"""Point-in-time index membership — Pattern #3 from STRATEGY_RESEARCH.md.

Why this exists: NIFTY 50 today is not NIFTY 50 of 2018. Backtesting on the
current static list introduces ~9% annualized survivorship bias (arxiv 2603.19380
for Indian indices). Every entry to the index has historically replaced a name
that was kicked out for underperformance — the "losers" are invisible if you
just trade today's roster.

Schema: one row per (index_name, symbol, from_date, to_date). NULL to_date
means "still in the index as of now". The backtest engine queries this with
an `as_of_date` filter to reconstruct the universe that existed on each
historical bar.

Seed data: NSE publishes index reconstitutions twice a year (March/September
effective). The `seed_nifty50_membership` helper in scripts/ loads the
historical list; the backtest can also operate with current-only membership
as a fallback (with a warning logged).
"""

from datetime import date

from sqlalchemy import Date, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, UUIDMixin


class IndexMembership(Base, UUIDMixin):
    """A symbol's tenure in a named index over a date range."""

    __tablename__ = "index_membership"
    __table_args__ = (
        Index("ix_idx_member_lookup", "index_name", "symbol", "from_date", "to_date"),
        # A symbol can only have one "current" (open-ended) membership per index.
        UniqueConstraint("index_name", "symbol", "from_date", name="uq_index_member_period"),
    )

    index_name: Mapped[str] = mapped_column(String(30), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # NULL = ongoing
