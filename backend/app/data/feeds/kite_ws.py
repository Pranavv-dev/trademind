"""Zerodha KiteTicker WebSocket handler for live market data.

Connects to Kite's WebSocket feed, receives tick data, and pushes it into
the Redis price cache for consumption by agents and the dashboard.
"""

import asyncio
from threading import Thread

import structlog
from kiteconnect import KiteTicker

from app.config import settings
from app.data.cache import PriceCache

log = structlog.get_logger()


class KiteTickerManager:
    """Manages Kite Connect WebSocket connection for live market data.

    KiteTicker uses a threaded WebSocket internally, so we run it in a
    background thread and bridge tick data to our async Redis cache.
    """

    def __init__(self, cache: PriceCache | None = None):
        self._ticker: KiteTicker | None = None
        self._cache = cache
        self._subscribed_tokens: set[int] = set()
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False
        # Callback for broadcasting ticks to WebSocket clients
        self._tick_callback = None

    @property
    def connected(self) -> bool:
        return self._connected

    def set_tick_callback(self, callback) -> None:
        """Set a callback function(ticks: list[dict]) called on each tick batch."""
        self._tick_callback = callback

    async def connect(
        self,
        api_key: str | None = None,
        access_token: str | None = None,
    ) -> None:
        """Initialize KiteTicker and connect in background thread."""
        key = api_key or settings.kite_api_key
        token = access_token or settings.kite_access_token

        if not key or not token:
            log.warning("kite_ticker_skipped", reason="missing credentials")
            return

        self._loop = asyncio.get_running_loop()

        self._ticker = KiteTicker(key, token)
        self._ticker.on_ticks = self._on_ticks
        self._ticker.on_connect = self._on_connect
        self._ticker.on_close = self._on_close
        self._ticker.on_error = self._on_error
        self._ticker.on_reconnect = self._on_reconnect

        # KiteTicker.connect() blocks, so run in a daemon thread
        self._thread = Thread(
            target=self._ticker.connect,
            kwargs={"threaded": False},
            daemon=True,
        )
        self._thread.start()
        log.info("kite_ticker_connecting")

    async def subscribe(self, instrument_tokens: list[int], mode: str = "full") -> None:
        """Subscribe to live tick data for given instrument tokens.

        Modes: 'full' (OHLC + depth), 'quote' (OHLC), 'ltp' (last price only)
        """
        if not self._ticker:
            log.warning("kite_ticker_not_connected", action="subscribe")
            return

        new_tokens = [t for t in instrument_tokens if t not in self._subscribed_tokens]
        if not new_tokens:
            return

        self._subscribed_tokens.update(new_tokens)
        self._ticker.subscribe(new_tokens)

        mode_map = {"full": "full", "quote": "quote", "ltp": "ltp"}
        kite_mode = mode_map.get(mode, "full")
        if kite_mode == "full":
            self._ticker.set_mode(self._ticker.MODE_FULL, new_tokens)
        elif kite_mode == "quote":
            self._ticker.set_mode(self._ticker.MODE_QUOTE, new_tokens)
        else:
            self._ticker.set_mode(self._ticker.MODE_LTP, new_tokens)

        log.info("kite_ticker_subscribed", tokens=len(new_tokens), mode=kite_mode)

    async def unsubscribe(self, instrument_tokens: list[int]) -> None:
        """Unsubscribe from instrument tokens."""
        if not self._ticker:
            return

        self._subscribed_tokens -= set(instrument_tokens)
        self._ticker.unsubscribe(instrument_tokens)
        log.info("kite_ticker_unsubscribed", tokens=len(instrument_tokens))

    async def disconnect(self) -> None:
        """Gracefully close the WebSocket connection."""
        if self._ticker:
            self._ticker.close()
            self._connected = False
            log.info("kite_ticker_disconnected")

    # ── KiteTicker callbacks (run in background thread) ──

    def _on_ticks(self, ws, ticks: list[dict]) -> None:
        """Handle incoming tick data — called from KiteTicker's thread."""
        if not ticks:
            return

        # Process ticks and push to Redis cache (thread-safe via asyncio)
        parsed = []
        for tick in ticks:
            parsed.append(
                {
                    "instrument_token": tick.get("instrument_token"),
                    "symbol": tick.get("tradingsymbol", ""),
                    "ltp": tick.get("last_price", 0),
                    "open": tick.get("ohlc", {}).get("open", 0),
                    "high": tick.get("ohlc", {}).get("high", 0),
                    "low": tick.get("ohlc", {}).get("low", 0),
                    "close": tick.get("ohlc", {}).get("close", 0),
                    "volume": tick.get("volume_traded", 0),
                    "change": tick.get("change", 0),
                    "timestamp": str(tick.get("exchange_timestamp", "")),
                    # Depth data (full mode only)
                    "buy_depth": tick.get("depth", {}).get("buy", []),
                    "sell_depth": tick.get("depth", {}).get("sell", []),
                }
            )

        if self._loop and self._cache:
            asyncio.run_coroutine_threadsafe(self._push_ticks_to_cache(parsed), self._loop)

        if self._loop and self._tick_callback:
            asyncio.run_coroutine_threadsafe(self._tick_callback(parsed), self._loop)

    async def _push_ticks_to_cache(self, ticks: list[dict]) -> None:
        """Push parsed tick data into Redis cache."""
        if not self._cache:
            return

        quotes = {}
        for tick in ticks:
            symbol = tick.get("symbol", "")
            if symbol:
                quotes[symbol] = {
                    "ltp": tick["ltp"],
                    "open": tick["open"],
                    "high": tick["high"],
                    "low": tick["low"],
                    "close": tick["close"],
                    "volume": tick["volume"],
                    "change": tick["change"],
                }

        if quotes:
            await self._cache.set_bulk_quotes("NSE", quotes)

    def _on_connect(self, ws, response) -> None:
        self._connected = True
        log.info("kite_ticker_connected")
        # Re-subscribe to any tokens if reconnecting
        if self._subscribed_tokens:
            ws.subscribe(list(self._subscribed_tokens))
            ws.set_mode(ws.MODE_FULL, list(self._subscribed_tokens))

    def _on_close(self, ws, code, reason) -> None:
        self._connected = False
        log.warning("kite_ticker_closed", code=code, reason=reason)

    def _on_error(self, ws, code, reason) -> None:
        log.error("kite_ticker_error", code=code, reason=reason)

    def _on_reconnect(self, ws, attempts_count) -> None:
        log.info("kite_ticker_reconnecting", attempt=attempts_count)


# ── Singleton ──

_ticker_manager: KiteTickerManager | None = None


def get_ticker_manager() -> KiteTickerManager:
    """Get or create the global KiteTickerManager."""
    global _ticker_manager
    if _ticker_manager is None:
        import redis.asyncio as aioredis

        from app.config import settings
        from app.data.cache import PriceCache

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        cache = PriceCache(redis_client)
        _ticker_manager = KiteTickerManager(cache=cache)
    return _ticker_manager
