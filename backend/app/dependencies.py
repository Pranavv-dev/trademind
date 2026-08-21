from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories import AgentRepository, CandleRepository, TradeRepository
from app.db.session import get_session

# Redis client — initialized in app lifespan
_redis: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    global _redis
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.close()
        _redis = None


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    if _redis is None:
        raise RuntimeError("Redis not initialized")
    yield _redis


async def get_db(
    session: AsyncSession = Depends(get_session),
) -> AsyncGenerator[AsyncSession, None]:
    yield session


def get_agent_repo(session: AsyncSession = Depends(get_session)) -> AgentRepository:
    return AgentRepository(session)


def get_trade_repo(session: AsyncSession = Depends(get_session)) -> TradeRepository:
    return TradeRepository(session)


def get_candle_repo(session: AsyncSession = Depends(get_session)) -> CandleRepository:
    return CandleRepository(session)
