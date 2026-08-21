"""Tick-to-bar aggregator — builds 5m and 15m OHLCV bars from the live Kite tick stream.

Wired as a callback on KiteTickerManager. Maintains in-memory state per symbol
per timeframe; persists each bar to the `candles` table on bar close.
"""

from __future__ import annotations

import zoneinfo
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

import structlog

from app.data.feeds.instruments import get_instrument_master
from app.db.repositories.candle_repo import CandleRepository
from app.db.session import async_session_factory

log = structlog.get_logger()

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# Timeframes we aggregate. Key is the DB `timeframe` string; value is minutes.
TIMEFRAMES: dict[str, int] = {"5m": 5, "15m": 15}


@dataclass
class _BarState:
    """In-memory state for a single (symbol, timeframe) bucket."""

    bar_start: datetime  # IST timezone, timeframe-aligned
    open: float
    high: float
    low: float
    close: float
    volume_at_start: int  # cumulative day volume when this bar started
    last_volume: int  # last observed cumulative day volume
    # ticks count used only for observability
    ticks: int = 0


@dataclass
class _SymbolState:
    """Per-symbol state across all timeframes."""

    symbol: str
    bars: dict[str, _BarState] = field(default_factory=dict)
    last_tick_volume: int | None = None


def _bar_start_for(ts: datetime, minutes: int) -> datetime:
    """Floor an IST datetime to the nearest bar-start aligned to 9:15 session open."""
    # Align to the NSE session start (9:15) so bars are 9:15/9:20/9:25 on 5m etc.
    session_open = ts.replace(hour=9, minute=15, second=0, microsecond=0)
    if ts < session_open:
        return session_open
    delta_min = int((ts - session_open).total_seconds() // 60)
    bucket = (delta_min // minutes) * minutes
    return session_open + timedelta(minutes=bucket)


class TickBarAggregator:
    """Builds OHLCV bars from Kite tick stream and persists on bar close."""

    def __init__(self):
        self._state: dict[str, _SymbolState] = {}

    async def on_ticks(self, ticks: list[dict]) -> None:
        """Called by KiteTickerManager on every tick batch (async, on the event loop)."""
        if not ticks:
            return

        master = get_instrument_master()
        completed: list[dict] = []

        for tick in ticks:
            # Resolve symbol — kite tick includes instrument_token; fall back to tradingsymbol
            token = tick.get("instrument_token")
            symbol = tick.get("symbol") or ""
            if not symbol and token is not None:
                resolved = master.get_symbol(token)
                if resolved:
                    _, symbol = resolved
            if not symbol:
                continue

            ltp = float(tick.get("ltp", 0) or 0)
            if ltp <= 0:
                continue

            cumulative_vol = int(tick.get("volume", 0) or 0)
            # Parse exchange_timestamp if present, else fall back to now-IST
            ts_raw = tick.get("timestamp", "")
            try:
                # Kite exchange_timestamp is a datetime.datetime at the source;
                # we converted it to str in kite_ws
                ts = datetime.fromisoformat(str(ts_raw)) if ts_raw else datetime.now(IST)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=IST)
                else:
                    ts = ts.astimezone(IST)
            except Exception:
                ts = datetime.now(IST)

            sym_state = self._state.setdefault(symbol, _SymbolState(symbol=symbol))

            for tf, minutes in TIMEFRAMES.items():
                bucket_start = _bar_start_for(ts, minutes)
                bar = sym_state.bars.get(tf)

                if bar is None:
                    # First tick for this (symbol, timeframe)
                    sym_state.bars[tf] = _BarState(
                        bar_start=bucket_start,
                        open=ltp,
                        high=ltp,
                        low=ltp,
                        close=ltp,
                        volume_at_start=cumulative_vol,
                        last_volume=cumulative_vol,
                        ticks=1,
                    )
                    continue

                if bucket_start > bar.bar_start:
                    # Bar has rolled over → persist the completed bar before starting new one
                    bar_volume = max(bar.last_volume - bar.volume_at_start, 0)
                    completed.append(
                        {
                            "symbol": symbol,
                            "exchange": "NSE",
                            "timeframe": tf,
                            "time": bar.bar_start,
                            "open": Decimal(str(round(bar.open, 2))),
                            "high": Decimal(str(round(bar.high, 2))),
                            "low": Decimal(str(round(bar.low, 2))),
                            "close": Decimal(str(round(bar.close, 2))),
                            "volume": bar_volume,
                        }
                    )
                    # Start new bar with this tick
                    sym_state.bars[tf] = _BarState(
                        bar_start=bucket_start,
                        open=ltp,
                        high=ltp,
                        low=ltp,
                        close=ltp,
                        volume_at_start=cumulative_vol,
                        last_volume=cumulative_vol,
                        ticks=1,
                    )
                else:
                    # Update current bar in place
                    bar.high = max(bar.high, ltp)
                    bar.low = min(bar.low, ltp)
                    bar.close = ltp
                    bar.last_volume = cumulative_vol
                    bar.ticks += 1

        if completed:
            await self._persist_bars(completed)

    async def _persist_bars(self, bars: list[dict]) -> None:
        """Upsert completed bars via CandleRepository."""
        try:
            async with async_session_factory() as session:
                repo = CandleRepository(session)
                count = await repo.upsert_candles(bars)
            log.info(
                "intraday_bars_flushed",
                count=count,
                timeframes=sorted({b["timeframe"] for b in bars}),
            )
        except Exception:
            log.exception("intraday_bar_flush_error", bars=len(bars))


# ── Singleton ──

_aggregator: TickBarAggregator | None = None


def get_aggregator() -> TickBarAggregator:
    global _aggregator
    if _aggregator is None:
        _aggregator = TickBarAggregator()
    return _aggregator
