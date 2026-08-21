"""Order state machine: pending → placed → filled / partially_filled / cancelled / rejected."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.trade import Trade
from app.db.repositories.trade_repo import TradeRepository

log = structlog.get_logger()

# Valid state transitions
TRANSITIONS = {
    "pending": {"placed", "cancelled", "rejected"},
    "placed": {"filled", "partially_filled", "cancelled", "rejected"},
    "partially_filled": {"filled", "cancelled"},
    # Terminal states — no further transitions
    "filled": set(),
    "cancelled": set(),
    "rejected": set(),
}


class OrderManager:
    """Manages order lifecycle and state transitions."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = TradeRepository(session)

    async def create_order(
        self,
        agent_id: uuid.UUID,
        symbol: str,
        exchange: str,
        side: str,
        quantity: int,
        price: Decimal,
        order_type: str,
        product: str,
        is_paper: bool = True,
        signal_data: dict | None = None,
        risk_check: dict | None = None,
        ai_reasoning: str | None = None,
    ) -> Trade:
        """Create a new order in pending state."""
        trade = await self.repo.create(
            agent_id=agent_id,
            symbol=symbol,
            exchange=exchange,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            product=product,
            status="pending",
            is_paper=is_paper,
            signal_data=signal_data,
            risk_check=risk_check,
            ai_reasoning=ai_reasoning,
        )
        log.info(
            "order_created",
            trade_id=str(trade.id),
            symbol=symbol,
            side=side,
            quantity=quantity,
        )
        return trade

    async def transition(
        self,
        trade_id: uuid.UUID,
        new_status: str,
        **kwargs,
    ) -> Trade | None:
        """Transition an order to a new state. Validates the transition is allowed."""
        trade = await self.repo.get_by_id(trade_id)
        if not trade:
            log.error("order_not_found", trade_id=str(trade_id))
            return None

        allowed = TRANSITIONS.get(trade.status, set())
        if new_status not in allowed:
            log.error(
                "invalid_transition",
                trade_id=str(trade_id),
                from_status=trade.status,
                to_status=new_status,
            )
            return None

        update_kwargs = {"status": new_status, **kwargs}
        if new_status == "filled" and "executed_at" not in kwargs:
            update_kwargs["executed_at"] = datetime.now(timezone.utc)

        updated = await self.repo.update_status(trade_id, new_status, **kwargs)
        log.info(
            "order_transitioned",
            trade_id=str(trade_id),
            from_status=trade.status,
            to_status=new_status,
        )
        return updated

    async def mark_placed(
        self, trade_id: uuid.UUID, broker_order_id: str | None = None
    ) -> Trade | None:
        return await self.transition(trade_id, "placed", broker_order_id=broker_order_id)

    async def mark_filled(
        self,
        trade_id: uuid.UUID,
        fill_price: Decimal,
        brokerage: Decimal = Decimal("0"),
    ) -> Trade | None:
        return await self.transition(
            trade_id,
            "filled",
            fill_price=fill_price,
            brokerage=brokerage,
            executed_at=datetime.now(timezone.utc),
        )

    async def mark_rejected(self, trade_id: uuid.UUID, reason: str = "") -> Trade | None:
        return await self.transition(trade_id, "rejected")

    async def mark_cancelled(self, trade_id: uuid.UUID) -> Trade | None:
        return await self.transition(trade_id, "cancelled")
