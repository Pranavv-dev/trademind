"""Expectancy and R-multiple statistics — the only numbers that say if the system has edge.

Pattern #1 from STRATEGY_RESEARCH.md, completion half. The forensic audit and
research both flagged "PnL in rupees, not R-multiples" as an anti-pattern. Rupee
P&L can paper over an asymmetric loss distribution (small wins, big losses).
R-multiples and expectancy expose it.

Expectancy = (P(win) * avg_R_win) - (P(loss) * |avg_R_loss|)

A positive expectancy ≥ 0.2 R/trade after costs is the practical threshold for a
deployable strategy. Anything less than that and the system is path-dependent
noise.

Profit Factor = sum(winning trades) / |sum(losing trades)|
  PF > 1.5 = decent
  PF > 2.0 = good
  PF > 3.0 = exceptional (rare and usually overfit)

Payoff Ratio = avg_win / |avg_loss|
  Combined with hit rate, gives the engine of expectancy.

These computations read from signal_outcomes (populated on every position close
since the May 30 fixes) and are exposed via /api/signal-performance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ExpectancyStats:
    agent_id: str | None
    agent_name: str | None
    strategy_type: str | None
    window_days: int
    trades: int
    wins: int
    losses: int
    breakevens: int
    hit_rate: float
    avg_win_r: float | None
    avg_loss_r: float | None
    payoff_ratio: float | None
    profit_factor: float | None
    expectancy_r: float | None  # in R units (preferred)
    expectancy_rupees: float  # in absolute rupees
    total_net_pnl: float
    total_gross_pnl: float
    total_charges: float
    largest_win_r: float | None
    largest_loss_r: float | None
    avg_days_held: float | None
    deployable: bool  # expectancy_r >= 0.2 after costs

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


async def compute_expectancy(
    session: AsyncSession,
    days: int = 30,
    agent_name: str | None = None,
    strategy_type: str | None = None,
) -> list[ExpectancyStats]:
    """Compute expectancy statistics, grouped by (agent_name, strategy_type).

    Filters:
        days: lookback window (default 30 days, max practical is full history)
        agent_name: restrict to one named agent
        strategy_type: restrict to one strategy

    Returns:
        One ExpectancyStats per (agent_name, strategy_type) group with trades > 0.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    where = ["closed_at >= :cutoff"]
    params = {"cutoff": cutoff}
    if agent_name:
        where.append("agent_name = :agent_name")
        params["agent_name"] = agent_name
    if strategy_type:
        where.append("strategy_type = :strategy_type")
        params["strategy_type"] = strategy_type
    where_clause = " AND ".join(where)

    raw_q = f"""
        SELECT
          agent_id, agent_name, strategy_type,
          COUNT(*)                                              AS trades,
          SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END)          AS wins,
          SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END)          AS losses,
          SUM(CASE WHEN net_pnl = 0 THEN 1 ELSE 0 END)          AS breakevens,
          AVG(CASE WHEN r_multiple > 0 THEN r_multiple END)     AS avg_win_r,
          AVG(CASE WHEN r_multiple < 0 THEN r_multiple END)     AS avg_loss_r,
          MAX(r_multiple)                                       AS largest_win_r,
          MIN(r_multiple)                                       AS largest_loss_r,
          SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END)    AS gross_wins,
          SUM(CASE WHEN net_pnl < 0 THEN -net_pnl ELSE 0 END)   AS gross_losses,
          SUM(net_pnl)                                          AS total_net_pnl,
          SUM(gross_pnl)                                        AS total_gross_pnl,
          SUM(total_charges)                                    AS total_charges,
          AVG(days_held)                                        AS avg_days_held
        FROM signal_outcomes
        WHERE {where_clause}
        GROUP BY agent_id, agent_name, strategy_type
        HAVING COUNT(*) > 0
        ORDER BY total_net_pnl DESC
    """
    result = await session.execute(text(raw_q), params)
    rows = result.mappings().all()

    out: list[ExpectancyStats] = []
    for r in rows:
        trades = int(r["trades"])
        wins = int(r["wins"] or 0)
        losses = int(r["losses"] or 0)
        breakevens = int(r["breakevens"] or 0)
        hit_rate = wins / trades if trades > 0 else 0.0

        avg_win_r = float(r["avg_win_r"]) if r["avg_win_r"] is not None else None
        avg_loss_r = float(r["avg_loss_r"]) if r["avg_loss_r"] is not None else None
        gross_wins = float(r["gross_wins"] or 0)
        gross_losses = float(r["gross_losses"] or 0)

        payoff_ratio = (
            (avg_win_r / abs(avg_loss_r))
            if (avg_win_r and avg_loss_r and avg_loss_r != 0)
            else None
        )
        profit_factor = (
            (gross_wins / gross_losses)
            if gross_losses > 0
            else (float("inf") if gross_wins > 0 else None)
        )

        # Expectancy in R: hit_rate × avg_win_r + (1 - hit_rate) × avg_loss_r
        expectancy_r = None
        if avg_win_r is not None and avg_loss_r is not None:
            expectancy_r = hit_rate * avg_win_r + (1 - hit_rate) * avg_loss_r
        elif avg_win_r is not None and losses == 0:
            expectancy_r = avg_win_r
        elif avg_loss_r is not None and wins == 0:
            expectancy_r = avg_loss_r

        total_net = float(r["total_net_pnl"] or 0)
        expectancy_rupees = total_net / trades if trades > 0 else 0.0

        deployable = expectancy_r is not None and expectancy_r >= 0.2 and trades >= 30

        out.append(
            ExpectancyStats(
                agent_id=str(r["agent_id"]),
                agent_name=r["agent_name"],
                strategy_type=r["strategy_type"],
                window_days=days,
                trades=trades,
                wins=wins,
                losses=losses,
                breakevens=breakevens,
                hit_rate=round(hit_rate, 4),
                avg_win_r=round(avg_win_r, 4) if avg_win_r is not None else None,
                avg_loss_r=round(avg_loss_r, 4) if avg_loss_r is not None else None,
                payoff_ratio=round(payoff_ratio, 4) if payoff_ratio is not None else None,
                profit_factor=round(profit_factor, 4)
                if profit_factor is not None and profit_factor != float("inf")
                else (None if profit_factor is None else 999.0),
                expectancy_r=round(expectancy_r, 4) if expectancy_r is not None else None,
                expectancy_rupees=round(expectancy_rupees, 2),
                total_net_pnl=round(total_net, 2),
                total_gross_pnl=round(float(r["total_gross_pnl"] or 0), 2),
                total_charges=round(float(r["total_charges"] or 0), 2),
                largest_win_r=float(r["largest_win_r"]) if r["largest_win_r"] is not None else None,
                largest_loss_r=float(r["largest_loss_r"])
                if r["largest_loss_r"] is not None
                else None,
                avg_days_held=float(r["avg_days_held"]) if r["avg_days_held"] is not None else None,
                deployable=deployable,
            )
        )
    return out


