"""NSE market holiday detection — dynamic via NSE API + Redis cache + hardcoded fallback."""

import json
from datetime import date, datetime

import httpx
import pytz
import redis.asyncio as aioredis
import structlog

log = structlog.get_logger()

# Hardcoded fallback — used only when NSE API and Redis are both unavailable
NSE_HOLIDAYS_FALLBACK = {
    # 2025
    date(2025, 2, 26),  # Mahashivratri
    date(2025, 3, 14),  # Holi
    date(2025, 3, 31),  # Id-Ul-Fitr (Ramadan)
    date(2025, 4, 10),  # Shri Mahavir Jayanti
    date(2025, 4, 14),  # Dr. Baba Saheb Ambedkar Jayanti
    date(2025, 4, 18),  # Good Friday
    date(2025, 5, 1),  # Maharashtra Day
    date(2025, 8, 15),  # Independence Day
    date(2025, 8, 27),  # Ganesh Chaturthi
    date(2025, 10, 2),  # Mahatma Gandhi Jayanti / Dussehra
    date(2025, 10, 21),  # Diwali Laxmi Pujan
    date(2025, 10, 22),  # Diwali Balipratipada
    date(2025, 11, 5),  # Prakash Gurpurb Sri Guru Nanak Dev
    date(2025, 12, 25),  # Christmas
    # 2026 — Full NSE holiday calendar (source: groww.in/p/nse-holidays)
    date(2026, 1, 15),  # Municipal Corporation Election - Maharashtra
    date(2026, 1, 26),  # Republic Day
    date(2026, 3, 3),  # Holi
    date(2026, 3, 26),  # Shri Ram Navami
    date(2026, 3, 31),  # Shri Mahavir Jayanti
    date(2026, 4, 3),  # Good Friday
    date(2026, 4, 14),  # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5, 1),  # Maharashtra Day
    date(2026, 5, 28),  # Bakri Id
    date(2026, 6, 26),  # Muharram
    date(2026, 9, 14),  # Ganesh Chaturthi
    date(2026, 10, 2),  # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 10),  # Diwali-Balipratipada
    date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
    date(2026, 12, 25),  # Christmas
}

IST = pytz.timezone("Asia/Kolkata")

REDIS_KEY = "nse:holidays"
REDIS_TTL = 60 * 60 * 24 * 30  # 30 days

NSE_HOLIDAY_API = "https://www.nseindia.com/api/holiday-master?type=trading"


async def _fetch_nse_holidays() -> set[date] | None:
    """Fetch holiday list from NSE API. Returns None on failure."""
    try:
        async with httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TradeMind/1.0)"},
            follow_redirects=True,
        ) as client:
            # NSE requires a session cookie
            await client.get("https://www.nseindia.com")
            resp = await client.get(NSE_HOLIDAY_API)
            resp.raise_for_status()
            data = resp.json()

        holidays = set()
        # NSE API returns {"CM": [...], "FO": [...], ...} — use CM (equity segment)
        for entry in data.get("CM", []):
            try:
                dt = datetime.strptime(entry["tradingDate"], "%d-%b-%Y").date()
                holidays.add(dt)
            except (KeyError, ValueError):
                continue

        if holidays:
            log.info("nse_holidays_fetched", count=len(holidays))
            return holidays
    except Exception as e:
        log.warning("nse_holiday_fetch_failed", error=str(e)[:100])
    return None


async def _sync_holidays_to_redis(redis_client: aioredis.Redis) -> set[date] | None:
    """Fetch from NSE API and cache in Redis. Returns the holiday set or None."""
    holidays = await _fetch_nse_holidays()
    if holidays:
        # Store as JSON list of ISO date strings
        date_strs = [d.isoformat() for d in holidays]
        await redis_client.set(REDIS_KEY, json.dumps(date_strs), ex=REDIS_TTL)
        return holidays
    return None


async def _get_holidays_from_redis(redis_client: aioredis.Redis) -> set[date] | None:
    """Read cached holidays from Redis."""
    try:
        data = await redis_client.get(REDIS_KEY)
        if data:
            date_strs = json.loads(data)
            return {date.fromisoformat(d) for d in date_strs}
    except Exception:
        pass
    return None


async def is_market_holiday_async(
    redis_client: aioredis.Redis | None = None,
    check_date: date | None = None,
) -> bool:
    """Check if the given date is an NSE holiday or weekend.

    Checks: Redis cache → NSE API (and caches result) → hardcoded fallback.
    """
    if check_date is None:
        check_date = datetime.now(IST).date()

    # Weekend check (always works, no API needed)
    if check_date.weekday() >= 5:
        return True

    if redis_client:
        # Try Redis cache first
        holidays = await _get_holidays_from_redis(redis_client)
        if holidays is not None:
            return check_date in holidays

        # Cache miss — fetch from NSE API and populate cache
        holidays = await _sync_holidays_to_redis(redis_client)
        if holidays is not None:
            return check_date in holidays

    # Ultimate fallback — hardcoded list
    return check_date in NSE_HOLIDAYS_FALLBACK


def is_market_holiday(check_date: date | None = None) -> bool:
    """Synchronous fallback — uses hardcoded list only. For celery beat schedule guard."""
    if check_date is None:
        check_date = datetime.now(IST).date()

    if check_date.weekday() >= 5:
        return True

    return check_date in NSE_HOLIDAYS_FALLBACK
