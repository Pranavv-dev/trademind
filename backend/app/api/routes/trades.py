import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.schemas import TradeResponse, TradeSummary
from app.db.repositories import TradeRepository
from app.dependencies import get_trade_repo

router = APIRouter()


@router.get("", response_model=list[TradeResponse])
async def list_trades(
    agent_id: uuid.UUID | None = None,
    status: str | None = None,
    side: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    repo: TradeRepository = Depends(get_trade_repo),
):
    return await repo.list_trades(
        agent_id=agent_id,
        status=status,
        side=side,
        limit=limit,
        offset=offset,
    )


@router.get("/summary/today", response_model=TradeSummary)
async def get_today_summary(
    repo: TradeRepository = Depends(get_trade_repo),
):
    return await repo.get_today_summary()


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(
    trade_id: uuid.UUID,
    repo: TradeRepository = Depends(get_trade_repo),
):
    trade = await repo.get_by_id(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade
