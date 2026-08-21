"""ProactiveAgent — primary signal originator using leading (proactive) indicators.

Unlike technical/sentiment agents which react to price/news AFTER moves happen, this agent
fires BEFORE the move by combining:
  - Relative strength rank vs NIFTY
  - Sector rotation momentum
  - 52-week high proximity (breakout candidates)
  - Volume Z-score (institutional accumulation)
  - Consolidation tightness (coiled-spring setups)

Watchlist is computed daily from D1 candles. Entry trigger is a minimal intraday confirmation
(price holding above today's open after the first hour) — not a reactive indicator.

In the restructured pipeline, sentiment + technical agents become CONFIRMERS — they can
boost or veto this agent's signals but cannot originate trades.
"""

from datetime import datetime, timezone

import structlog

from app.agents.base import BaseAgent
from app.agents.signals import MarketSnapshot, Signal
from app.data.context_scorer import ContextScore

log = structlog.get_logger()


DEFAULT_CONFIG = {
    # Score threshold for watchlist eligibility (out of 100)
    "watchlist_threshold": 60.0,
    # Score threshold for signal generation (must clear)
    "signal_threshold": 65.0,
    # Max watchlist size per scan
    "max_watchlist": 10,
    # SL is now ATR-aware: 2.0 × ATR (from ContextScorer's `tightness` = ATR/price),
    # clamped to [sl_floor_pct, sl_cap_pct]. Floor protects against too-tight stops
    # in calm regimes; cap protects against absurd stops in volatile regimes.
    "atr_sl_multiplier": 2.0,
    "sl_floor_pct": 0.04,  # never tighter than 4%
    "sl_cap_pct": 0.08,  # never wider than 8%
    "sl_pct": 0.05,  # fallback when ATR/tightness unavailable
    "tp_ratio": 2.5,
    # R-based sizing: fraction of capital risked on any single trade.
    # 0.5% means a series of 10 consecutive losers caps drawdown at 5% — survivable.
    "risk_per_trade_pct": 0.5,
    # Require price > today's open for BUY entry confirmation
    "require_open_confirmation": True,
    # ── Strategy direction ──
    # "momentum" (legacy: buy high context_score / breakouts) vs "mean_reversion"
    # (buy the most-oversold names by 52w proximity, on a green-day bounce).
    # The 5y backtest (docs/SIGNAL_EDGE_FINDINGS.md) showed momentum has NEGATIVE
    # expectancy on NIFTY-50 (it mean-reverts) and inversion flips it positive.
    "strategy_mode": "momentum",
    "mr_max_watchlist": 10,  # how many most-oversold names are eligible per scan
    "mr_tp_ratio": 2.0,  # mean-reversion target as multiple of stop distance
    "mr_confidence": 0.65,  # fixed confidence for MR picks (clears 0.60 risk gate)
}


