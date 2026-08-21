"""Risk manager — gateway that approves or rejects every trade proposal."""

import time
from collections import deque
from decimal import Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.signals import Signal, TradeProposal
from app.data.cache import PriceCache
from app.data.universe import get_sector
from app.db.models import Position
from app.risk.drawdown import DrawdownMonitor
from app.risk.limits import RiskCheckResult, RiskLimits
from app.risk.position_sizer import fixed_percentage_size
from app.risk.stop_loss import atr_stop, fixed_percentage_stop

log = structlog.get_logger()


class RiskManager:
    """Central risk gateway. Every trade proposal must pass ALL checks."""

    def __init__(
        self,
        session: AsyncSession,
        cache: PriceCache,
        limits: RiskLimits | None = None,
    ):
        self.session = session
        self.cache = cache
        self.limits = limits or RiskLimits()
        self.drawdown = DrawdownMonitor(self.limits.max_drawdown_pct)
        self.drawdown.set_notify_callback(self._on_circuit_breaker_event)
        # Track order timestamps for rate limiting
        self._order_times: deque[float] = deque(maxlen=100)

    async def _on_circuit_breaker_event(
        self,
        event: str,
        drawdown_pct: float,
        equity_peak: float,
        current: float,
    ) -> None:
        """Handle circuit breaker notifications via Telegram."""
        from app.notifications.telegram import get_bot

        bot = get_bot()
        if event == "triggered":
            await bot.notify_circuit_breaker(drawdown_pct, equity_peak, current)
        elif event == "reset":
            await bot.notify_circuit_breaker_reset(drawdown_pct)
        await bot.close()

    async def evaluate(
        self,
        signal: Signal,
        agent_capital: Decimal,
        total_capital: Decimal,
    ) -> tuple[RiskCheckResult, TradeProposal | None]:
        """Run all risk checks on a signal. Returns (result, proposal_if_approved)."""
        result = RiskCheckResult(approved=True)

        # 1. Min confidence
        passed = signal.confidence >= self.limits.min_confidence
        result.checks["min_confidence"] = passed
        if not passed:
            result.rejections.append(
                f"Confidence {signal.confidence:.2f} below minimum {self.limits.min_confidence}"
            )

        # 2. Stop-loss required
        has_sl = signal.stop_loss is not None and signal.stop_loss > 0
        result.checks["stop_loss_required"] = has_sl or not self.limits.require_stop_loss
        if self.limits.require_stop_loss and not has_sl:
            result.rejections.append("Stop-loss is required but not provided")

        # 3. Circuit breaker (drawdown)
        circuit_ok = not self.drawdown.circuit_breaker_active
        result.checks["circuit_breaker"] = circuit_ok
        if not circuit_ok:
            result.rejections.append("Circuit breaker active — max drawdown exceeded")

        # Resolve any existing open position in this symbol (across ALL agents) up
        # front — both the regime filter (3b) and the position check (4) below need
        # it. (Bug fix: 3b referenced `existing` before it was defined, raising
        # UnboundLocalError on every BUY and silently blocking all new entries.)
        import uuid

        agent_uuid = uuid.UUID(signal.agent_id)
        existing_pos = await self.session.execute(
            select(Position).where(
                Position.symbol == signal.symbol,
                Position.closed_at.is_(None),
            )
        )
        existing = existing_pos.scalars().first()

        # 3b. NIFTY-regime filter (written by scanner each cycle).
        # risk_off_strong → block new BUYs entirely; risk_off → halve position size;
        # neutral/risk_on → normal. SELLs to close existing positions are NEVER blocked.
        nifty_size_scale = 1.0
        if signal.action == "BUY" and not existing:
            try:
                bias = await self.cache.redis.get("market:nifty_bias")
                if isinstance(bias, bytes):
                    bias = bias.decode()
            except Exception:
                bias = None
            if bias == "risk_off_strong":
                result.checks["nifty_regime"] = False
                result.rejections.append("NIFTY down >1.5% — risk-off, no new entries this cycle")
            elif bias == "risk_off":
                nifty_size_scale = 0.5
                result.checks["nifty_regime"] = True
            else:
                result.checks["nifty_regime"] = True

        # 4. Position check — no naked shorts, no duplicate same-direction, cap sell qty
        # (existing position + agent_uuid already resolved above for the regime filter)
        max_sell_qty = None  # Used to cap sell quantity to position size

        if existing:
            existing_side = "BUY" if existing.quantity > 0 else "SELL"
            if existing_side != signal.action:
                result.checks["duplicate_position"] = True
                # Cap sell quantity to actual position size
                max_sell_qty = existing.quantity
            else:
                # Same direction — reject (prevents buying same stock every scan cycle)
                result.checks["duplicate_position"] = False
                result.rejections.append(
                    f"Already have an open {existing_side} position on {signal.symbol}"
                )
        else:
            if signal.action == "SELL":
                result.checks["duplicate_position"] = False
                result.rejections.append(
                    f"No open position on {signal.symbol} — cannot sell without buying first"
                )
            else:
                result.checks["duplicate_position"] = True

        # 4b. Per-agent symbol cooldown (set after a position closes, lifts at next 9:15 IST)
        # Only applies to BUY signals — closing trades (SELL when position exists) bypass cooldown.
        # FAIL-CLOSED: if Redis is unreachable, treat as in-cooldown rather than silently
        # bypassing the gate. A money-safety check should default to "no" on doubt.
        if signal.action == "BUY" and not existing:
            try:
                in_cooldown = await self.cache.is_position_in_cooldown(
                    signal.agent_id, signal.symbol
                )
            except Exception:
                log.exception(
                    "cooldown_check_failed_failing_closed",
                    symbol=signal.symbol,
                    agent_id=signal.agent_id,
                )
                in_cooldown = (
                    True  # Fail closed: better to miss a trade than to re-enter on bad Redis
                )
            result.checks["cooldown"] = not in_cooldown
            if in_cooldown:
                result.rejections.append(
                    f"{signal.symbol} in cooldown after recent close (lifts at next 9:15 IST)"
                )

        # 5. Calculate position size
        atr_val = signal.metadata.get("atr")
        if atr_val and atr_val > 0:
            sl = atr_stop(signal.entry_price, atr_val, side=signal.action)
        elif signal.stop_loss > 0:
            sl = signal.stop_loss
        else:
            sl = fixed_percentage_stop(signal.entry_price, pct=3.0, side=signal.action)

        # ── Position sizing: prefer R-based (Pattern #1 / STRATEGY_RESEARCH.md) ──
        # When the signal carries a sensible SL distance, size by fixed dollar risk
        # (default 0.5% of capital). This automatically gives volatile names smaller
        # positions and calm names larger ones at the same risk — fixing the
        # 'equal-rupee flat sizing' anti-pattern.
        #
        # NIFTY-regime scaling is applied to BOTH the risk budget AND the notional cap.
        # Fallback to fixed-percentage sizing if SL is unusable.
        risk_per_trade_pct = signal.metadata.get("risk_per_trade_pct", 0.5)
        risk_budget_pct = float(risk_per_trade_pct) * nifty_size_scale
        effective_size_pct = self.limits.max_position_size_pct * Decimal(str(nifty_size_scale))

        use_r_sizing = (
            signal.action == "BUY"
            and sl is not None
            and sl > 0
            and abs(signal.entry_price - sl) > 0
        )
        if use_r_sizing:
            from app.risk.position_sizer import r_based_size

            quantity = r_based_size(
                capital=agent_capital,
                entry_price=signal.entry_price,
                stop_loss=sl,
                risk_per_trade_pct=risk_budget_pct,
                max_notional_pct=float(effective_size_pct),
            )
            sizing_method = "r_based"
        else:
            quantity = fixed_percentage_size(
                agent_capital,
                signal.entry_price,
                effective_size_pct,
            )
            sizing_method = "fixed_pct_fallback"

        if nifty_size_scale < 1.0 or sizing_method == "r_based":
            log.info(
                "position_sized",
                symbol=signal.symbol,
                method=sizing_method,
                quantity=quantity,
                risk_budget_pct=risk_budget_pct,
                effective_notional_pct=float(effective_size_pct),
                nifty_size_scale=nifty_size_scale,
                sl_distance=(signal.entry_price - sl) if sl else None,
            )

        # For closing trades, cap quantity to existing position size
        if max_sell_qty is not None and signal.action == "SELL":
            quantity = min(quantity, max_sell_qty)
        if (
            max_sell_qty is not None
            and signal.action == "BUY"
            and existing
            and existing.quantity < 0
        ):
            quantity = min(quantity, abs(existing.quantity))

        if quantity <= 0:
            result.checks["position_size"] = False
            result.rejections.append("Calculated position size is zero")
        else:
            result.checks["position_size"] = True

        order_value = Decimal(str(signal.entry_price)) * Decimal(str(quantity))
        position_pct = (order_value / agent_capital * 100) if agent_capital > 0 else Decimal("0")
        result.position_size_pct = float(position_pct)
        result.stop_loss = sl

        # 6. Max position size check (skip for closing trades — must be able to close)
        is_closing = existing is not None and (
            (existing.quantity > 0 and signal.action == "SELL")
            or (existing.quantity < 0 and signal.action == "BUY")
        )
        if not is_closing:
            size_ok = position_pct <= self.limits.max_position_size_pct
            result.checks["max_position_size"] = size_ok
            if not size_ok:
                result.rejections.append(
                    f"Position size {position_pct:.1f}% exceeds max "
                    f"{self.limits.max_position_size_pct}%"
                )
        else:
            result.checks["max_position_size"] = True

        # 7. Max single order value
        value_ok = order_value <= self.limits.max_single_order_value
        result.checks["max_order_value"] = value_ok
        if not value_ok:
            result.rejections.append(
                f"Order value ₹{order_value:,.0f} exceeds max "
                f"₹{self.limits.max_single_order_value:,.0f}"
            )

        # 8. Daily loss limit
        daily_pnl = await self.cache.get_daily_pnl()
        max_daily_loss = float(total_capital * self.limits.max_daily_loss_pct / 100)
        daily_ok = abs(daily_pnl) < max_daily_loss or daily_pnl >= 0
        result.checks["daily_loss_limit"] = daily_ok
        if not daily_ok:
            result.rejections.append(
                f"Daily loss ₹{abs(daily_pnl):,.0f} exceeds limit ₹{max_daily_loss:,.0f}"
            )

        # 8b. Max open positions per agent
        pos_count_result = await self.session.execute(
            select(func.count(Position.id)).where(
                Position.agent_id == agent_uuid,
                Position.closed_at.is_(None),
            )
        )
        open_positions = pos_count_result.scalar() or 0
        pos_ok = open_positions < self.limits.max_open_positions
        result.checks["max_open_positions"] = pos_ok
        if not pos_ok:
            result.rejections.append(
                f"Open positions {open_positions} >= max {self.limits.max_open_positions}"
            )

        # 8c. Portfolio-level GROSS EXPOSURE cap (cross-agent).
        # Previously: max_position_size_pct (10%) × max_open_positions (10) allowed
        # 100% capital deployment with no headroom. Now: 70% portfolio-level cap.
        # Counts existing open exposure + the NEW proposed order's value.
        gross_result = await self.session.execute(
            select(func.coalesce(func.sum(Position.avg_price * Position.quantity), 0)).where(
                Position.closed_at.is_(None),
            )
        )
        current_gross = Decimal(str(gross_result.scalar() or 0))
        proposed_gross = current_gross + order_value
        gross_pct = (proposed_gross / total_capital * 100) if total_capital > 0 else Decimal("0")
        gross_ok = gross_pct <= self.limits.max_gross_exposure_pct
        result.checks["max_gross_exposure"] = gross_ok
        if not gross_ok:
            result.rejections.append(
                f"Gross exposure {gross_pct:.1f}% (incl. this order) exceeds max "
                f"{self.limits.max_gross_exposure_pct}% — keeping dry powder"
            )

        # 9. Sector exposure
        sector = get_sector(signal.symbol)
        if sector:
            sector_exposure = await self._get_sector_exposure(agent_uuid, sector)
            new_exposure = sector_exposure + order_value
            sector_pct = (new_exposure / total_capital * 100) if total_capital > 0 else Decimal("0")
            sector_ok = sector_pct <= self.limits.max_sector_exposure_pct
            result.checks["sector_exposure"] = sector_ok
            if not sector_ok:
                result.rejections.append(
                    f"Sector {sector} exposure {sector_pct:.1f}% exceeds max "
                    f"{self.limits.max_sector_exposure_pct}%"
                )
        else:
            result.checks["sector_exposure"] = True

        # 10. Order rate limit
        rate_ok = self._check_rate_limit()
        result.checks["order_rate"] = rate_ok
        if not rate_ok:
            result.rejections.append(f"Order rate exceeds {self.limits.max_order_rate_per_sec}/sec")

        # Final decision
        result.approved = len(result.rejections) == 0

        proposal = None
        if result.approved:
            self._record_order_time()
            proposal = TradeProposal(
                signal=signal,
                order_type="MARKET",
                product="CNC" if signal.exchange in ("NSE", "BSE") else "NRML",
                quantity=quantity,
                stop_loss=sl,
                take_profit=signal.take_profit,
            )
            log.info(
                "trade_approved",
                symbol=signal.symbol,
                action=signal.action,
                quantity=quantity,
                sl=sl,
            )
        else:
            log.warning(
                "trade_rejected",
                symbol=signal.symbol,
                action=signal.action,
                reasons=result.rejections,
            )

        return result, proposal

    async def _get_sector_exposure(self, agent_id, sector: str) -> Decimal:
        """Calculate current exposure to a sector from open positions."""
        from app.data.universe import SECTORS

        sector_symbols = SECTORS.get(sector, [])
        if not sector_symbols:
            return Decimal("0")

        result = await self.session.execute(
            select(func.sum(Position.avg_price * Position.quantity)).where(
                Position.agent_id == agent_id,
                Position.closed_at.is_(None),
                Position.symbol.in_(sector_symbols),
            )
        )
        return Decimal(str(result.scalar() or 0))

    def _check_rate_limit(self) -> bool:
        """Check if order rate is within SEBI limit."""
        now = time.monotonic()
        # Count orders in the last second
        recent = sum(1 for t in self._order_times if now - t < 1.0)
        return recent < self.limits.max_order_rate_per_sec

    def _record_order_time(self) -> None:
        self._order_times.append(time.monotonic())
