"""Repository for the index_membership table — backtest universe queries.

Used by the walk-forward engine to answer "what was NIFTY 50 on YYYY-MM-DD?"
without leaking today's roster into a historical signal computation.
"""

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.index_membership import IndexMembership


class IndexMembershipRepository:
    """Read/write helpers for index_membership."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def universe_as_of(self, index_name: str, as_of: date) -> list[str]:
        """Return the set of symbols that were in `index_name` on `as_of`.

        Rules:
            from_date <= as_of  AND  (to_date IS NULL OR to_date >= as_of)
        """
        stmt = (
            select(IndexMembership.symbol)
            .where(IndexMembership.index_name == index_name)
            .where(IndexMembership.from_date <= as_of)
            .where(
                or_(
                    IndexMembership.to_date.is_(None),
                    IndexMembership.to_date >= as_of,
                )
            )
        )
        result = await self.session.execute(stmt)
        return sorted({row[0] for row in result.all()})

    async def rows_in_range(
        self, index_name: str, start: date, end: date
    ) -> list[tuple[str, date, date | None]]:
        """Return every tenure overlapping [start, end] as (symbol, from_date, to_date).

        A daily backtest needs the universe on ~250 dates per year. Rather than
        issuing one `universe_as_of` query per simulated day, callers can load the
        overlapping tenures once and resolve membership in memory.

        Overlap rule: from_date <= end AND (to_date IS NULL OR to_date >= start).
        """
        stmt = (
            select(
                IndexMembership.symbol,
                IndexMembership.from_date,
                IndexMembership.to_date,
            )
            .where(IndexMembership.index_name == index_name)
            .where(IndexMembership.from_date <= end)
            .where(
                or_(
                    IndexMembership.to_date.is_(None),
                    IndexMembership.to_date >= start,
                )
            )
        )
        result = await self.session.execute(stmt)
        return [(r[0], r[1], r[2]) for r in result.all()]

    async def upsert(
        self, index_name: str, symbol: str, from_date: date, to_date: date | None = None
    ) -> None:
        """Insert or update a membership row. Idempotent on (index_name, symbol, from_date)."""
        existing = await self.session.execute(
            select(IndexMembership).where(
                IndexMembership.index_name == index_name,
                IndexMembership.symbol == symbol,
                IndexMembership.from_date == from_date,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            row = IndexMembership(
                index_name=index_name,
                symbol=symbol,
                from_date=from_date,
                to_date=to_date,
            )
            self.session.add(row)
        else:
            row.to_date = to_date
        await self.session.commit()

    async def has_any(self, index_name: str) -> bool:
        """True if any membership rows exist for index_name. Used as a fallback signal."""
        stmt = select(IndexMembership.id).where(IndexMembership.index_name == index_name).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None
