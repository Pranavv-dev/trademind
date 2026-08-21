"""Free NSE India data client for development (no Kite API needed)."""

import asyncio
from datetime import datetime

import httpx
import structlog

log = structlog.get_logger()

NSE_BASE_URL = "https://www.nseindia.com"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


class NSEClient:
    """Fetches market data from NSE India website (free, no API key required).

    Note: NSE rate-limits aggressively. Use with caching + respectful delays.
    This client is for development/testing. Production should use Kite Connect.
    """

    def __init__(self):
        self._session_cookies: dict = {}
        self._last_request: float = 0
        self._min_delay = 1.0  # seconds between requests

    async def _get_session(self, client: httpx.AsyncClient) -> None:
        """Hit NSE homepage to get session cookies."""
        resp = await client.get(NSE_BASE_URL, headers=NSE_HEADERS)
        self._session_cookies = dict(resp.cookies)

    async def _throttled_get(self, client: httpx.AsyncClient, url: str) -> dict | None:
        """Rate-limited GET request."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request
        if elapsed < self._min_delay:
            await asyncio.sleep(self._min_delay - elapsed)

        try:
            resp = await client.get(
                url,
                headers=NSE_HEADERS,
                cookies=self._session_cookies,
                timeout=15,
            )
            self._last_request = asyncio.get_event_loop().time()
            if resp.status_code == 200:
                return resp.json()
            log.warning("nse_request_failed", url=url, status=resp.status_code)
        except Exception:
            log.exception("nse_request_error", url=url)
        return None

    async def get_quote(self, symbol: str) -> dict | None:
        """Get live quote for a symbol."""
        async with httpx.AsyncClient() as client:
            if not self._session_cookies:
                await self._get_session(client)
            url = f"{NSE_BASE_URL}/api/quote-equity?symbol={symbol}"
            data = await self._throttled_get(client, url)
            if data and "priceInfo" in data:
                info = data["priceInfo"]
                return {
                    "symbol": symbol,
                    "exchange": "NSE",
                    "ltp": info.get("lastPrice", 0),
                    "open": info.get("open", 0),
                    "high": info.get("intraDayHighLow", {}).get("max", 0),
                    "low": info.get("intraDayHighLow", {}).get("min", 0),
                    "close": info.get("previousClose", 0),
                    "volume": data.get("securityWiseDP", {}).get("quantityTraded", 0),
                    "change": info.get("change", 0),
                    "change_pct": info.get("pChange", 0),
                }
            return None

    async def get_historical(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Get historical daily candles from NSE.

        Note: NSE website only provides ~2 years of historical data.
        """
        async with httpx.AsyncClient() as client:
            if not self._session_cookies:
                await self._get_session(client)

            start_str = start.strftime("%d-%m-%Y")
            end_str = end.strftime("%d-%m-%Y")
            url = (
                f"{NSE_BASE_URL}/api/historical/securityArchives?"
                f"from={start_str}&to={end_str}&symbol={symbol}&dataType=priceVolumeDeliverable&series=EQ"
            )
            data = await self._throttled_get(client, url)
            if not data or "data" not in data:
                return []

            candles = []
            for row in data["data"]:
                try:
                    candles.append(
                        {
                            "symbol": symbol,
                            "exchange": "NSE",
                            "timeframe": "1d",
                            "time": datetime.strptime(row["CH_TIMESTAMP"], "%Y-%m-%d"),
                            "open": float(row["CH_OPENING_PRICE"]),
                            "high": float(row["CH_TRADE_HIGH_PRICE"]),
                            "low": float(row["CH_TRADE_LOW_PRICE"]),
                            "close": float(row["CH_CLOSING_PRICE"]),
                            "volume": int(row["CH_TOT_TRADED_QTY"]),
                        }
                    )
                except (KeyError, ValueError):
                    continue
            return candles


nse_client = NSEClient()
