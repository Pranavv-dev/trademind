"""Market-neutral long-short backtest of the mean-reversion signal.

The definitive test of "can we beat the index." Our factor study showed BOTH tails
of 52-week proximity predict (oversold rises, overbought falls); a long-only book
harvests only the long half. This tests the full dollar-neutral spread:

  every H trading days: LONG the N most-oversold names, SHORT the N most-overbought,
  equal-weight each leg, hold H days, rebalance.

Reports cumulative return, annualized Sharpe, max drawdown, and — the number that
matters for a market-neutral strategy — CORRELATION to the equal-weight market.
A real stat-arb edge shows Sharpe clearly > the index's ~1.1 AND correlation ~0.

    docker compose exec backend python -m app.backtest.longshort_backtest \
        --start 2021-01-01 --end 2026-06-20 --hold 21 --n 10
"""

from __future__ import annotations

import argparse
import asyncio
import math
from datetime import date

from app.backtest.proactive_backtest import AsOfCandleRepo
from app.data.context_scorer import ContextScorer
from app.data.universe import NIFTY50


def _sharpe(period_rets: list[float], periods_per_year: float) -> float:
    if len(period_rets) < 3:
        return 0.0
    m = sum(period_rets) / len(period_rets)
    var = sum((r - m) ** 2 for r in period_rets) / len(period_rets)
    sd = var**0.5
    return (m / sd) * math.sqrt(periods_per_year) if sd > 0 else 0.0


def _cum_and_dd(period_rets: list[float]) -> tuple[float, float]:
    """Compounded cumulative return and max drawdown from a series of period returns."""
    equity = [1.0]
    for r in period_rets:
        equity.append(equity[-1] * (1 + r))
    peak, max_dd = -1e18, 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    return equity[-1] - 1.0, max_dd


def _corr(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    return cov / (va**0.5 * vb**0.5) if va > 0 and vb > 0 else 0.0


async def run_longshort(
    session,
    start: date,
    end: date,
    hold: int,
    n: int,
    one_way_bps: float,
    symbols: list | None = None,
    momentum: bool = False,
) -> dict:
    from app.db.repositories.candle_repo import CandleRepository

    symbols = symbols or list(NIFTY50)
    asof = await AsOfCandleRepo.preload(CandleRepository(session), symbols, start, end)
    scorer = ContextScorer(candle_repo=asof)

    # ascending (date, close) per symbol for forward returns
    closes: dict[str, dict[date, float]] = {
        s: {c.time.date(): float(c.close) for c in rows} for s, rows in asof._rows.items()
    }
    all_dates = asof.trading_dates(start, end)
    idx_of = {d: i for i, d in enumerate(all_dates)}

    def fwd_ret(sym: str, d: date, h: int) -> float | None:
        i = idx_of.get(d)
        if i is None or i + h >= len(all_dates):
            return None
        d2 = all_dates[i + h]
        c0, c1 = closes[sym].get(d), closes[sym].get(d2)
        if c0 and c1 and c0 > 0:
            return c1 / c0 - 1.0
        return None

    rebal_dates = all_dates[::hold]
    # per-leg round-trip cost as a return drag: buy+sell each leg once per hold period
    rt_cost = 2 * one_way_bps / 10_000.0

    ls_gross, ls_net, long_only, short_only, market = [], [], [], [], []

    for d in rebal_dates:
        asof.as_of = d
        scores = await scorer.score_universe(symbols=symbols)
        if len(scores) < 2 * n + 5:
            continue
        ranked = sorted(scores.values(), key=lambda s: s.proximity_52w)
        if momentum:
            # momentum direction: LONG strongest (high proximity), SHORT weakest
            longs = [s.symbol for s in ranked[-n:]]
            shorts = [s.symbol for s in ranked[:n]]
        else:
            # mean-reversion direction: LONG oversold, SHORT overbought
            longs = [s.symbol for s in ranked[:n]]
            shorts = [s.symbol for s in ranked[-n:]]

        long_r = [r for s in longs if (r := fwd_ret(s, d, hold)) is not None]
        short_r = [r for s in shorts if (r := fwd_ret(s, d, hold)) is not None]
        mkt_r = [r for s in scores if (r := fwd_ret(s, d, hold)) is not None]
        if not long_r or not short_r or not mkt_r:
            continue

        long_mean = sum(long_r) / len(long_r)
        short_mean = sum(short_r) / len(short_r)
        market_mean = sum(mkt_r) / len(mkt_r)
        spread = long_mean - short_mean  # long gains + short gains (price falls)
        ls_gross.append(spread)
        ls_net.append(spread - 2 * rt_cost)  # both legs turn over
        long_only.append(long_mean - rt_cost)
        short_only.append(-short_mean - rt_cost)
        market.append(market_mean)

    ppy = 252.0 / hold

    def pack(series):
        cum, dd = _cum_and_dd(series)
        return {
            "cum_return_pct": round(100 * cum, 1),
            "ann_sharpe": round(_sharpe(series, ppy), 2),
            "max_dd_pct": round(100 * dd, 1),
        }

    out = {
        "periods": len(ls_net),
        "hold_days": hold,
        "n_per_leg": n,
        "long_short_net": pack(ls_net),
        "long_short_gross": pack(ls_gross),
        "long_only_net": pack(long_only),
        "market_eqw": pack(market),
        "corr_ls_to_market": round(_corr(ls_net, market), 2),
    }
    return out


async def _main(args):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.config import settings

    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        from app.data.universe import get_universe

        syms = get_universe(args.universe) if args.universe else None
        async with factory() as s:
            r = await run_longshort(
                s,
                date.fromisoformat(args.start),
                date.fromisoformat(args.end),
                hold=args.hold,
                n=args.n,
                one_way_bps=args.cost_bps,
                symbols=syms,
                momentum=args.momentum,
            )
    finally:
        await engine.dispose()

    print(
        f"\n=== Market-neutral long-short  (hold={r['hold_days']}d, {r['n_per_leg']}/leg, "
        f"{r['periods']} periods, {args.start}→{args.end}) ==="
    )
    print(f"  {'':<22}{'CumRet':>10}{'AnnSharpe':>11}{'MaxDD':>9}")
    for k in ("long_short_net", "long_short_gross", "long_only_net", "market_eqw"):
        d = r[k]
        print(
            f"  {k:<22}{str(d['cum_return_pct']) + '%':>10}"
            f"{d['ann_sharpe']:>11}{str(d['max_dd_pct']) + '%':>9}"
        )
    print(
        f"\n  correlation(long-short, market) = {r['corr_ls_to_market']}  (near 0 = market-neutral)"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--hold", type=int, default=21, help="holding/rebalance period in trading days")
    p.add_argument("--n", type=int, default=10, help="names per leg")
    p.add_argument(
        "--cost-bps",
        type=float,
        default=20.0,
        dest="cost_bps",
        help="one-way cost per leg in bps (slippage+charges/borrow proxy)",
    )
    p.add_argument("--universe", default="", help="universe name (NIFTY50/MIDCAP); default NIFTY50")
    p.add_argument(
        "--momentum", action="store_true", help="momentum direction: long strongest, short weakest"
    )
    asyncio.run(_main(p.parse_args()))
