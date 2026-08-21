import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    PositionResponse,
    TradeResponse,
)
from app.db.models import Position, Trade
from app.db.repositories import AgentRepository
from app.db.session import get_session
from app.dependencies import get_agent_repo

router = APIRouter()


@router.get("", response_model=list[AgentResponse])
async def list_agents(repo: AgentRepository = Depends(get_agent_repo)):
    return await repo.get_all()


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    body: AgentCreate,
    repo: AgentRepository = Depends(get_agent_repo),
):
    return await repo.create(**body.model_dump())


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: uuid.UUID,
    repo: AgentRepository = Depends(get_agent_repo),
):
    agent = await repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    repo: AgentRepository = Depends(get_agent_repo),
):
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    agent = await repo.update(agent_id, **update_data)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    repo: AgentRepository = Depends(get_agent_repo),
):
    deleted = await repo.delete(agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/start", response_model=AgentResponse)
async def start_agent(
    agent_id: uuid.UUID,
    repo: AgentRepository = Depends(get_agent_repo),
):
    agent = await repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status == "active":
        raise HTTPException(status_code=400, detail="Agent already active")
    return await repo.update_status(agent_id, "active")


@router.post("/{agent_id}/stop", response_model=AgentResponse)
async def stop_agent(
    agent_id: uuid.UUID,
    repo: AgentRepository = Depends(get_agent_repo),
):
    agent = await repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status == "paused":
        raise HTTPException(status_code=400, detail="Agent already paused")
    return await repo.update_status(agent_id, "paused")


@router.get("/{agent_id}/trades", response_model=list[TradeResponse])
async def get_agent_trades(
    agent_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select

    result = await session.execute(
        select(Trade)
        .where(Trade.agent_id == agent_id)
        .order_by(Trade.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


@router.get("/{agent_id}/positions", response_model=list[PositionResponse])
async def get_agent_positions(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select

    result = await session.execute(
        select(Position).where(Position.agent_id == agent_id, Position.closed_at.is_(None))
    )
    return list(result.scalars().all())