class ProactiveAgent(BaseAgent):
    """Originator of BUY signals from a pre-computed proactive watchlist.

    Designed to fire BEFORE classical indicators react. The watchlist is computed once
    per scan from D1 data (see ContextScorer); the agent confirms entry with intraday
    price action against today's open.
    """

    def __init__(self, agent_id: str, name: str, config: dict):
        merged = {**DEFAULT_CONFIG, **config}
        super().__init__(agent_id, name, merged)
        # Watchlist is injected by the orchestrator before scan
        # (per-cycle, dict[symbol → ContextScore])
        self._watchlist: dict[str, ContextScore] = {}
        # Mean-reversion eligible set (most-oversold names), recomputed per scan.
        self._mr_picks: set[str] = set()

    def set_watchlist(self, watchlist: dict[str, ContextScore]) -> None:
        """Inject the per-scan watchlist computed by the orchestrator."""
        self._watchlist = watchlist
        # In mean-reversion mode, eligibility = the N most-oversold names (lowest 52w
        # proximity), not the highest score. Precompute that set once per scan.
        if self.config.get("strategy_mode") == "mean_reversion" and watchlist:
            n = int(self.config.get("mr_max_watchlist", 10))
            ranked = sorted(watchlist.values(), key=lambda s: s.proximity_52w)[:n]
            self._mr_picks = {s.symbol for s in ranked}
        else:
            self._mr_picks = set()

    async def analyze(self, snapshot: MarketSnapshot) -> Signal | None:
        if not self._watchlist:
            self.log.debug("proactive_no_watchlist", symbol=snapshot.symbol)
            return None

        score = self._watchlist.get(snapshot.symbol)
        if score is None:
            return None

        # Mean-reversion mode diverges here: eligibility is "most oversold", not
        # "highest score", and we build a reversion signal.
        if self.config.get("strategy_mode") == "mean_reversion":
            return self._analyze_mean_reversion(snapshot, score)

        if score.total_score < self.config["signal_threshold"]:
            return None

        # Intraday entry confirmation: price must be above today's open
        # (We don't want to buy a stock that has already gapped down or weakened intraday.)
        if self.config.get("require_open_confirmation", True):
            if snapshot.ltp <= snapshot.open or snapshot.open <= 0:
                self.log.debug(
                    "proactive_entry_unconfirmed",
                    symbol=snapshot.symbol,
                    ltp=snapshot.ltp,
                    open=snapshot.open,
                )
                return None

        # Confidence scales linearly with total_score (50→0.50, 70→0.70, 95→0.95).
        # Ensures a watchlist hit (score ≥ 60) clears the risk manager's 0.60 gate.
        confidence = max(min(score.total_score / 100.0, 0.95), 0.50)

        entry = snapshot.ltp
        tp_ratio = float(self.config["tp_ratio"])

        # ATR-aware SL: k × (ATR / price) clamped to [floor, cap]. k is REGIME-AWARE
        # (set by ContextScorer.atr_multiplier — 1.2 in trends, 1.5 default, 2.0 in chop).
        # Falls back to config default if scorer didn't populate it. score.tightness
        # is ATR14 / close from daily candles — the right per-stock volatility measure.
        sl_floor = float(self.config["sl_floor_pct"])
        sl_cap = float(self.config["sl_cap_pct"])
        atr_mult = (
            float(score.atr_multiplier)
            if score.atr_multiplier > 0
            else float(self.config["atr_sl_multiplier"])
        )
        if score.tightness and score.tightness > 0:
            sl_pct = max(sl_floor, min(sl_cap, atr_mult * score.tightness))
        else:
            sl_pct = float(self.config["sl_pct"])
        stop_loss = entry * (1 - sl_pct)
        take_profit = entry * (1 + sl_pct * tp_ratio)

        metadata = {
            "context_score": score.total_score,
            "components": score.components,
            "rs_score": round(score.rs_score, 3),
            "sector_momentum": round(score.sector_momentum, 4),
            "proximity_52w": round(score.proximity_52w, 3),
            "volume_zscore": round(score.volume_zscore, 2),
            "tightness": round(score.tightness, 4),
            "atr_multiplier": atr_mult,
            "sl_pct_applied": round(sl_pct, 4),
            "sl_method": "atr" if (score.tightness and score.tightness > 0) else "fixed_fallback",
            # Slippage-model hints — PaperBroker uses these for realistic fills.
            "realized_vol_pct": round(score.realized_vol_pct, 3),
            "adv_shares": score.adv_20,
            # Risk-budget hint — RiskManager uses for R-based sizing.
            "risk_per_trade_pct": float(self.config.get("risk_per_trade_pct", 0.5)),
            "proactive_backing": True,
            "watchlist_rank": None,  # populated by orchestrator
        }

        reasoning = (
            f"Proactive BUY (context_score={score.total_score:.0f}). "
            f"RS={score.rs_score:.2f}, sector_mom={score.sector_momentum * 100:+.1f}%, "
            f"52w_prox={score.proximity_52w:.0%}, vol_z={score.volume_zscore:+.1f}σ."
        )

        signal = Signal(
            symbol=snapshot.symbol,
            exchange=snapshot.exchange,
            action="BUY",
            confidence=round(confidence, 4),
            entry_price=entry,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            reasoning=reasoning,
            agent_id=self.agent_id,
            agent_name=self.name,
            metadata=metadata,
            timestamp=datetime.now(timezone.utc),
        )

        self.log.info(
            "proactive_signal_generated",
            symbol=snapshot.symbol,
            context_score=score.total_score,
            confidence=signal.confidence,
            ltp=entry,
        )
        return signal

    def _analyze_mean_reversion(
        self, snapshot: MarketSnapshot, score: ContextScore
    ) -> Signal | None:
        """Mean-reversion entry: buy the most-oversold names on a green-day bounce.

        Validated on 5y NIFTY-50 data (docs/SIGNAL_EDGE_FINDINGS.md): inverting the
        momentum signal flips expectancy from negative to positive (PF 1.31, Sharpe ~1).
        """
        if snapshot.symbol not in self._mr_picks:
            return None

        # Same intraday confirmation as momentum: only buy a name showing a bounce
        # (price above today's open) — don't catch a still-falling knife.
        if self.config.get("require_open_confirmation", True):
            if snapshot.ltp <= snapshot.open or snapshot.open <= 0:
                return None

        entry = snapshot.ltp
        sl_floor = float(self.config["sl_floor_pct"])
        sl_cap = float(self.config["sl_cap_pct"])
        atr_mult = (
            float(score.atr_multiplier)
            if score.atr_multiplier > 0
            else float(self.config["atr_sl_multiplier"])
        )
        if score.tightness and score.tightness > 0:
            sl_pct = max(sl_floor, min(sl_cap, atr_mult * score.tightness))
        else:
            sl_pct = float(self.config["sl_pct"])
        tp_ratio = float(self.config.get("mr_tp_ratio", 2.0))
        stop_loss = entry * (1 - sl_pct)
        take_profit = entry * (1 + sl_pct * tp_ratio)
        confidence = float(self.config.get("mr_confidence", 0.65))

        metadata = {
            "context_score": score.total_score,
            "proximity_52w": round(score.proximity_52w, 3),
            "rs_score": round(score.rs_score, 3),
            "tightness": round(score.tightness, 4),
            "atr_multiplier": atr_mult,
            "sl_pct_applied": round(sl_pct, 4),
            "sl_method": "atr" if (score.tightness and score.tightness > 0) else "fixed_fallback",
            "realized_vol_pct": round(score.realized_vol_pct, 3),
            "adv_shares": score.adv_20,
            "risk_per_trade_pct": float(self.config.get("risk_per_trade_pct", 0.5)),
            "proactive_backing": True,
            "strategy": "mean_reversion",
        }
        reasoning = (
            f"Mean-reversion BUY (oversold). 52w_prox={score.proximity_52w:.0%} "
            f"(bottom-{self.config.get('mr_max_watchlist', 10)}), RS={score.rs_score:.2f}, "
            f"bounce above open. SL {sl_pct:.1%}, TP {sl_pct * tp_ratio:.1%}."
        )
        signal = Signal(
            symbol=snapshot.symbol,
            exchange=snapshot.exchange,
            action="BUY",
            confidence=round(confidence, 4),
            entry_price=entry,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            reasoning=reasoning,
            agent_id=self.agent_id,
            agent_name=self.name,
            metadata=metadata,
            timestamp=datetime.now(timezone.utc),
        )
        self.log.info(
            "mean_reversion_signal_generated",
            symbol=snapshot.symbol,
            proximity_52w=round(score.proximity_52w, 3),
            confidence=signal.confidence,
            ltp=entry,
        )
        return signal

    def get_config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "watchlist_threshold": {"type": "number", "default": 60.0},
                "signal_threshold": {"type": "number", "default": 65.0},
                "max_watchlist": {"type": "integer", "default": 10},
                "sl_pct": {"type": "number", "default": 0.04},
                "tp_ratio": {"type": "number", "default": 2.5},
                "require_open_confirmation": {"type": "boolean", "default": True},
            },
        }
