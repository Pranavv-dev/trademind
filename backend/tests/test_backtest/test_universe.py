"""Point-in-time universe resolution for the ProactiveAgent backtest.

Scoring every simulated day against today's NIFTY 50 is survivorship bias: every
current member survived, and the names dropped for underperformance are invisible.
These tests pin the behaviour that removes it.
"""

from datetime import date

import pytest

from app.backtest.proactive_backtest import (
    INDEX_NAME,
    _fixed_universe,
    _membership_universe,
)
from app.db.repositories.index_membership_repo import IndexMembershipRepository


# ── In-memory resolution ──


def test_fixed_universe_ignores_the_date():
    u = _fixed_universe(["RELIANCE", "TCS"])
    assert u(date(2021, 1, 1)) == ["RELIANCE", "TCS"]
    assert u(date(2026, 1, 1)) == ["RELIANCE", "TCS"]


def test_membership_universe_excludes_a_name_before_it_joined():
    """The whole point: a 2024 joiner must not be scored on a 2022 bar."""
    tenures = [
        ("RELIANCE", date(2020, 1, 1), None),
        ("JIOFIN", date(2025, 3, 28), None),
    ]
    u = _membership_universe(tenures)
    assert u(date(2022, 6, 1)) == ["RELIANCE"]
    assert u(date(2025, 6, 1)) == ["JIOFIN", "RELIANCE"]


def test_membership_universe_includes_a_name_that_was_later_dropped():
    """The survivorship half: a delisted/demoted name must still appear in its era."""
    tenures = [
        ("RELIANCE", date(2020, 1, 1), None),
        ("BPCL", date(2020, 1, 1), date(2025, 3, 28)),
    ]
    u = _membership_universe(tenures)
    assert "BPCL" in u(date(2023, 6, 1))
    assert "BPCL" not in u(date(2025, 6, 1))


def test_membership_universe_boundaries_are_inclusive():
    tenures = [("X", date(2024, 1, 1), date(2024, 12, 31))]
    u = _membership_universe(tenures)
    assert u(date(2023, 12, 31)) == []
    assert u(date(2024, 1, 1)) == ["X"]
    assert u(date(2024, 12, 31)) == ["X"]
    assert u(date(2025, 1, 1)) == []


def test_membership_universe_is_sorted_and_deduplicated():
    """Re-entry to the index produces two tenures for one symbol."""
    tenures = [
        ("ZZZ", date(2020, 1, 1), None),
        ("AAA", date(2020, 1, 1), date(2022, 1, 1)),
        ("AAA", date(2023, 1, 1), None),
    ]
    u = _membership_universe(tenures)
    assert u(date(2024, 1, 1)) == ["AAA", "ZZZ"]
    assert u(date(2022, 6, 1)) == ["ZZZ"]


def test_membership_universe_memoises_per_date():
    """A daily backtest asks for the same roster ~250x/year; resolve it once."""
    calls = {"n": 0}

    class CountingList(list):
        def __iter__(self):
            calls["n"] += 1
            return super().__iter__()

    u = _membership_universe(CountingList([("A", date(2020, 1, 1), None)]))
    u(date(2024, 1, 1))
    u(date(2024, 1, 1))
    u(date(2024, 1, 1))
    assert calls["n"] == 1


# ── Repository ──


async def test_rows_in_range_returns_only_overlapping_tenures(db_session):
    repo = IndexMembershipRepository(db_session)
    await repo.upsert(INDEX_NAME, "INWINDOW", date(2023, 1, 1), None)
    await repo.upsert(INDEX_NAME, "ENDEDBEFORE", date(2018, 1, 1), date(2019, 1, 1))
    await repo.upsert(INDEX_NAME, "STARTEDAFTER", date(2030, 1, 1), None)
    await db_session.commit()

    rows = await repo.rows_in_range(INDEX_NAME, date(2024, 1, 1), date(2025, 1, 1))
    symbols = {r[0] for r in rows}
    assert "INWINDOW" in symbols
    assert "ENDEDBEFORE" not in symbols
    assert "STARTEDAFTER" not in symbols


async def test_rows_in_range_and_universe_as_of_agree(db_session):
    """The in-memory resolver must match the SQL one, or the fix is a no-op."""
    repo = IndexMembershipRepository(db_session)
    await repo.upsert(INDEX_NAME, "OLD", date(2020, 1, 1), date(2024, 6, 30))
    await repo.upsert(INDEX_NAME, "NEW", date(2024, 7, 1), None)
    await db_session.commit()

    for probe in (date(2023, 1, 1), date(2024, 6, 30), date(2024, 7, 1), date(2025, 1, 1)):
        rows = await repo.rows_in_range(INDEX_NAME, probe, probe)
        assert _membership_universe(rows)(probe) == await repo.universe_as_of(INDEX_NAME, probe)


async def test_has_any_is_false_on_an_empty_table(db_session):
    """This is the switch that decides point-in-time vs the biased fallback."""
    repo = IndexMembershipRepository(db_session)
    assert await repo.has_any(INDEX_NAME) is False


# ── Coverage gap ──


def test_membership_universe_is_empty_before_coverage_begins():
    """The failure mode the coverage guard exists to catch.

    Seed data baselined at 2023-01-01 resolves to NO members for 2021-2022, so
    those bars trade nothing while the run still reports the full period.
    """
    tenures = [("RELIANCE", date(2023, 1, 1), None)]
    u = _membership_universe(tenures)
    assert u(date(2022, 6, 1)) == []
    assert u(date(2023, 6, 1)) == ["RELIANCE"]


async def test_backtest_flags_a_membership_coverage_gap(db_session):
    """A truncated window must be reported on the result, not left implicit."""
    from app.backtest.proactive_backtest import BTConfig, run_proactive_backtest

    repo = IndexMembershipRepository(db_session)
    await repo.upsert(INDEX_NAME, "RELIANCE", date(2023, 1, 1), None)
    await db_session.commit()

    result = await run_proactive_backtest(
        db_session, start=date(2021, 1, 1), end=date(2023, 12, 31), cfg=BTConfig()
    )
    assert result["universe_method"] == "point_in_time"
    assert result["universe_coverage_start"] == "2023-01-01"
    assert "traded nothing" in result["universe_warning"]


async def test_backtest_reports_fallback_when_membership_is_unseeded(db_session):
    """No membership rows => today's roster, and the result says so."""
    from app.backtest.proactive_backtest import BTConfig, run_proactive_backtest

    result = await run_proactive_backtest(
        db_session, start=date(2023, 1, 1), end=date(2023, 3, 31), cfg=BTConfig()
    )
    assert result["universe_method"] == "current_only_fallback"
    assert "universe_warning" not in result


async def test_backtest_marks_explicit_symbols_as_such(db_session):
    """An explicit symbol list bypasses membership entirely; label it honestly."""
    from app.backtest.proactive_backtest import BTConfig, run_proactive_backtest

    repo = IndexMembershipRepository(db_session)
    await repo.upsert(INDEX_NAME, "RELIANCE", date(2020, 1, 1), None)
    await db_session.commit()

    result = await run_proactive_backtest(
        db_session,
        start=date(2023, 1, 1),
        end=date(2023, 3, 31),
        cfg=BTConfig(),
        symbols=["TCS"],
    )
    assert result["universe_method"] == "explicit_symbols"
