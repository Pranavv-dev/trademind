"""Head-to-head: mean-reversion strategy vs NIFTY-50 buy-and-hold.

Answers the only question that matters — is running this bot better than just
holding an index fund? Reports return, CAGR, max drawdown, and Sharpe for BOTH,
over the same window and universe, so the comparison is apples-to-apples.

    docker compose exec backend python -m app.backtest.compare_vs_benchmark \
        --start 2021-01-01 --end 2026-06-20
"""

from __future__ import annotations

import argparse
import asyncio
import math
from datetime import date

from app.backtest.proactive_backtest import AsOfCandleRepo, BTConfig, run_proactive_backtest
from app.data.universe import NIFTY50


def _metrics_from_index(index_series: list[float], years: float) -> dict:
    """Return/CAGR/maxDD/Sharpe from a daily portfolio-value index (starts at 1.0)."""
    if len(index_series) < 3:
        return {}
    total_return = index_series[-1] / index_series[0] - 1.0
    cagr = (index_series[-1] / index_series[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    # daily returns
    rets = [
        index_series[i] / index_series[i - 1] - 1
        for i in range(1, len(index_series))
        if index_series[i - 1] > 0
    ]
    sharpe = 0.0
    if rets:
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / len(rets)
        sd = var**0.5
        if sd > 0:
            sharpe = (m / sd) * math.sqrt(252)
    peak, max_dd = -1e18, 0.0
    for v in index_series:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    return {
        "return_pct": round(100 * total_return, 1),
        "cagr_pct": round(100 * cagr, 1),
        "max_drawdown_pct": round(100 * max_dd, 1),
        "sharpe": round(sharpe, 2),
    }


async def buy_and_hold_benchmark(session, start: date, end: date) -> dict:
    """Equal-weight buy-and-hold of the NIFTY-50 universe (proxy for an index fund).

    Portfolio value index(d) = mean over symbols of close(d)/close(start).  This is
    equal-rupee allocation at `start`, held to `end` (weights drift, no rebalance).
    """
    from app.db.repositories.candle_repo import CandleRepository

    symbols = list(NIFTY50)
    asof = await AsOfCandleRepo.preload(CandleRepository(session), symbols, start, end)

    # ascending (date, close) per symbol
    series: dict[str, dict[date, float]] = {}
    for sym, rows in asof._rows.items():
        series[sym] = {c.time.date(): float(c.close) for c in rows}

    dates = asof.trading_dates(start, end)
    if not dates:
        return {}
    start_d = dates[0]

    # symbols with a valid starting price
    base = {
        s: series[s][start_d] for s in series if start_d in series[s] and series[s][start_d] > 0
    }

    index_series = []
    for d in dates:
        ratios = [series[s][d] / base[s] for s in base if d in series[s]]
        if ratios:
            index_series.append(sum(ratios) / len(ratios))
    years = (dates[-1] - dates[0]).days / 365.25
    m = _metrics_from_index(index_series, years)
    m["symbols"] = len(base)
    m["years"] = round(years, 2)
    return m


async def _main(args):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.config import settings

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s1:
            strat = await run_proactive_backtest(
                s1,
                start,
                end,
                cfg=BTConfig(invert=True, trail_after_r=2.0, apply_costs=True),
            )
        async with factory() as s2:
            bench = await buy_and_hold_benchmark(s2, start, end)
    finally:
        await engine.dispose()

    def row(label, d):
        return (
            f"  {label:<26}{str(d.get('return_pct', '-')) + '%':>10}"
            f"{str(d.get('cagr_pct', '-')) + '%':>9}"
            f"{str(d.get('max_drawdown_pct', '-')) + '%':>10}"
            f"{str(d.get('sharpe', '-')):>9}"
        )

    print(f"\n=== Strategy vs NIFTY-50 buy-and-hold  ({args.start} → {args.end}) ===")
    print(f"  {'':<26}{'Return':>10}{'CAGR':>9}{'MaxDD':>10}{'Sharpe':>9}")
    print(row("Mean-reversion strategy", strat))
    print(row("NIFTY-50 buy & hold", bench))
    print(
        f"\n  strategy trades: {strat.get('trades')}, win%: {strat.get('win_rate_pct')}, "
        f"benchmark symbols: {bench.get('symbols')}, years: {bench.get('years')}"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    asyncio.run(_main(p.parse_args()))
