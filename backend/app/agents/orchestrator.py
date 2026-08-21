"""Agent orchestrator — manages agent lifecycle and scan cycles."""

import asyncio
from datetime import datetime, timezone
from functools import partial

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.agents.ensemble import EnsembleAgent
from app.agents.intraday_technical import IntradayTechnicalAgent
from app.agents.proactive import ProactiveAgent
from app.agents.reasoning import ReasoningAgent
from app.agents.sentiment import SentimentAgent
from app.agents.signals import MarketSnapshot, Signal
from app.agents.technical import TechnicalAgent
from app.data.cache import PriceCache
from app.data.feeds.nse_api import nse_client
from app.db.repositories import AgentRepository, CandleRepository

log = structlog.get_logger()

# Registry: strategy_type -> agent class
AGENT_CLASSES: dict[str, type[BaseAgent]] = {
    "technical": TechnicalAgent,
    "sentiment": SentimentAgent,
    "reasoning": ReasoningAgent,
    "intraday_technical": IntradayTechnicalAgent,
    "proactive": ProactiveAgent,
}


class AgentOrchestrator:
    """Manages agent instantiation and orchestrates scan cycles."""

    def __init__(self, session: AsyncSession, cache: PriceCache):
        self.session = session
        self.cache = cache
        self.agent_repo = AgentRepository(session)
        self.candle_repo = CandleRepository(session)
        self._running_agents: dict[str, BaseAgent] = {}
        self.ensemble = EnsembleAgent()

    async def start_agent(self, agent_id: str) -> BaseAgent | None:
        """Load agent config from DB, instantiate, and start."""
        import uuid

        db_agent = await self.agent_repo.get_by_id(uuid.UUID(agent_id))
        if not db_agent:
            log.error("agent_not_found", agent_id=agent_id)
            return None

        agent_cls = AGENT_CLASSES.get(db_agent.strategy_type)
        if not agent_cls:
            log.error("unknown_strategy", strategy=db_agent.strategy_type)
            return None

        agent = agent_cls(
            agent_id=str(db_agent.id),
            name=db_agent.name,
            config=db_agent.config or {},
        )
        await agent.on_start()
        self._running_agents[agent_id] = agent
        await self.agent_repo.update_status(db_agent.id, "active")
        return agent

    async def stop_agent(self, agent_id: str) -> None:
        """Stop a running agent."""
        import uuid

        agent = self._running_agents.pop(agent_id, None)
        if agent:
            await agent.on_stop()
        await self.agent_repo.update_status(uuid.UUID(agent_id), "paused")

    async def run_scan_cycle(self) -> list[Signal]:
        """Run a full scan cycle. Proactive-first architecture:

        1. Compute ContextScorer watchlist from D1 candles (proactive signals)
        2. Inject watchlist into ProactiveAgent(s)
        3. Run ALL agents (proactive originates; technical/sentiment also run for confirmation)
        4. If ProactiveAgent is active: filter to ONLY proactive signals; apply confirm/veto
           from technical+sentiment to adjust confidence
        5. If no ProactiveAgent: fall back to legacy ensemble + reasoning flow
        """
        active_agents = await self.agent_repo.get_active()
        # Intraday strategies run in their own scan (tasks/intraday_scanner.py) — exclude here
        active_agents = [a for a in active_agents if a.strategy_type != "intraday_technical"]
        if not active_agents:
            log.info("no_active_agents")
            return []

        # Ensure all active agents are instantiated
        for db_agent in active_agents:
            aid = str(db_agent.id)
            if aid not in self._running_agents:
                await self.start_agent(aid)

        # Compute proactive watchlist if any ProactiveAgent is active
        proactive_agents = [
            a for a in self._running_agents.values() if isinstance(a, ProactiveAgent)
        ]
        watchlist = {}
        if proactive_agents:
            from app.agents.memory import SignalMemory
            from app.data.context_scorer import ContextScorer

            try:
                # Learning-loop feedback: fetch per-symbol memory multipliers from
                # recent signal_outcomes so the scorer down-weights symbols that
                # have been burning us and leans into proven winners.
                memory_multipliers = {}
                try:
                    memory_multipliers = await SignalMemory(self.session).get_multipliers()
                    biased = {s: m for s, m in memory_multipliers.items() if m != 1.0}
                    if biased:
                        log.info("memory_multipliers_applied", biased=biased)
                except Exception:
                    log.exception("memory_lookup_failed")

                scorer = ContextScorer(self.candle_repo)
                watchlist = await scorer.score_universe(memory_multipliers=memory_multipliers)
                top = sorted(watchlist.values(), key=lambda s: s.total_score, reverse=True)[:10]
                log.info(
                    "context_watchlist_built",
                    candidates=len(watchlist),
                    top_5=[(s.symbol, s.total_score) for s in top[:5]],
                )
                for pa in proactive_agents:
                    pa.set_watchlist(watchlist)
            except Exception:
                log.exception("context_scorer_failed")

        # Collect all unique symbols across agents
        all_symbols: set[str] = set()
        for db_agent in active_agents:
            symbols = self._get_agent_symbols(db_agent)
            all_symbols.update(symbols)

        snapshots = await self._fetch_snapshots(list(all_symbols))
        log.info("snapshots_fetched", count=len(snapshots))

        # Run agents concurrently. Track which agent produced each signal so we can
        # distinguish "originated" from "confirming" later.
        tasks_with_owners = []
        for db_agent in active_agents:
            agent = self._running_agents.get(str(db_agent.id))
            if not agent:
                continue
            symbols = self._get_agent_symbols(db_agent)
            for symbol in symbols:
                snap = snapshots.get(symbol)
                if snap:
                    tasks_with_owners.append((agent, self._safe_analyze(agent, snap)))

        results = await asyncio.gather(*[t for (_, t) in tasks_with_owners])
        raw_signals: list[tuple[BaseAgent, Signal]] = [
            (agent, sig) for (agent, _), sig in zip(tasks_with_owners, results) if sig is not None
        ]

        # ── Restructured pipeline: proactive-first ──
        if proactive_agents:
            # Fetch the set of currently-held symbols (cross-agent) so the proactive
            # veto logic can distinguish a "legitimate" SELL (real position to close)
            # from a noise SELL (no position, nonsense for long-only) that shouldn't
            # kill a legitimate proactive BUY.
            from sqlalchemy import select as _select

            from app.db.models import Position as _Position

            held_result = await self.session.execute(
                _select(_Position.symbol).where(_Position.closed_at.is_(None))
            )
            held_symbols = {row[0] for row in held_result.all()}

            final_signals = self._apply_proactive_first(raw_signals, held_symbols)
            log.info(
                "scan_cycle_proactive",
                raw_signals=len(raw_signals),
                final_signals=len(final_signals),
                held_count=len(held_symbols),
            )
        else:
            # Legacy path: ensemble + reasoning validation
            raw_only = [s for (_, s) in raw_signals]
            ensemble_signals = self.ensemble.combine(raw_only)
            final_signals = await self._validate_with_reasoning(ensemble_signals, snapshots)
            log.info(
                "scan_cycle_legacy",
                raw_signals=len(raw_signals),
                final_signals=len(final_signals),
            )

        all_signals: list[Signal] = []
        for signal in final_signals:
            all_signals.append(signal)
            await self.cache.set_agent_last_signal(
                signal.agent_id,
                {
                    "symbol": signal.symbol,
                    "action": signal.action,
                    "confidence": signal.confidence,
                    "timestamp": signal.timestamp.isoformat(),
                },
            )

        log.info("scan_cycle_complete", signals_generated=len(all_signals))
        return all_signals

    def _apply_proactive_first(
        self,
        raw_signals: list[tuple[BaseAgent, Signal]],
        held_symbols: set[str] | None = None,
    ) -> list[Signal]:
        """In proactive-first mode, ONLY proactive signals make it through.
        Sentiment + technical signals on the same symbol act as confirmers (boost confidence)
        or vetoers (drop the signal entirely).

        IMPORTANT (fixed 2026-05-30): a SELL signal from technical/sentiment is treated
        as a "real veto" of a proactive BUY ONLY if there is an actual open position
        for that symbol. In long-only mode, a SELL on a no-position symbol is nonsense
        (you can't sell what you don't hold) — treating it as a veto previously killed
        legitimate proactive re-entries on stocks that had just been closed.
        """
        proactive_signals = [s for a, s in raw_signals if isinstance(a, ProactiveAgent)]
        if not proactive_signals:
            return []
        held_symbols = held_symbols or set()

        # Group non-proactive signals by symbol for fast lookup
        confirmer_signals_by_symbol: dict[str, list[tuple[BaseAgent, Signal]]] = {}
        for agent, sig in raw_signals:
            if isinstance(agent, ProactiveAgent):
                continue
            confirmer_signals_by_symbol.setdefault(sig.symbol, []).append((agent, sig))

        final: list[Signal] = []
        for ps in proactive_signals:
            confirmers = confirmer_signals_by_symbol.get(ps.symbol, [])
            tech_confirm = sent_confirm = False
            tech_veto = sent_veto = False
            tech_sell_ignored = sent_sell_ignored = False
            has_position = ps.symbol in held_symbols
            for agent, csig in confirmers:
                if isinstance(agent, TechnicalAgent):
                    if csig.action == ps.action:
                        tech_confirm = True
                    elif csig.action == "SELL":
                        # Only a real veto if there's actually a position to sell
                        if has_position:
                            tech_veto = True
                        else:
                            tech_sell_ignored = True
                elif isinstance(agent, SentimentAgent):
                    if csig.action == ps.action:
                        sent_confirm = True
                    elif csig.action == "SELL":
                        if has_position:
                            sent_veto = True
                        else:
                            sent_sell_ignored = True

            if tech_veto or sent_veto:
                log.info(
                    "proactive_vetoed",
                    symbol=ps.symbol,
                    tech_veto=tech_veto,
                    sent_veto=sent_veto,
                    has_position=has_position,
                )
                continue
            if tech_sell_ignored or sent_sell_ignored:
                log.info(
                    "proactive_phantom_sell_ignored",
                    symbol=ps.symbol,
                    tech_sell_ignored=tech_sell_ignored,
                    sent_sell_ignored=sent_sell_ignored,
                )

            # Boost confidence on confirmation
            boost = 1.0
            if tech_confirm:
                boost *= 1.15
            if sent_confirm:
                boost *= 1.10
            adjusted = min(ps.confidence * boost, 0.99)
            ps.confidence = round(adjusted, 4)
            ps.metadata["confirmations"] = {
                "technical": tech_confirm,
                "sentiment": sent_confirm,
            }
            log.info(
                "proactive_final",
                symbol=ps.symbol,
                confidence=ps.confidence,
                tech_confirm=tech_confirm,
                sent_confirm=sent_confirm,
            )
            final.append(ps)

        return final

    async def _validate_with_reasoning(
        self,
        signals: list[Signal],
        snapshots: dict[str, MarketSnapshot],
    ) -> list[Signal]:
        """Validate signals using the Gemini reasoning agent.

        - If a reasoning agent is configured, each signal gets a second opinion.
        - Signals where the LLM DISAGREES are dropped.
        - Confidence is adjusted based on the LLM's assessment.
        - If no reasoning agent is available, signals pass through unchanged.
        """
        if not signals:
            return signals

        # Find the first active reasoning agent instance
        reasoning_agent: ReasoningAgent | None = None
        for agent in self._running_agents.values():
            if isinstance(agent, ReasoningAgent):
                reasoning_agent = agent
                break

        if reasoning_agent is None:
            # No reasoning agent configured — create a default one for validation
            from app.config import settings

            if settings.gemini_api_key:
                reasoning_agent = ReasoningAgent(
                    agent_id="system-reasoning",
                    name="Gemini Validator",
                    config={},
                )
            else:
                log.info("no_reasoning_agent", reason="no_api_key")
                return signals

        validated: list[Signal] = []
        for signal in signals:
            snapshot = snapshots.get(signal.symbol)
            if not snapshot:
                validated.append(signal)
                continue

            # --- Confidence gate: metadata-aware ---
            # Technical-backed signals (have "votes" in metadata) with strong confidence
            # skip Gemini. Sentiment-only signals with very high confidence also skip
            # (Gemini systematically rejects sentiment-only signals).
            has_technical_backing = signal.metadata.get("votes") is not None
            headlines_count = signal.metadata.get("headlines_count", 0)

            if signal.confidence >= 0.60 and has_technical_backing:
                log.info(
                    "reasoning_skip_technical", symbol=signal.symbol, confidence=signal.confidence
                )
                validated.append(signal)
                continue
            if signal.confidence >= 0.65 and headlines_count >= 2:
                # Sentiment signal with strong keyword match and multiple headlines
                log.info(
                    "reasoning_skip_sentiment_strong",
                    symbol=signal.symbol,
                    confidence=signal.confidence,
                    headlines=headlines_count,
                )
                validated.append(signal)
                continue
            if signal.confidence < 0.50:
                log.info(
                    "reasoning_drop_low_confidence",
                    symbol=signal.symbol,
                    confidence=signal.confidence,
                )
                continue

            # Skip expensive LLM call for SELL signals with no position to close
            if signal.action == "SELL":
                import uuid

                from sqlalchemy import select

                from app.db.models.position import Position

                agent_uuid = (
                    uuid.UUID(signal.agent_id) if signal.agent_id != "system-reasoning" else None
                )
                if agent_uuid:
                    pos_result = await self.session.execute(
                        select(Position).where(
                            Position.agent_id == agent_uuid,
                            Position.symbol == signal.symbol,
                            Position.closed_at.is_(None),
                        )
                    )
                    if not pos_result.scalars().first():
                        log.debug("skip_reasoning_no_position", symbol=signal.symbol, action="SELL")
                        validated.append(signal)
                        continue

            try:
                result = await reasoning_agent.validate_signal(signal, snapshot)

                validation = result.get("validation", "AGREE")
                adjusted_conf = result.get("adjusted_confidence", signal.confidence)
                reasoning = result.get("reasoning", "")
                risk_flags = result.get("risk_flags", [])

                if validation == "DISAGREE":
                    log.info(
                        "signal_rejected_by_llm",
                        symbol=signal.symbol,
                        action=signal.action,
                        reasoning=reasoning[:100],
                    )
                    continue

                # Update signal with LLM's adjustments
                signal.confidence = adjusted_conf
                signal.reasoning = f"{signal.reasoning}\n\n[Gemini]: {reasoning}"

                # Apply suggested SL/TP if LLM recommends tighter levels
                suggested_sl = result.get("suggested_stop_loss")
                suggested_tp = result.get("suggested_take_profit")
                if suggested_sl is not None:
                    signal.stop_loss = suggested_sl
                if suggested_tp is not None:
                    signal.take_profit = suggested_tp

                # Store risk flags in metadata
                if signal.metadata is None:
                    signal.metadata = {}
                signal.metadata["llm_validation"] = validation
                signal.metadata["llm_risk_flags"] = risk_flags

                validated.append(signal)

            except Exception:
                log.exception("reasoning_validation_error", symbol=signal.symbol)
                # On error, pass the signal through unchanged
                validated.append(signal)

        log.info(
            "reasoning_validation_complete",
            input_signals=len(signals),
            output_signals=len(validated),
        )
        return validated

    async def _safe_analyze(self, agent: BaseAgent, snapshot: MarketSnapshot) -> Signal | None:
        """Run agent.analyze() with error handling."""
        try:
            return await agent.analyze(snapshot)
        except Exception:
            log.exception(
                "agent_analyze_error",
                agent_id=agent.agent_id,
                symbol=snapshot.symbol,
            )
            return None

    async def _fetch_snapshots(self, symbols: list[str]) -> dict[str, MarketSnapshot]:
        """Fetch current market data for a list of symbols."""
        # Build a quote map: symbol -> quote dict
        quotes: dict[str, dict] = {}

        # 1. Check Redis cache for all symbols first
        uncached: list[str] = []
        for symbol in symbols:
            cached = await self.cache.get_quote("NSE", symbol)
            if cached:
                quotes[symbol] = cached
            else:
                uncached.append(symbol)

        # 2. Batch-fetch uncached symbols via Kite API (one call for all symbols)
        if uncached:
            from app.execution.broker.kite import get_kite_broker

            broker = get_kite_broker()

            # Sync the per-process broker to the authoritative Redis token
            # (written by auto_auth / FastAPI after auth). Refresh whenever it
            # diverges so a stale prior-day token can't linger in memory.
            token = await self.cache.get_kite_token()
            if token and broker.access_token != token:
                broker.set_access_token(token)
                log.info("kite_token_loaded_from_redis")

            if broker.access_token:
                instruments = [f"NSE:{s}" for s in uncached]
                try:
                    loop = asyncio.get_event_loop()
                    kite_quotes = await loop.run_in_executor(
                        None, partial(broker.kite.quote, instruments)
                    )
                    fetched_via_kite: list[str] = []
                    for symbol in uncached:
                        q = kite_quotes.get(f"NSE:{symbol}", {})
                        if q:
                            quote = {
                                "ltp": q.get("last_price", 0),
                                "open": q.get("ohlc", {}).get("open", 0),
                                "high": q.get("ohlc", {}).get("high", 0),
                                "low": q.get("ohlc", {}).get("low", 0),
                                "close": q.get("ohlc", {}).get("close", 0),
                                "volume": q.get("volume", 0),
                            }
                            quotes[symbol] = quote
                            fetched_via_kite.append(symbol)
                    # Cache results for other agents in the same scan
                    await self.cache.set_bulk_quotes(
                        "NSE", {s: quotes[s] for s in fetched_via_kite}
                    )
                    still_missing = [s for s in uncached if s not in quotes]
                    log.info(
                        "kite_batch_quote",
                        fetched=len(fetched_via_kite),
                        missing=len(still_missing),
                    )
                    uncached = still_missing
                except Exception as e:
                    log.warning("kite_batch_quote_failed", error=str(e)[:100])

        # 3. NSE API only for symbols still missing after Kite attempt
        if uncached:
            log.warning("nse_api_fallback", symbols=len(uncached))
            for symbol in uncached:
                try:
                    quote = await nse_client.get_quote(symbol)
                    if quote:
                        quotes[symbol] = quote
                        await self.cache.set_quote("NSE", symbol, quote)
                except Exception:
                    log.exception("nse_quote_error", symbol=symbol)

        # 4. Build MarketSnapshot for each symbol that has a quote
        snapshots: dict[str, MarketSnapshot] = {}
        for symbol in symbols:
            quote = quotes.get(symbol)
            if not quote:
                continue
            try:
                ltp = float(quote.get("ltp", 0))
                candles = await self.candle_repo.get_candles(
                    symbol=symbol, exchange="NSE", timeframe="1d", limit=250
                )
                candles_dicts = [
                    {
                        "time": c.time,
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": c.volume,
                    }
                    for c in reversed(candles)
                ]
                snapshots[symbol] = MarketSnapshot(
                    symbol=symbol,
                    exchange="NSE",
                    ltp=ltp,
                    open=float(quote.get("open", ltp)),
                    high=float(quote.get("high", ltp)),
                    low=float(quote.get("low", ltp)),
                    close=float(quote.get("close", ltp)),
                    volume=int(quote.get("volume", 0)),
                    candles_1d=candles_dicts,
                    timestamp=datetime.now(timezone.utc),
                )
            except Exception:
                log.exception("snapshot_build_error", symbol=symbol)

        return snapshots

    def _get_agent_symbols(self, db_agent) -> list[str]:
        """Extract symbol list from agent's universe config."""
        universe = db_agent.universe
        if not universe:
            return []
        if isinstance(universe, list):
            return universe
        if isinstance(universe, dict):
            # {"index": "NIFTY50"} or {"symbols": ["RELIANCE", "TCS"]}
            if "index" in universe:
                from app.data.universe import get_universe

                return get_universe(universe["index"])
            if "symbols" in universe:
                return universe["symbols"]
        return []