async def compute_aggregate_expectancy(
    session: AsyncSession, days: int = 30
) -> ExpectancyStats | None:
    """System-wide expectancy across ALL agents in the window. Returns a single row
    or None if no closed trades exist in the window.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    raw_q = """
        SELECT
          COUNT(*)                                              AS trades,
          SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END)          AS wins,
          SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END)          AS losses,
          SUM(CASE WHEN net_pnl = 0 THEN 1 ELSE 0 END)          AS breakevens,
          AVG(CASE WHEN r_multiple > 0 THEN r_multiple END)     AS avg_win_r,
          AVG(CASE WHEN r_multiple < 0 THEN r_multiple END)     AS avg_loss_r,
          MAX(r_multiple)                                       AS largest_win_r,
          MIN(r_multiple)                                       AS largest_loss_r,
          SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END)    AS gross_wins,
          SUM(CASE WHEN net_pnl < 0 THEN -net_pnl ELSE 0 END)   AS gross_losses,
          SUM(net_pnl)                                          AS total_net_pnl,
          SUM(gross_pnl)                                        AS total_gross_pnl,
          SUM(total_charges)                                    AS total_charges,
          AVG(days_held)                                        AS avg_days_held
        FROM signal_outcomes
        WHERE closed_at >= :cutoff
    """
    result = await session.execute(text(raw_q), {"cutoff": cutoff})
    r = result.mappings().first()
    if not r or not r["trades"]:
        return None

    trades = int(r["trades"])
    wins = int(r["wins"] or 0)
    losses = int(r["losses"] or 0)
    hit_rate = wins / trades if trades > 0 else 0.0
    avg_win_r = float(r["avg_win_r"]) if r["avg_win_r"] is not None else None
    avg_loss_r = float(r["avg_loss_r"]) if r["avg_loss_r"] is not None else None

    expectancy_r = None
    if avg_win_r is not None and avg_loss_r is not None:
        expectancy_r = hit_rate * avg_win_r + (1 - hit_rate) * avg_loss_r

    gross_wins = float(r["gross_wins"] or 0)
    gross_losses = float(r["gross_losses"] or 0)
    pf = (gross_wins / gross_losses) if gross_losses > 0 else (999.0 if gross_wins > 0 else None)
    payoff = (avg_win_r / abs(avg_loss_r)) if (avg_win_r and avg_loss_r) else None

    return ExpectancyStats(
        agent_id=None,
        agent_name="SYSTEM",
        strategy_type=None,
        window_days=days,
        trades=trades,
        wins=wins,
        losses=losses,
        breakevens=int(r["breakevens"] or 0),
        hit_rate=round(hit_rate, 4),
        avg_win_r=round(avg_win_r, 4) if avg_win_r is not None else None,
        avg_loss_r=round(avg_loss_r, 4) if avg_loss_r is not None else None,
        payoff_ratio=round(payoff, 4) if payoff is not None else None,
        profit_factor=round(pf, 4) if pf is not None else None,
        expectancy_r=round(expectancy_r, 4) if expectancy_r is not None else None,
        expectancy_rupees=round(float(r["total_net_pnl"] or 0) / max(trades, 1), 2),
        total_net_pnl=round(float(r["total_net_pnl"] or 0), 2),
        total_gross_pnl=round(float(r["total_gross_pnl"] or 0), 2),
        total_charges=round(float(r["total_charges"] or 0), 2),
        largest_win_r=float(r["largest_win_r"]) if r["largest_win_r"] is not None else None,
        largest_loss_r=float(r["largest_loss_r"]) if r["largest_loss_r"] is not None else None,
        avg_days_held=float(r["avg_days_held"]) if r["avg_days_held"] is not None else None,
        deployable=(expectancy_r is not None and expectancy_r >= 0.2 and trades >= 30),
    )
