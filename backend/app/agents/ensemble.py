"""Ensemble agent — combines signals from multiple agents via weighted voting."""

from collections import defaultdict
from datetime import datetime, timezone

import structlog

from app.agents.signals import Signal

log = structlog.get_logger()

# Default weights per strategy type — technical dominant, sentiment secondary
DEFAULT_WEIGHTS: dict[str, float] = {
    "technical": 3.0,
    "sentiment": 0.5,
}

# Minimum weighted confidence to emit a signal (lowered since technical is now regime-aware)
DEFAULT_MIN_ENSEMBLE_CONFIDENCE = 0.45

# Minimum number of agreeing agents (by action) before emitting
DEFAULT_MIN_AGREEMENT = 1


class EnsembleAgent:
    """Combines signals from multiple agents into consensus signals.

    Not a BaseAgent subclass — this is a pure aggregation layer called
    by the orchestrator between raw signal collection and LLM validation.

    Algorithm:
        1. Group raw signals by symbol.
        2. For each symbol, tally weighted votes for BUY / SELL.
        3. If the dominant action meets the minimum agreement count and
           the weighted average confidence exceeds the threshold, emit
           a single ensemble signal.
        4. SL/TP are taken from the highest-confidence contributing signal
           (the "lead agent"), optionally tightened.
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.weights = cfg.get("weights", DEFAULT_WEIGHTS)
        self.min_confidence = cfg.get("min_ensemble_confidence", DEFAULT_MIN_ENSEMBLE_CONFIDENCE)
        self.min_agreement = cfg.get("min_agreement", DEFAULT_MIN_AGREEMENT)

    def combine(self, signals: list[Signal]) -> list[Signal]:
        """Combine raw signals into ensemble consensus signals.

        Returns one Signal per symbol at most.
        """
        if not signals:
            return []

        grouped = self._group_by_symbol(signals)
        ensemble_signals: list[Signal] = []

        for symbol, sym_signals in grouped.items():
            result = self._vote(symbol, sym_signals)
            if result is not None:
                ensemble_signals.append(result)

        log.info(
            "ensemble_combine",
            input_signals=len(signals),
            symbols=len(grouped),
            output_signals=len(ensemble_signals),
        )
        return ensemble_signals

    def _group_by_symbol(self, signals: list[Signal]) -> dict[str, list[Signal]]:
        grouped: dict[str, list[Signal]] = defaultdict(list)
        for sig in signals:
            grouped[sig.symbol].append(sig)
        return grouped

    def _vote(self, symbol: str, signals: list[Signal]) -> Signal | None:
        """Weighted vote across signals for a single symbol."""
        buy_weight = 0.0
        sell_weight = 0.0
        buy_signals: list[Signal] = []
        sell_signals: list[Signal] = []

        for sig in signals:
            agent_type = self._infer_agent_type(sig)
            w = self.weights.get(agent_type, 1.0)
            weighted_conf = sig.confidence * w

            if sig.action == "BUY":
                buy_weight += weighted_conf
                buy_signals.append(sig)
            elif sig.action == "SELL":
                sell_weight += weighted_conf
                sell_signals.append(sig)
            # HOLD signals are ignored in voting

        # Determine dominant direction
        if buy_weight > sell_weight and len(buy_signals) >= self.min_agreement:
            action = "BUY"
            winning_signals = buy_signals
        elif sell_weight > buy_weight and len(sell_signals) >= self.min_agreement:
            action = "SELL"
            winning_signals = sell_signals
        else:
            log.debug(
                "ensemble_no_consensus",
                symbol=symbol,
                buy_weight=round(buy_weight, 3),
                sell_weight=round(sell_weight, 3),
                buy_count=len(buy_signals),
                sell_count=len(sell_signals),
            )
            return None

        # Weighted average confidence
        weight_sum = sum(self.weights.get(self._infer_agent_type(s), 1.0) for s in winning_signals)
        if weight_sum == 0:
            return None

        avg_confidence = (
            sum(
                s.confidence * self.weights.get(self._infer_agent_type(s), 1.0)
                for s in winning_signals
            )
            / weight_sum
        )

        if avg_confidence < self.min_confidence:
            log.debug(
                "ensemble_low_confidence",
                symbol=symbol,
                action=action,
                avg_confidence=round(avg_confidence, 3),
                threshold=self.min_confidence,
            )
            return None

        # Lead agent = highest individual confidence
        lead = max(winning_signals, key=lambda s: s.confidence)

        # Build merged reasoning
        reasoning_parts = []
        for s in winning_signals:
            agent_type = self._infer_agent_type(s)
            reasoning_parts.append(
                f"[{agent_type}:{s.agent_name}] (conf={s.confidence:.2f}) {s.reasoning[:150]}"
            )
        merged_reasoning = (
            f"Ensemble {action} — {len(winning_signals)} agents agree.\n"
            + "\n".join(reasoning_parts)
        )

        # Build merged metadata
        contributing = [
            {
                "agent_id": s.agent_id,
                "agent_name": s.agent_name,
                "agent_type": self._infer_agent_type(s),
                "confidence": s.confidence,
                "action": s.action,
            }
            for s in winning_signals
        ]

        opposing = [
            {
                "agent_id": s.agent_id,
                "agent_name": s.agent_name,
                "agent_type": self._infer_agent_type(s),
                "confidence": s.confidence,
                "action": s.action,
            }
            for s in signals
            if s not in winning_signals and s.action != "HOLD"
        ]

        ensemble_signal = Signal(
            agent_id=lead.agent_id,
            agent_name=f"ensemble({lead.agent_name})",
            symbol=symbol,
            exchange=lead.exchange,
            action=action,
            confidence=round(avg_confidence, 4),
            entry_price=lead.entry_price,
            stop_loss=lead.stop_loss,
            take_profit=lead.take_profit,
            reasoning=merged_reasoning,
            metadata={
                "ensemble": True,
                "contributing_agents": contributing,
                "opposing_agents": opposing,
                "buy_weight": round(buy_weight, 3),
                "sell_weight": round(sell_weight, 3),
                "lead_agent": lead.agent_name,
                **(lead.metadata or {}),
            },
            timestamp=datetime.now(timezone.utc),
        )

        log.info(
            "ensemble_signal",
            symbol=symbol,
            action=action,
            confidence=round(avg_confidence, 4),
            contributors=len(winning_signals),
            opposing=len(opposing),
        )
        return ensemble_signal

    def _infer_agent_type(self, signal: Signal) -> str:
        """Infer agent type from the signal's agent_name or metadata."""
        name_lower = signal.agent_name.lower()
        for agent_type in self.weights:
            if agent_type in name_lower:
                return agent_type
        # Check metadata
        return signal.metadata.get("agent_type", "unknown")
