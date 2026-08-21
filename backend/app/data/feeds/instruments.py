"""Kite Connect instrument master — maps trading symbols to instrument tokens.

Downloads and caches the full instrument list from Zerodha.
Instrument tokens are required for WebSocket subscriptions and order placement.
"""

import asyncio
from datetime import datetime, timezone

import structlog
from kiteconnect import KiteConnect

from app.config import settings

log = structlog.get_logger()


class InstrumentMaster:
    """Manages the Zerodha instrument master list.

    The master is a ~70MB CSV that changes daily (new expiries, listings).
    We download it once at startup and cache in memory.
    """

    def __init__(self):
        self._instruments: list[dict] = []
        self._by_symbol: dict[str, dict] = {}  # "NSE:RELIANCE" -> instrument
        self._by_token: dict[int, dict] = {}  # 738561 -> instrument
        self._last_updated: datetime | None = None

    @property
    def loaded(self) -> bool:
        return len(self._instruments) > 0

    @property
    def count(self) -> int:
        return len(self._instruments)

    async def load(self, kite: KiteConnect | None = None) -> int:
        """Download instrument master from Zerodha. Returns count of instruments loaded."""
        if kite is None:
            if not settings.kite_api_key:
                log.warning("instruments_skipped", reason="no_api_key")
                return 0
            kite = KiteConnect(api_key=settings.kite_api_key)
            if settings.kite_access_token:
                kite.set_access_token(settings.kite_access_token)

        loop = asyncio.get_running_loop()

        try:
            instruments = await loop.run_in_executor(None, kite.instruments)
        except Exception as e:
            log.error("instruments_download_failed", error=str(e))
            return 0

        self._instruments = instruments
        self._by_symbol = {}
        self._by_token = {}

        for inst in instruments:
            token = inst.get("instrument_token")
            exchange = inst.get("exchange", "")
            symbol = inst.get("tradingsymbol", "")
            key = f"{exchange}:{symbol}"
            self._by_symbol[key] = inst
            if token:
                self._by_token[token] = inst

        self._last_updated = datetime.now(timezone.utc)
        log.info("instruments_loaded", count=len(instruments))
        return len(instruments)

    def get_token(self, exchange: str, symbol: str) -> int | None:
        """Look up instrument token for a trading symbol."""
        key = f"{exchange}:{symbol}"
        inst = self._by_symbol.get(key)
        return inst["instrument_token"] if inst else None

    def get_tokens(self, symbols: list[tuple[str, str]]) -> dict[str, int]:
        """Batch look up tokens. Input: [(exchange, symbol), ...]. Returns {symbol: token}."""
        result = {}
        for exchange, symbol in symbols:
            token = self.get_token(exchange, symbol)
            if token is not None:
                result[symbol] = token
        return result

    def get_symbol(self, token: int) -> tuple[str, str] | None:
        """Reverse look up: token -> (exchange, symbol)."""
        inst = self._by_token.get(token)
        if inst:
            return inst["exchange"], inst["tradingsymbol"]
        return None

    def search(self, query: str, exchange: str = "NSE", limit: int = 10) -> list[dict]:
        """Search instruments by name or symbol."""
        query_upper = query.upper()
        results = []
        for inst in self._instruments:
            if inst.get("exchange") != exchange:
                continue
            symbol = inst.get("tradingsymbol", "")
            name = inst.get("name", "")
            if query_upper in symbol or query_upper in name.upper():
                results.append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "exchange": exchange,
                        "token": inst.get("instrument_token"),
                        "instrument_type": inst.get("instrument_type"),
                        "lot_size": inst.get("lot_size"),
                        "tick_size": inst.get("tick_size"),
                    }
                )
                if len(results) >= limit:
                    break
        return results

    def get_nfo_options(
        self,
        underlying: str,
        expiry: str | None = None,
        option_type: str | None = None,
    ) -> list[dict]:
        """Get NFO options chain for an underlying.

        Args:
            underlying: e.g. "NIFTY", "BANKNIFTY", "RELIANCE"
            expiry: filter by expiry date string (e.g. "2024-01-25")
            option_type: "CE" or "PE"
        """
        results = []
        for inst in self._instruments:
            if inst.get("exchange") != "NFO":
                continue
            if inst.get("name") != underlying:
                continue
            if inst.get("instrument_type") not in ("CE", "PE"):
                continue
            if option_type and inst.get("instrument_type") != option_type:
                continue
            if expiry and str(inst.get("expiry", "")) != expiry:
                continue
            results.append(
                {
                    "symbol": inst["tradingsymbol"],
                    "token": inst["instrument_token"],
                    "strike": inst.get("strike"),
                    "expiry": str(inst.get("expiry", "")),
                    "type": inst.get("instrument_type"),
                    "lot_size": inst.get("lot_size"),
                    "tick_size": inst.get("tick_size"),
                }
            )

        results.sort(key=lambda x: (x.get("expiry", ""), x.get("strike", 0)))
        return results


# ── Singleton ──

_master: InstrumentMaster | None = None


def get_instrument_master() -> InstrumentMaster:
    """Get or create the global InstrumentMaster."""
    global _master
    if _master is None:
        _master = InstrumentMaster()
    return _master
