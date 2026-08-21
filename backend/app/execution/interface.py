"""Execution interface — routes trade proposals to paper or live broker."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.signals import TradeProposal
from app.data.cache import PriceCache
from app.db.models.position import Position
from app.execution.broker.base import BaseBroker
from app.execution.charges import calculate_charges
from app.execution.order_manager import OrderManager
from app.execution.paper import PaperBroker

log = structlog.get_logger()


class ExecutionEngine:
    """Central execution engine — manages the full order lifecycle.

    signal → risk manager → ExecutionEngine.execute() → broker → DB
    """

    def __init__(
        self,
        session: AsyncSession,
        cache: PriceCache,
        broker: BaseBroker | None = None,
        is_paper: bool = True,
    ):
        self.session = session
        self.cache = cache
        self.order_mgr = OrderManager(session)
        self.broker = broker or PaperBroker()
        self.is_paper = is_paper

    async def execute(self, proposal: TradeProposal) -> dict:
        """Execute a trade proposal through the full lifecycle.

        1. Create order record (pending)
        2. Place with broker (placed)
        3. On fill → update order + create/update position + update P&L
        """
        signal = proposal.signal
        agent_id = uuid.UUID(signal.agent_id)
        price = Decimal(str(signal.entry_price))

        # 1. Create order in DB
        trade = await self.order_mgr.create_order(
            agent_id=agent_id,
            symbol=signal.symbol,
            exchange=signal.exchange,
            side=signal.action,
            quantity=proposal.quantity,
            price=price,
            order_type=proposal.order_type,
            product=proposal.product,
            is_paper=self.is_paper,
            signal_data=signal.metadata,
            risk_check={
                "stop_loss": proposal.stop_loss,
                "take_profit": proposal.take_profit,
            },
            ai_reasoning=signal.reasoning,
        )

        # 2. Send to broker. If the agent supplied realized vol / ADV in signal
        # metadata (ContextScorer can — tightness × 100 is a vol proxy), forward
        # them to the broker so PaperBroker can apply realistic slippage.
        broker_kwargs = {
            "symbol": signal.symbol,
            "exchange": signal.exchange,
            "side": signal.action,
            "quantity": proposal.quantity,
            "price": price,
            "order_type": proposal.order_type,
            "product": proposal.product,
        }
        sig_meta = signal.metadata or {}
        if "realized_vol_pct" in sig_meta and self.is_paper:
            broker_kwargs["realized_vol_pct"] = float(sig_meta["realized_vol_pct"])
        if "adv_shares" in sig_meta and self.is_paper:
            broker_kwargs["adv_shares"] = int(sig_meta["adv_shares"])
        try:
            broker_result = await self.broker.place_order(**broker_kwargs)
        except TypeError:
            # Live broker doesn't accept extra kwargs; retry without them.
            broker_kwargs.pop("realized_vol_pct", None)
            broker_kwargs.pop("adv_shares", None)
            try:
                broker_result = await self.broker.place_order(**broker_kwargs)
            except Exception:
                log.exception("broker_place_error", trade_id=str(trade.id))
                await self.order_mgr.mark_rejected(trade.id, reason="Broker error")
                return {"trade_id": str(trade.id), "status": "rejected", "reason": "broker_error"}
        except Exception:
            log.exception("broker_place_error", trade_id=str(trade.id))
            await self.order_mgr.mark_rejected(trade.id, reason="Broker error")
            return {"trade_id": str(trade.id), "status": "rejected", "reason": "broker_error"}

        broker_order_id = broker_result.get("order_id")
        await self.order_mgr.mark_placed(trade.id, broker_order_id=broker_order_id)

        # 3. Handle fill
        broker_status = broker_result.get("status", "")
        if broker_status == "filled":
            fill_price = Decimal(str(broker_result.get("fill_price", signal.entry_price)))

            # Calculate brokerage and statutory charges
            charges = calculate_charges(
                price=fill_price,
                quantity=proposal.quantity,
                side=signal.action,
                product=proposal.product,
                exchange=signal.exchange,
            )
            brokerage = charges["total"]

            await self.order_mgr.mark_filled(trade.id, fill_price=fill_price, brokerage=brokerage)

            # Update position. _update_position now ALSO deducts entry-leg charges
            # (stored on Position at open) so realized_pnl reflects BOTH legs.
            # Returns (net_pnl_or_None, total_charges_used) so the caller can
            # honestly update the daily P&L cache and the trade record.
            pnl_net, total_charges = await self._update_position(
                agent_id=agent_id,
                symbol=signal.symbol,
                exchange=signal.exchange,
                side=signal.action,
                quantity=proposal.quantity,
                fill_price=fill_price,
                stop_loss=Decimal(str(proposal.stop_loss)),
                take_profit=Decimal(str(proposal.take_profit)),
                exit_charges=brokerage,
                signal_metadata=signal.metadata or {},
                signal_confidence=signal.confidence,
            )

            # Update daily P&L in Redis if closing a position
            if pnl_net is not None:
                await self.cache.add_daily_pnl(float(pnl_net))
                # Update trade record with realized P&L (net of both legs)
                trade_obj = await self.order_mgr.repo.get_by_id(trade.id)
                if trade_obj:
                    trade_obj.pnl = pnl_net
                    await self.session.commit()

            log.info(
                "trade_executed",
                trade_id=str(trade.id),
                symbol=signal.symbol,
                side=signal.action,
                quantity=proposal.quantity,
                fill_price=float(fill_price),
                pnl=float(pnl_net) if pnl_net is not None else None,
                total_charges=float(total_charges) if total_charges else None,
            )

            return {
                "trade_id": str(trade.id),
                "status": "filled",
                "fill_price": float(fill_price),
                "pnl": float(pnl_net) if pnl_net is not None else None,
            }

        return {"trade_id": str(trade.id), "status": broker_status}

    async def _update_position(
        self,
        agent_id: uuid.UUID,
        symbol: str,
        exchange: str,
        side: str,
        quantity: int,
        fill_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        exit_charges: Decimal = Decimal("0"),
        signal_metadata: dict | None = None,
        signal_confidence: float | None = None,
    ) -> tuple[Decimal | None, Decimal]:
        """Create / update / close a position.

        Returns:
            (net_pnl_or_None, total_charges_recorded)
              - net_pnl_or_None: realized P&L NET of both entry+exit charges if closing,
                else None (when opening or adding to a position).
              - total_charges_recorded: entry_charges + exit_charges that were deducted,
                or just the entry leg if opening (still ≥0).
        """
        signal_metadata = signal_metadata or {}
        # Find existing open position
        result = await self.session.execute(
            select(Position).where(
                Position.agent_id == agent_id,
                Position.symbol == symbol,
                Position.exchange == exchange,
                Position.closed_at.is_(None),
            )
        )
        existing = result.scalar_one_or_none()

        if existing is None:
            # Open new position. Capture the OPENING-leg brokerage on the position
            # itself so we can deduct it at close. Initialize highest_price for the
            # trailing-stop ratchet.
            pos_side = "LONG" if side == "BUY" else "SHORT"
            position = Position(
                agent_id=agent_id,
                symbol=symbol,
                exchange=exchange,
                side=pos_side,
                quantity=quantity,
                avg_price=fill_price,
                current_price=fill_price,
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
                stop_loss=stop_loss,
                take_profit=take_profit,
                entry_charges=exit_charges,  # NB: at open, the parameter named
                # `exit_charges` actually holds THIS leg's
                # brokerage — i.e. the entry-leg cost.
                highest_price=fill_price,
                # Persist the entry thesis so the close-time reflection can recall it.
                entry_metadata=signal_metadata or {},
                is_paper=self.is_paper,
                opened_at=datetime.now(timezone.utc),
            )
            self.session.add(position)
            await self.session.commit()
            log.info(
                "position_opened",
                symbol=symbol,
                side=pos_side,
                quantity=quantity,
                entry_charges=float(exit_charges),
            )
            return None, exit_charges

        # Determine if adding to or closing existing position
        is_same_direction = (existing.side == "LONG" and side == "BUY") or (
            existing.side == "SHORT" and side == "SELL"
        )

        if is_same_direction:
            # Add to existing position — update average price.
            # Accumulate the new leg's brokerage onto entry_charges so the eventual
            # close still deducts the full life-of-position cost.
            total_cost = existing.avg_price * existing.quantity + fill_price * quantity
            existing.quantity += quantity
            existing.avg_price = total_cost / existing.quantity
            existing.stop_loss = stop_loss
            existing.take_profit = take_profit
            existing.entry_charges = (existing.entry_charges or Decimal("0")) + exit_charges
            # Update high-water mark since price action may have advanced
            if existing.highest_price is None or fill_price > existing.highest_price:
                existing.highest_price = fill_price
            await self.session.commit()
            log.info(
                "position_added",
                symbol=symbol,
                new_qty=existing.quantity,
                additional_charges=float(exit_charges),
            )
            return None, exit_charges

        # Closing (fully or partially).
        # GROSS P&L = price diff × close_qty. NET P&L additionally deducts
        # entry_charges (pro-rated by closing fraction) + this leg's exit_charges.
        # Pro-rating matters for partial closes: if you close half the shares,
        # half the entry-leg cost is realized.
        close_qty = min(quantity, existing.quantity)
        if existing.side == "LONG":
            gross_pnl = (fill_price - existing.avg_price) * close_qty
        else:
            gross_pnl = (existing.avg_price - fill_price) * close_qty

        # Pro-rated entry charges allocated to THIS close
        total_qty_before = existing.quantity
        if total_qty_before > 0:
            entry_charge_share = (existing.entry_charges or Decimal("0")) * (
                Decimal(close_qty) / Decimal(total_qty_before)
            )
        else:
            entry_charge_share = Decimal("0")
        total_leg_charges = entry_charge_share + exit_charges
        net_pnl = gross_pnl - total_leg_charges

        existing.realized_pnl += net_pnl
        existing.quantity -= close_qty
        # Reduce remaining entry_charges by the share we just consumed
        existing.entry_charges = (existing.entry_charges or Decimal("0")) - entry_charge_share

        # Set cooldown on ANY closure (partial or full). Belt-and-suspenders against
        # death-spiral re-entry. Cooldown is keyed (agent_id, symbol) and capped at
        # next 9:15 IST so it auto-expires; setting it on partial closes is safe
        # because new BUYs would also be blocked by the duplicate-position check
        # until the partial position is fully closed. Set BEFORE commit so a Redis
        # failure aborts the close instead of leaving an unprotected hole.
        if close_qty > 0:
            try:
                ttl = await self.cache.set_position_cooldown(str(agent_id), symbol)
                log.info(
                    "position_cooldown_set",
                    agent_id=str(agent_id),
                    symbol=symbol,
                    ttl_seconds=ttl,
                    partial=(existing.quantity > 0),
                )
            except Exception:
                log.exception("position_cooldown_set_failed_aborting_close", symbol=symbol)
                # Roll back the in-memory mutations so the close isn't persisted
                # without its protective cooldown. Caller will see this as an
                # execution failure and may retry next cycle.
                existing.realized_pnl -= net_pnl
                existing.quantity += close_qty
                existing.entry_charges = (
                    existing.entry_charges or Decimal("0")
                ) + entry_charge_share
                raise

        if existing.quantity <= 0:
            # Fully closed
            existing.closed_at = datetime.now(timezone.utc)
            existing.current_price = fill_price
            log.info(
                "position_closed",
                symbol=symbol,
                net_pnl=float(net_pnl),
                gross_pnl=float(gross_pnl),
                total_charges=float(total_leg_charges),
            )
            # Write the learning-loop row. This is the foundation for every
            # future tuning decision; do not skip even if signal_metadata is sparse.
            try:
                await self._write_signal_outcome(
                    position=existing,
                    fill_price=fill_price,
                    gross_pnl=gross_pnl,
                    total_charges=total_leg_charges,
                    net_pnl=net_pnl,
                    close_qty=close_qty,
                    signal_metadata=signal_metadata,
                    signal_confidence=signal_confidence,
                )
            except Exception:
                log.exception("signal_outcome_write_failed", symbol=symbol)
        else:
            log.info(
                "position_reduced",
                symbol=symbol,
                remaining=existing.quantity,
                net_pnl=float(net_pnl),
                total_charges=float(total_leg_charges),
            )

        await self.session.commit()
        return net_pnl, total_leg_charges

    async def _write_signal_outcome(
        self,
        position: Position,
        fill_price: Decimal,
        gross_pnl: Decimal,
        total_charges: Decimal,
        net_pnl: Decimal,
        close_qty: int,
        signal_metadata: dict,
        signal_confidence: float | None,
    ) -> None:
        """Insert one row in signal_outcomes capturing the full lifecycle of this close.

        Used by /api/signal-performance and any future analytics or auto-tuning logic.
        """
        # Need the agent's strategy_type + name for downstream queries.
        from app.db.models.agent import Agent
        from app.db.models.signal_outcome import SignalOutcome

        agent = await self.session.get(Agent, position.agent_id)
        if agent is None:
            return

        now = datetime.now(timezone.utc)
        opened_ist = position.opened_at  # stored UTC; days_held is timezone-agnostic enough
        days_held = max((now.date() - opened_ist.date()).days, 0)

        entry_value = position.avg_price * Decimal(close_qty)
        net_pct = (net_pnl / entry_value * 100) if entry_value > 0 else Decimal("0")

        # R-multiple = (realized $) / (planned risk per share × qty).
        planned_risk = None
        if position.stop_loss and position.avg_price:
            risk_per_share = abs(position.avg_price - position.stop_loss)
            planned_risk = risk_per_share * Decimal(close_qty)
        r_multiple = None
        if planned_risk and planned_risk > 0:
            r_multiple = float(net_pnl / planned_risk)

        # close_reason: best-effort heuristic. The position_monitor passes the
        # actual reason via signal_metadata["close_reason"]; manual closes/legacy
        # paths default to "other".
        close_reason = signal_metadata.get("close_reason", "other")

        # Build the one-line reflection (learning-loop feedback half).
        # Entry context comes from the position's stored entry_metadata (the OPENING
        # signal), not the closing signal which only carries close_reason.
        entry_meta = position.entry_metadata or {}
        from app.agents.memory import build_reflection

        reflection = build_reflection(
            symbol=position.symbol,
            close_reason=close_reason,
            net_r=r_multiple,
            days_held=days_held,
            context_score=entry_meta.get("context_score"),
            rs_score=entry_meta.get("rs_score"),
            sector_momentum=entry_meta.get("sector_momentum"),
            net_pnl=float(net_pnl),
        )

        outcome = SignalOutcome(
            agent_id=position.agent_id,
            position_id=position.id,
            symbol=position.symbol,
            exchange=position.exchange,
            strategy_type=agent.strategy_type,
            agent_name=agent.name,
            opened_at=position.opened_at,
            closed_at=now,
            days_held=days_held,
            close_reason=close_reason,
            entry_price=position.avg_price,
            exit_price=fill_price,
            stop_loss_set=position.stop_loss,
            take_profit_set=position.take_profit,
            highest_price_seen=position.highest_price,
            quantity=close_qty,
            entry_value=entry_value,
            gross_pnl=gross_pnl,
            total_charges=total_charges,
            net_pnl=net_pnl,
            net_pnl_pct=net_pct,
            # Record the ENTRY thesis (richer for analytics) plus the close reason.
            signal_metadata={**entry_meta, "close_reason": close_reason},
            confidence_at_entry=signal_confidence,
            expected_pnl_pct=None,  # caller can pass via signal_metadata["expected_pnl_pct"]
            r_multiple=r_multiple,
            reflection=reflection,
        )
        self.session.add(outcome)
        log.info(
            "signal_outcome_logged",
            symbol=position.symbol,
            strategy=agent.strategy_type,
            close_reason=close_reason,
            net_pnl=float(net_pnl),
            r_multiple=r_multiple,
            days_held=days_held,
        )
