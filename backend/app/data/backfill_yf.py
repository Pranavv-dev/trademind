"""One-off historical daily-candle backfill from Yahoo Finance (research only).

Extends the `candles` table BACKWARD with ~5 years of daily OHLCV so the backtest
and factor-IC study can be validated across multiple market regimes (we otherwise
have only ~15 months of Kite data).

SAFETY: inserts ONLY rows dated strictly BEFORE the earliest existing candle for
each symbol, and uses ON CONFLICT DO NOTHING. It therefore never overwrites or
duplicates the live Kite-sourced rows — it only fills the gap before them. No
schema migration, no DB creation. Daily ("1d") timeframe only.

  pip install yfinance        # ephemeral, in the container
  python -m app.data.backfill_yf --since 2020-01-01

yfinance is fetched with auto_adjust=False to keep raw (unadjusted) OHLC close to
Kite's convention; a small price seam at the Kite boundary is possible and is an
accepted research caveat (see docs/SIGNAL_EDGE_FINDINGS.md).
"""

from __future__ import annotations

import argparse
import asyncio
import time as _time
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import structlog

from app.data.universe import NIFTY50

log = structlog.get_logger()
IST = ZoneInfo("Asia/Kolkata")


def _q2(v) -> Decimal:
    return Decimal(str(round(float(v), 2))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _earliest_candle_date(session, symbol: str):
    from sqlalchemy import text

    row = await session.execute(
        text("SELECT min(time) FROM candles WHERE symbol=:s AND exchange='NSE' AND timeframe='1d'"),
        {"s": symbol},
    )
    return row.scalar()


async def backfill(since: str, symbols: list[str] | None = None) -> dict:
    import yfinance as yf
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.config import settings
    from app.db.models.candle import Candle

    symbols = symbols or list(NIFTY50)
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    inserted_total = 0
    failures: list[str] = []
    per_symbol: dict[str, int] = {}

    try:
        for sym in symbols:
            ticker = f"{sym}.NS"
            # Only fill BEFORE the earliest existing row for this symbol.
            async with factory() as session:
                earliest = await _earliest_candle_date(session, sym)
            cutoff = earliest.date() if earliest else datetime.now(IST).date()

            try:
                df = yf.download(
                    ticker,
                    start=since,
                    end=cutoff.isoformat(),
                    interval="1d",
                    auto_adjust=False,
                    actions=False,
                    progress=False,
                    threads=False,
                )
            except Exception as e:
                failures.append(f"{sym}: download {type(e).__name__}")
                continue
            if df is None or df.empty:
                failures.append(f"{sym}: empty")
                continue
            # Flatten possible MultiIndex columns (yfinance>=0.2 single-ticker quirk)
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df.columns = df.columns.droplevel(1)

            rows = []
            for idx, r in df.iterrows():
                try:
                    o, h, lo, c = r["Open"], r["High"], r["Low"], r["Close"]
                    v = r["Volume"]
                    if any(x != x for x in (o, h, lo, c)):  # NaN guard
                        continue
                    d = idx.date() if hasattr(idx, "date") else idx
                    if d >= cutoff:
                        continue
                    rows.append(
                        {
                            "symbol": sym,
                            "exchange": "NSE",
                            "timeframe": "1d",
                            "time": datetime(d.year, d.month, d.day, tzinfo=IST),
                            "open": _q2(o),
                            "high": _q2(h),
                            "low": _q2(lo),
                            "close": _q2(c),
                            "volume": int(v) if v == v else 0,
                        }
                    )
                except Exception:
                    continue

            if not rows:
                per_symbol[sym] = 0
                continue

            async with factory() as session:
                stmt = pg_insert(Candle).values(rows)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["symbol", "exchange", "timeframe", "time"]
                )
                result = await session.execute(stmt)
                await session.commit()
                n = result.rowcount if result.rowcount is not None else len(rows)
            per_symbol[sym] = n
            inserted_total += n
            log.info("backfill_symbol", symbol=sym, inserted=n, cutoff=str(cutoff))
            _time.sleep(0.7)  # be polite to Yahoo
    finally:
        await engine.dispose()

    return {
        "inserted_total": inserted_total,
        "symbols_ok": len([k for k, v in per_symbol.items() if v]),
        "failures": failures,
        "per_symbol": per_symbol,
    }


async def _main(args):
    from app.data.universe import get_universe

    syms = get_universe(args.universe) if args.universe else None
    res = await backfill(args.since, symbols=syms)
    print("\n=== yfinance daily backfill ===")
    print(f"  inserted rows : {res['inserted_total']}")
    print(f"  symbols w/data: {res['symbols_ok']}/{len(NIFTY50)}")
    if res["failures"]:
        print(f"  failures      : {res['failures']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="2020-01-01", help="earliest date to fetch (YYYY-MM-DD)")
    p.add_argument("--universe", default="", help="universe name (NIFTY50/MIDCAP); default NIFTY50")
    asyncio.run(_main(p.parse_args()))
