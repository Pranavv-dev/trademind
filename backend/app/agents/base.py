"""Abstract base agent that all trading agents must implement."""

from abc import ABC, abstractmethod

import structlog

from app.agents.signals import MarketSnapshot, Signal

log = structlog.get_logger()


class BaseAgent(ABC):
    """Base class for all trading agents.

    Subclasses must implement:
        - analyze(): Core analysis logic, returns a Signal or None
        - get_config_schema(): JSON schema for agent-specific config
    """

    def __init__(self, agent_id: str, name: str, config: dict):
        self.agent_id = agent_id
        self.name = name
        self.config = config
        self.status = "paused"
        self.log = log.bind(agent_id=agent_id, agent_name=name)

    @abstractmethod
    async def analyze(self, snapshot: MarketSnapshot) -> Signal | None:
        """Analyze market data and return a Signal if actionable, None otherwise."""

    @abstractmethod
    def get_config_schema(self) -> dict:
        """Return JSON schema describing this agent's config parameters."""

    async def on_start(self) -> None:
        """Called when the agent is started. Override for setup logic."""
        self.status = "active"
        self.log.info("agent_started")

    async def on_stop(self) -> None:
        """Called when the agent is stopped. Override for cleanup logic."""
        self.status = "paused"
        self.log.info("agent_stopped")
