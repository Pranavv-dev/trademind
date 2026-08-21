"""Factor information-coefficient (IC) study for the ContextScorer signals.

Trade backtests conflate signal quality with trade management. This isolates the
signal: for every symbol on every (sampled) day it computes each ContextScorer
factor point-in-time, then correlates that factor with the stock's ACTUAL forward
return over 5/10/21 trading days.

IC = cross-sectional correlation(factor, forward_return). Interpretation:
  IC ~ 0            → the factor has no predictive power (noise).
  IC > 0            → higher factor value precedes higher returns (momentum works).
  IC < 0            → higher factor value precedes LOWER returns (mean-reversion edge:
                      we'd want to BUY the low end, i.e. the signal is inverted).
Equity-factor ICs of 0.03-0.05 are already meaningful; |IC| < 0.02 is basically noise.

Run:
    docker compose exec backend python -m app.backtest.factor_study \
        --start 2025-06-01 --end 2026-06-20
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

import structlog

from app.backtest.proactive_backtest import AsOfCandleRepo
from app.data.context_scorer import ContextScorer
from app.data.universe import NIFTY50

log = structlog.get_logger()

FACTORS = [
    "total_score",
    "rs_score",
    "sector_momentum",
    "proximity_52w",
    "volume_zscore",
    "tightness",
]
HORIZONS = [5, 10, 21]


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return cov / (vx**0.5 * vy**0.5)


def _ranks(vals: list[float]) -> list[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float:
    return _pearson(_ranks(xs), _ranks(ys))


def _ascending_closes(rows) -> list[tuple[date, float]]:
    # rows are newest-first; return [(date, close)] ascending
    return [(c.time.date(), float(c.close)) for c in reversed(rows)]


async def run_factor_study(
    session, start: date, end: date, symbols: list[str] | None = None, sample_every: int = 5
) -> dict:
    from app.db.repositories.candle_repo import CandleRepository

    symbols = symbols or list(NIFTY50)
    candle_repo = CandleRepository(session)
    asof = await AsOfCandleRepo.preload(candle_repo, symbols, start, end)
    scorer = ContextScorer(candle_repo=asof)

    # Per-symbol ascending close series for forward returns (uses future data — OK for a study).
    closes_by_symbol: dict[str, list[tuple[date, float]]] = {
        s: _ascending_closes(rows) for s, rows in asof._rows.items()
    }
    idx_by_symbol: dict[str, dict[date, int]] = {
        s: {d: i for i, (d, _) in enumerate(series)} for s, series in closes_by_symbol.items()
    }

    def fwd_return(symbol: str, d: date, h: int) -> float | None:
        series = closes_by_symbol.get(symbol)
        idx = idx_by_symbol.get(symbol, {})
        i = idx.get(d)
        if series is None or i is None or i + h >= len(series):
            return None
        c0 = series[i][1]
        c1 = series[i + h][1]
        if c0 <= 0:
            return None
        return c1 / c0 - 1.0

    dates = asof.trading_dates(start, end)
    sampled = dates[::sample_every]
    log.info("factor_study_start", n_sampled_days=len(sampled), n_symbols=len(asof._rows))

    # Collect (factor_value, fwd_return) pairs per factor per horizon
    pairs: dict[tuple[str, int], tuple[list[float], list[float]]] = {
        (f, h): ([], []) for f in FACTORS for h in HORIZONS
    }
    n_obs = 0

    for d in sampled:
        asof.as_of = d
        scores = await scorer.score_universe(symbols=symbols)
        if not scores:
            continue
        for sym, sc in scores.items():
            fvals = {
                "total_score": sc.total_score,
                "rs_score": sc.rs_score,
                "sector_momentum": sc.sector_momentum,
                "proximity_52w": sc.proximity_52w,
                "volume_zscore": sc.volume_zscore,
                "tightness": sc.tightness,
            }
            for h in HORIZONS:
                fr = fwd_return(sym, d, h)
                if fr is None:
                    continue
                for f in FACTORS:
                    xs, ys = pairs[(f, h)]
                    xs.append(fvals[f])
                    ys.append(fr)
                n_obs += 1

    result = {"n_observations": n_obs, "sampled_days": len(sampled), "ic": {}}
    for f in FACTORS:
        result["ic"][f] = {}
        for h in HORIZONS:
            xs, ys = pairs[(f, h)]
            result["ic"][f][f"{h}d"] = {
                "pearson": round(_pearson(xs, ys), 4),
                "spearman": round(_spearman(xs, ys), 4),
                "n": len(xs),
            }
    return result


async def _main(args):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.config import settings

    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        from app.data.universe import get_universe

        syms = get_universe(args.universe) if args.universe else None
        async with factory() as session:
            res = await run_factor_study(
                session,
                date.fromisoformat(args.start),
                date.fromisoformat(args.end),
                symbols=syms,
                sample_every=args.sample_every,
            )
    finally:
        await engine.dispose()

    print(
        f"\n=== Factor IC study  ({res['sampled_days']} sampled days, "
        f"{res['n_observations']} obs) ==="
    )
    print("  (Spearman IC; |IC|<0.02 = noise, >0.03 = meaningful. Sign shows direction.)\n")
    print(f"  {'factor':<18}{'5d':>10}{'10d':>10}{'21d':>10}")
    for f in FACTORS:
        row = res["ic"][f]
        print(
            f"  {f:<18}{row['5d']['spearman']:>10}"
            f"{row['10d']['spearman']:>10}{row['21d']['spearman']:>10}"
        )
    return res


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument(
        "--sample-every",
        type=int,
        default=5,
        dest="sample_every",
        help="sample every Nth trading day (default 5 = weekly) to reduce overlap autocorrelation",
    )
    p.add_argument("--universe", default="", help="universe name (NIFTY50/MIDCAP); default NIFTY50")
    asyncio.run(_main(p.parse_args()))
