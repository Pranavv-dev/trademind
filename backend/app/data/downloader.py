"""Historical OHLCV data downloader and storage manager."""

import asyncio
from datetime import datetime, timedelta
from functools import partial

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.feeds.instruments import get_instrument_master
from app.db.repositories.candle_repo import CandleRepository
from app.execution.broker.kite import get_kite_broker

log = structlog.get_logger()


class DataDownloader:
    """Downloads and stores historical candle data using Kite Connect."""

    def __init__(self, session: AsyncSession):
        self.repo = CandleRepository(session)

    async def download_symbol(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        """Download historical daily candles for a single symbol via Kite."""
        if end is None:
            end = datetime.now()
        if start is None:
            start = end - timedelta(days=365)

        token = get_instrument_master().get_token("NSE", symbol)
        if token is None:
            log.warning("instrument_not_found", symbol=symbol)
            return 0

        broker = get_kite_broker()
        if not broker.access_token:
            log.warning("kite_not_authenticated", symbol=symbol)
            return 0

        kite = broker.kite
        log.info(
            "downloading_candles", symbol=symbol, token=token, start=start.date(), end=end.date()
        )

        candles_total = 0
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=60), end)
            try:
                records = await asyncio.get_event_loop().run_in_executor(
                    None,
                    partial(
                        kite.historical_data,
                        instrument_token=token,
                        from_date=chunk_start.strftime("%Y-%m-%d"),
                        to_date=chunk_end.strftime("%Y-%m-%d"),
                        interval="day",
                    ),
                )
                if records:
                    candles = []
                    for row in records:
                        dt = row["date"]
                        if not isinstance(dt, datetime):
                            dt = datetime.strptime(str(dt)[:10], "%Y-%m-%d")
                        candles.append(
                            {
                                "symbol": symbol,
                                "exchange": "NSE",
                                "timeframe": "1d",
                                "time": dt,
                                "open": float(row["open"]),
                                "high": float(row["high"]),
                                "low": float(row["low"]),
                                "close": float(row["close"]),
                                "volume": int(row["volume"]),
                            }
                        )
                    count = await self.repo.upsert_candles(candles)
                    candles_total += count
                    log.debug("chunk_downloaded", symbol=symbol, count=count)
            except Exception:
                log.exception("kite_historical_error", symbol=symbol)
            chunk_start = chunk_end + timedelta(days=1)
            await asyncio.sleep(0.5)

        log.info("download_complete", symbol=symbol, total_candles=candles_total)
        return candles_total

    async def download_universe(
        self,
        symbols: list[str],
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, int]:
        """Download historical data for a list of symbols sequentially."""
        results = {}
        for symbol in symbols:
            try:
                count = await self.download_symbol(symbol, start, end)
                results[symbol] = count
            except Exception:
                log.exception("download_failed", symbol=symbol)
                results[symbol] = 0
            await asyncio.sleep(1)
        return results
