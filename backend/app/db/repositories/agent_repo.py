import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent


class AgentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(self) -> list[Agent]:
        result = await self.session.execute(
            select(Agent).where(Agent.status != "archived").order_by(Agent.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, agent_id: uuid.UUID) -> Agent | None:
        result = await self.session.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one_or_none()

    async def get_active(self) -> list[Agent]:
        result = await self.session.execute(select(Agent).where(Agent.status == "active"))
        return list(result.scalars().all())

    async def create(self, **kwargs) -> Agent:
        agent = Agent(**kwargs)
        self.session.add(agent)
        await self.session.commit()
        await self.session.refresh(agent)
        return agent

    async def update(self, agent_id: uuid.UUID, **kwargs) -> Agent | None:
        await self.session.execute(update(Agent).where(Agent.id == agent_id).values(**kwargs))
        await self.session.commit()
        return await self.get_by_id(agent_id)

    async def delete(self, agent_id: uuid.UUID) -> bool:
        # Soft-delete: the agent has FK-referencing rows (trades, positions, signals),
        # so a hard DELETE raises a foreign-key violation (HTTP 500, surfaced in the
        # browser as "Failed to fetch"). Archiving removes it from all lists, stops it
        # trading (get_active only returns status=="active"), and preserves history.
        agent = await self.get_by_id(agent_id)
        if agent:
            agent.status = "archived"
            await self.session.commit()
            return True
        return False

    async def update_status(self, agent_id: uuid.UUID, status: str) -> Agent | None:
        return await self.update(agent_id, status=status)
