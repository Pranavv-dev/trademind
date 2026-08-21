"""Redis cache for live market prices and runtime state."""

import json

import redis.asyncio as aioredis
import structlog

log = structlog.get_logger()


class PriceCache:
    """Manages live price data in Redis."""

    def __init__(self, redis: aioredis.Redis):
        self.redis = redis

    async def set_quote(self, exchange: str, symbol: str, quote: dict) -> None:
        """Cache a live quote. TTL = 60s (stale after 1 min)."""
        key = f"price:{exchange}:{symbol}"
        await self.redis.set(key, json.dumps(quote, default=str), ex=60)

    async def get_quote(self, exchange: str, symbol: str) -> dict | None:
        """Get cached quote."""
        key = f"price:{exchange}:{symbol}"
        data = await self.redis.get(key)
        if data:
            return json.loads(data)
        return None

    async def set_bulk_quotes(self, exchange: str, quotes: dict[str, dict]) -> None:
        """Cache multiple quotes in a pipeline."""
        pipe = self.redis.pipeline()
        for symbol, quote in quotes.items():
            key = f"price:{exchange}:{symbol}"
            pipe.set(key, json.dumps(quote, default=str), ex=60)
        await pipe.execute()

    async def get_agent_status(self, agent_id: str) -> str | None:
        """Get agent runtime status from Redis."""
        return await self.redis.get(f"agent:{agent_id}:status")

    async def set_agent_status(self, agent_id: str, status: str) -> None:
        await self.redis.set(f"agent:{agent_id}:status", status)

    async def get_agent_last_signal(self, agent_id: str) -> dict | None:
        data = await self.redis.get(f"agent:{agent_id}:last_signal")
        if data:
            return json.loads(data)
        return None

    async def set_agent_last_signal(self, agent_id: str, signal: dict) -> None:
        await self.redis.set(
            f"agent:{agent_id}:last_signal",
            json.dumps(signal, default=str),
            ex=3600,
        )

    async def get_daily_pnl(self) -> float:
        val = await self.redis.get("risk:daily_pnl")
        return float(val) if val else 0.0

    # ── Per-agent symbol cooldown (after a position closes) ──

    async def set_position_cooldown(self, agent_id: str, symbol: str) -> int:
        """Block re-entry of (agent_id, symbol) until next 9:15 IST.

        Set after a position is fully closed (SL/TP/manual). Returns the TTL in seconds.
        If now < 9:15 IST today → lifts at today's 9:15. Otherwise → tomorrow's 9:15.
        """
        import zoneinfo
        from datetime import datetime, timedelta

        ist = zoneinfo.ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        target = now.replace(hour=9, minute=15, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(days=1)
        ttl = max(int((target - now).total_seconds()), 60)
        key = f"cooldown:{agent_id}:{symbol}"
        await self.redis.set(key, "1", ex=ttl)
        return ttl

    async def is_position_in_cooldown(self, agent_id: str, symbol: str) -> bool:
        """True if (agent_id, symbol) is in post-close cooldown."""
        key = f"cooldown:{agent_id}:{symbol}"
        return bool(await self.redis.exists(key))

    async def clear_position_cooldown(self, agent_id: str, symbol: str) -> None:
        """Manually clear a cooldown (admin/debug)."""
        await self.redis.delete(f"cooldown:{agent_id}:{symbol}")

    async def add_daily_pnl(self, pnl: float) -> float:
        """Atomically add to daily P&L. Returns new total."""
        return await self.redis.incrbyfloat("risk:daily_pnl", pnl)

    async def reset_daily_pnl(self) -> None:
        await self.redis.set("risk:daily_pnl", "0")

    async def set_circuit_breaker(self, active: bool) -> None:
        await self.redis.set("risk:circuit_breaker", "true" if active else "false")

    async def is_circuit_breaker_active(self) -> bool:
        val = await self.redis.get("risk:circuit_breaker")
        return val == "true"

    async def set_kite_token(self, access_token: str) -> None:
        """Persist Kite access token so celery workers can use it.

        TTL is set to expire at 11:55 PM IST — Zerodha invalidates tokens
        at midnight, so this ensures stale tokens don't linger into the next day.
        """
        import zoneinfo
        from datetime import datetime

        ist = zoneinfo.ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        expiry = now.replace(hour=23, minute=55, second=0, microsecond=0)
        ttl = max(int((expiry - now).total_seconds()), 60)
        await self.redis.set("kite:access_token", access_token, ex=ttl)

    async def get_kite_token(self) -> str | None:
        """Read Kite access token (written by FastAPI after auth callback)."""
        return await self.redis.get("kite:access_token")
