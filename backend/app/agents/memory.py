"""SignalMemory — the feedback half of the learning loop.

Inspired by TauricResearch/TradingAgents' memory mechanism: after a trade closes,
its outcome is recorded (signal_outcomes table) along with a one-line reflection.
Before the system trades a symbol again, it looks at that symbol's recent history
and lets it bias the decision.

TradingAgents feeds past lessons into an LLM prompt. We do it DETERMINISTICALLY
instead — the memory produces a numeric multiplier applied to the ContextScore,
plus a human-readable reflection string for transparency. This keeps the feedback
loop free, reproducible, and unable to be "hallucinated away" by an LLM, while
still encoding the lesson: *stop force-feeding capital into a symbol that keeps
burning you, and lean into symbols that have been working.*

This is complementary to:
  - cooldown (intraday: blocks same-day re-entry after a close)
  - max_holding_days (exit side: caps how long a position lingers)
SignalMemory operates on a longer 10-30 day horizon: "has this symbol been good
or bad for us lately?"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger()

# Tunables
SHORT_WINDOW_DAYS = 10  # window for "keeps stopping out" detection
LONG_WINDOW_DAYS = 30  # window for cumulative R assessment
MIN_MULT = 0.4  # never zero a symbol out entirely (cooldown handles hard blocks)
MAX_MULT = 1.10  # modest reward for symbols that have been working


@dataclass
class SymbolMemory:
    symbol: str
    trades_30d: int
    losses_10d: int
    net_r_30d: float
    multiplier: float
    last_reflection: str | None


def _multiplier_from_stats(losses_10d: int, net_r_30d: float) -> float:
    """Map recent performance to a ContextScore multiplier in [MIN_MULT, MAX_MULT]."""
    mult = 1.0
    # Penalize symbols that keep stopping out — "stop force-feeding a loser"
    if losses_10d >= 3:
        mult *= 0.5
    elif losses_10d == 2:
        mult *= 0.7
    # Reward / penalize on cumulative 30-day R
    if net_r_30d <= -1.5:
        mult *= 0.6
    elif net_r_30d <= -0.5:
        mult *= 0.8
    elif net_r_30d >= 1.5:
        mult *= 1.10
    return max(MIN_MULT, min(MAX_MULT, mult))


class SignalMemory:
    """Reads recent signal_outcomes and turns them into per-symbol decision biases."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_symbol_memory(self, symbols: list[str] | None = None) -> dict[str, SymbolMemory]:
        """Return per-symbol memory for the given symbols (or all recently-traded).

        One grouped query over the 30-day window; the 10-day loss count is computed
        with a conditional aggregate so we don't issue two round-trips.
        """
        now = datetime.now(timezone.utc)
        long_cutoff = now - timedelta(days=LONG_WINDOW_DAYS)
        short_cutoff = now - timedelta(days=SHORT_WINDOW_DAYS)

        params: dict = {"long_cutoff": long_cutoff, "short_cutoff": short_cutoff}
        symbol_filter = ""
        if symbols:
            # Build a safe IN clause with bound params
            keys = []
            for i, s in enumerate(symbols):
                k = f"sym_{i}"
                params[k] = s
                keys.append(f":{k}")
            symbol_filter = f"AND symbol IN ({', '.join(keys)})"

        q = f"""
            SELECT
              symbol,
              COUNT(*)                                                   AS trades_30d,
              SUM(CASE WHEN net_pnl < 0 AND closed_at >= :short_cutoff
                       THEN 1 ELSE 0 END)                                AS losses_10d,
              COALESCE(SUM(r_multiple), 0)                               AS net_r_30d,
              (ARRAY_AGG(reflection ORDER BY closed_at DESC)
                 FILTER (WHERE reflection IS NOT NULL))[1]               AS last_reflection
            FROM signal_outcomes
            WHERE closed_at >= :long_cutoff
            {symbol_filter}
            GROUP BY symbol
        """
        result = await self.session.execute(text(q), params)
        out: dict[str, SymbolMemory] = {}
        for r in result.mappings().all():
            losses_10d = int(r["losses_10d"] or 0)
            net_r_30d = float(r["net_r_30d"] or 0.0)
            out[r["symbol"]] = SymbolMemory(
                symbol=r["symbol"],
                trades_30d=int(r["trades_30d"] or 0),
                losses_10d=losses_10d,
                net_r_30d=net_r_30d,
                multiplier=_multiplier_from_stats(losses_10d, net_r_30d),
                last_reflection=r["last_reflection"],
            )
        return out

    async def get_multipliers(self, symbols: list[str] | None = None) -> dict[str, float]:
        """Convenience: just the per-symbol score multipliers (default 1.0 if no history)."""
        mem = await self.get_symbol_memory(symbols)
        return {s: m.multiplier for s, m in mem.items()}


def build_reflection(
    *,
    symbol: str,
    close_reason: str,
    net_r: float | None,
    days_held: int,
    context_score: float | None,
    rs_score: float | None,
    sector_momentum: float | None,
    net_pnl: float,
) -> str:
    """Generate a one-line, deterministic post-mortem stored on the outcome row.

    Example:
      "TRAILING_STOP +0.04R after 7d | entry score 68, RS 1.24, sector +10.5% | momentum held"
    Kept rule-based (free + reproducible). Can be upgraded to an LLM one-liner later
    without changing the schema.
    """
    r_str = f"{net_r:+.2f}R" if net_r is not None else f"₹{net_pnl:+.0f}"
    bits = [f"{close_reason.upper()} {r_str} after {days_held}d"]

    ctx = []
    if context_score is not None:
        ctx.append(f"score {context_score:.0f}")
    if rs_score is not None:
        ctx.append(f"RS {rs_score:.2f}")
    if sector_momentum is not None:
        ctx.append(f"sector {sector_momentum:+.1%}")
    if ctx:
        bits.append("entry " + ", ".join(ctx))

    # Heuristic note
    note = None
    won = net_pnl > 0
    if close_reason == "stop_loss" and sector_momentum is not None and sector_momentum > 0:
        note = "stopped despite positive sector — likely stock-specific weakness"
    elif close_reason == "trailing_stop" and won:
        note = "trail locked gains as momentum faded"
    elif close_reason == "take_profit":
        note = "target hit cleanly"
    elif close_reason == "max_holding_days":
        note = "timed out — thesis never played"
    elif close_reason == "stop_loss":
        note = "stopped out"
    if note:
        bits.append(note)

    return " | ".join(bits)[:500]
