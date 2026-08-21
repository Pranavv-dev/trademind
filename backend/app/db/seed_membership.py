"""Seed index_membership with NIFTY 50 reconstitution history.

Importable inside the backend container (unlike the root scripts/ copy, which
isn't part of the image). Run:

    docker compose exec backend python -m app.db.seed_membership

Idempotent — re-running won't duplicate rows. Only needed for point-in-time
backtests; live daily trading does not depend on it.
"""

import asyncio
from datetime import date

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.data.universe import NIFTY50
from app.db.repositories.index_membership_repo import IndexMembershipRepository

log = structlog.get_logger()

INDEX = "NIFTY50"
INITIAL_BASELINE_DATE = date(2023, 1, 1)

# NIFTY 50 reconstitutions (additions/removals by effective date). Focused on the
# past ~3 years; extend from NSE archives for deeper history.
RECONSTITUTIONS: list[dict] = [
    {"effective": date(2023, 3, 31), "additions": ["ADANIENT"], "removals": ["SHREECEM"]},
    {"effective": date(2023, 9, 29), "additions": ["LTIM"], "removals": ["HDFC"]},
    {"effective": date(2024, 3, 28), "additions": [], "removals": []},
    {
        "effective": date(2024, 9, 30),
        "additions": ["BEL", "TRENT"],
        "removals": ["LTIM", "DIVISLAB"],
    },
    {"effective": date(2025, 3, 28), "additions": ["JIOFIN"], "removals": ["BPCL"]},
    {"effective": date(2025, 9, 26), "additions": ["ETERNAL"], "removals": ["BAJAJ-AUTO"]},
]


async def seed() -> dict:
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            repo = IndexMembershipRepository(session)
            if await repo.has_any(INDEX):
                log.info("seed_membership_skip", reason="already_populated")
                return {"status": "skipped", "reason": "already_populated"}

            per_symbol_from: dict[str, date] = {s: INITIAL_BASELINE_DATE for s in NIFTY50}
            rows: list[tuple[str, date, date | None]] = []

            for ev in RECONSTITUTIONS:
                eff = ev["effective"]
                for sym in ev.get("removals", []):
                    if sym in per_symbol_from:
                        rows.append((sym, per_symbol_from.pop(sym), eff))
                for sym in ev.get("additions", []):
                    per_symbol_from.setdefault(sym, eff)

            for sym, frm in per_symbol_from.items():
                rows.append((sym, frm, None))

            for sym, frm, to in rows:
                await repo.upsert(INDEX, sym, frm, to)

            log.info("seed_membership_done", rows=len(rows))
            return {"status": "ok", "rows": len(rows)}
    finally:
        await engine.dispose()


if __name__ == "__main__":
    print(asyncio.run(seed()))
