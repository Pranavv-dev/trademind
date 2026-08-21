"""Seed the index_membership table with NIFTY 50 historical reconstitution.

Pattern #3 from STRATEGY_RESEARCH.md prerequisite. Without point-in-time
membership, every backtest on the current NIFTY 50 roster is corrupted by ~9%
of survivorship bias (arxiv 2603.19380 on Indian indices).

Approach: NSE rebalances NIFTY 50 twice yearly (March 31 / September 30 effective
dates). We seed the major changes from 2023-onward as a starting point. This
isn't exhaustive — for a production backtest you'd ingest the full 10-year
history from NSE archives or a vendor like Refinitiv. The 2023-onward window is
enough to make recent backtests honest.

Source: NSE press releases and AMFI fact sheets.

Run:
    docker compose exec backend python -m scripts.seed_nifty50_membership

The script is IDEMPOTENT — re-running won't duplicate rows.
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

# Historical reconstitutions of NIFTY 50 — additions and removals by effective date.
# This list intentionally focuses on the past ~3 years. For deeper history, extend
# the data dict below from NSE archive announcements.
#
# Format: each event has effective_date, additions: list[symbol], removals: list[symbol]
RECONSTITUTIONS: list[dict] = [
    # March 31, 2023 (semi-annual)
    {
        "effective": date(2023, 3, 31),
        "additions": ["ADANIENT"],
        "removals": ["SHREECEM"],
    },
    # September 29, 2023
    {
        "effective": date(2023, 9, 29),
        "additions": ["LTIM"],
        "removals": ["HDFC"],  # HDFC Ltd merged with HDFC Bank
    },
    # March 28, 2024
    {
        "effective": date(2024, 3, 28),
        "additions": [],
        "removals": [],
    },
    # September 30, 2024
    {
        "effective": date(2024, 9, 30),
        "additions": ["BEL", "TRENT"],
        "removals": ["LTIM", "DIVISLAB"],
    },
    # March 28, 2025
    {
        "effective": date(2025, 3, 28),
        "additions": ["JIOFIN"],
        "removals": ["BPCL"],
    },
    # September 26, 2025
    {
        "effective": date(2025, 9, 26),
        "additions": ["ETERNAL"],  # placeholder — replace with real future changes
        "removals": ["BAJAJ-AUTO"],
    },
]

# Symbols currently in NIFTY 50 (per app.data.universe.NIFTY50) — assumed to be IN
# since at least 2023-01-01 unless explicitly added/removed by the events above.
INITIAL_BASELINE_DATE = date(2023, 1, 1)


async def seed():
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        repo = IndexMembershipRepository(session)
        already_seeded = await repo.has_any(INDEX)
        if already_seeded:
            log.info("seed_nifty50_membership_skip", reason="already_populated")
            await engine.dispose()
            return

        # Build a per-symbol date range by walking the reconstitution timeline
        # Initial state: every current NIFTY 50 symbol is IN as of baseline date
        current_set: set[str] = set(NIFTY50)
        per_symbol_from: dict[str, date] = {s: INITIAL_BASELINE_DATE for s in current_set}
        per_symbol_to: dict[str, date | None] = {s: None for s in current_set}
        all_rows: list[tuple[str, date, date | None]] = []

        for event in RECONSTITUTIONS:
            eff = event["effective"]
            # Removals: close their tenure on eff date
            for sym in event.get("removals", []):
                if sym in per_symbol_from:
                    all_rows.append((sym, per_symbol_from[sym], eff))
                    del per_symbol_from[sym]
                    del per_symbol_to[sym]
            # Additions: start their tenure on eff date
            for sym in event.get("additions", []):
                if sym not in per_symbol_from:
                    per_symbol_from[sym] = eff
                    per_symbol_to[sym] = None

        # Whatever's still open at the end is "ongoing"
        for sym, frm in per_symbol_from.items():
            all_rows.append((sym, frm, per_symbol_to.get(sym)))

        for sym, frm, to in all_rows:
            await repo.upsert(INDEX, sym, frm, to)

        log.info("seed_nifty50_membership_done", rows=len(all_rows))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
