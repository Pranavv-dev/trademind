import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_agents_empty(client: AsyncClient):
    response = await client.get("/api/agents")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_agent(client: AsyncClient):
    payload = {
        "name": "test-technical",
        "strategy_type": "technical",
        "config": {"rsi_period": 14},
        "market": "NSE",
        "capital_allocated": "100000",
    }
    response = await client.post("/api/agents", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "test-technical"
    assert data["strategy_type"] == "technical"
    assert data["status"] == "paused"


@pytest.mark.asyncio
async def test_get_agent_not_found(client: AsyncClient):
    response = await client.get("/api/agents/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
