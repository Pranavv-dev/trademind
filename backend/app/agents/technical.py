"""Technical analysis agent — generates signals from regime-aware indicator voting."""

from datetime import datetime, timezone

import pandas as pd

from app.agents.base import BaseAgent
from app.agents.signals import MarketSnapshot, Signal
from app.data.indicators import (
    calculate_all,
    calculate_atr_stop,
    classify_regime,
    get_52w_proximity_signal,
    get_bb_signal,
    get_macd_signal,
    get_obv_signal,
    get_rsi_signal,
    get_sma_crossover_signal,
    get_vwap_signal,
)

# Base weights — adjusted per regime at runtime
DEFAULT_WEIGHTS = {
    "rsi": 1.5,
    "macd": 2.0,
    "bb": 1.0,
    "sma_20_50": 1.5,
    "sma_50_200": 2.0,
    "vwap": 0.5,
    "obv": 1.0,
    "proximity_52w": 0.5,
}

# Regime-specific weight multipliers
REGIME_WEIGHT_MODS = {
    "trending_up": {"rsi": 0.5, "bb": 0.5, "macd": 1.5, "sma_20_50": 1.2, "sma_50_200": 1.5},
    "trending_down": {"rsi": 0.5, "bb": 0.5, "macd": 1.5, "sma_20_50": 1.2, "sma_50_200": 1.5},
    "ranging": {"rsi": 2.0, "bb": 2.0, "macd": 0.5, "sma_20_50": 0.0, "sma_50_200": 0.0},
    "volatile": {},  # All weights halved (applied separately)
}

DEFAULT_CONFIG = {
    "rsi_period": 14,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "bb_period": 20,
    "bb_std": 2,
    "atr_period": 14,
    "atr_sl_multiplier": 2.0,
    "tp_ratio": 2.0,
    "signal_threshold": 0.20,
    "weights": DEFAULT_WEIGHTS,
    "long_only": True,  # No short selling in CNC mode
}


class TechnicalAgent(BaseAgent):
    """Generates trading signals from regime-aware technical indicator consensus."""

    def __init__(self, agent_id: str, name: str, config: dict):
        merged = {**DEFAULT_CONFIG, **config}
        super().__init__(agent_id, name, merged)

    async def analyze(self, snapshot: MarketSnapshot) -> Signal | None:
        df = self._build_dataframe(snapshot)
        if df is None or len(df) < 50:
            self.log.debug(
                "insufficient_data", symbol=snapshot.symbol, rows=len(df) if df is not None else 0
            )
            return None

        df = calculate_all(df)
        last = df.iloc[-1]
        prev = df.iloc[-2]

        # Detect market regime
        regime = classify_regime(
            adx=_safe_float(last.get("ADX_14")),
            atr=_safe_float(last.get("atr_14")),
            atr_sma=_safe_float(last.get("atr_sma_20")),
            sma_20=_safe_float(last.get("sma_20")),
            sma_50=_safe_float(last.get("sma_50")),
            sma_200=_safe_float(last.get("sma_200")),
        )

        votes = self._vote(last, prev, df)
        weights = self._get_regime_weights(regime)
        score = sum(votes.get(k, 0) * weights.get(k, 0) for k in weights)
        max_score = sum(abs(w) for w in weights.values())
        confidence = abs(score) / max_score if max_score > 0 else 0

        if abs(score) < self.config["signal_threshold"] * max_score:
            self.log.debug(
                "below_threshold",
                symbol=snapshot.symbol,
                score=round(score, 3),
                confidence=round(confidence, 3),
                regime=regime,
            )
            return None

        action = "BUY" if score > 0 else "SELL"

        # Long-only bear market logic: suppress SELL signals (can't execute without position)
        if self.config.get("long_only", True) and action == "SELL" and regime == "trending_down":
            self.log.debug(
                "suppress_sell_bear_regime", symbol=snapshot.symbol, score=round(score, 3)
            )
            return None

        # In trending_down + long_only: only allow BUY if at least one oversold indicator fires
        if self.config.get("long_only", True) and action == "BUY" and regime == "trending_down":
            rsi = _safe_float(last.get("rsi_14"))
            bb_vote = votes.get("bb", 0)
            obv_vote = votes.get("obv", 0)
            oversold_count = sum(
                1
                for v in [
                    1 if (rsi is not None and rsi < 35) else 0,
                    1 if bb_vote > 0 else 0,
                    1 if obv_vote > 0 else 0,
                ]
                if v > 0
            )
            if oversold_count < 1:
                self.log.debug(
                    "suppress_buy_not_oversold",
                    symbol=snapshot.symbol,
                    oversold_count=oversold_count,
                    regime=regime,
                )
                return None

        atr = last.get("atr_14")
        entry_price = snapshot.ltp
        sl = calculate_atr_stop(
            entry_price, _safe_float(atr), self.config["atr_sl_multiplier"], side=action
        )
        if sl is None:
            sl = entry_price * (0.97 if action == "BUY" else 1.03)

        sl_distance = abs(entry_price - sl)
        if action == "BUY":
            tp = entry_price + sl_distance * self.config["tp_ratio"]
        else:
            tp = entry_price - sl_distance * self.config["tp_ratio"]

        metadata = self._build_metadata(last, prev, votes, score, regime, weights)

        signal = Signal(
            symbol=snapshot.symbol,
            exchange=snapshot.exchange,
            action=action,
            confidence=round(confidence, 4),
            entry_price=entry_price,
            stop_loss=round(sl, 2),
            take_profit=round(tp, 2),
            reasoning=self._build_reasoning(action, votes, confidence, regime),
            agent_id=self.agent_id,
            agent_name=self.name,
            metadata=metadata,
            timestamp=datetime.now(timezone.utc),
        )

        self.log.info(
            "signal_generated",
            symbol=snapshot.symbol,
            action=action,
            confidence=signal.confidence,
            score=round(score, 3),
            regime=regime,
        )
        return signal

    def get_config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "signal_threshold": {"type": "number", "default": 0.20},
                "atr_sl_multiplier": {"type": "number", "default": 2.0},
                "tp_ratio": {"type": "number", "default": 2.0},
                "long_only": {"type": "boolean", "default": True},
            },
        }

    # ── Private helpers ──

    def _build_dataframe(self, snapshot: MarketSnapshot) -> pd.DataFrame | None:
        candles = snapshot.candles_1d or snapshot.candles_5m
        if not candles:
            return None
        df = pd.DataFrame(candles)
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
        return df

    def _vote(self, last: pd.Series, prev: pd.Series, df: pd.DataFrame) -> dict[str, int]:
        """Each indicator casts a vote: +1 (BUY), -1 (SELL), 0 (NEUTRAL)."""
        votes = {}
        votes["rsi"] = get_rsi_signal(last.get("rsi_14"))
        votes["macd"] = get_macd_signal(
            last.get("MACD_12_26_9"),
            last.get("MACDs_12_26_9"),
            prev.get("MACD_12_26_9"),
            prev.get("MACDs_12_26_9"),
        )
        votes["bb"] = get_bb_signal(
            last.get("close", 0), last.get("BBL_20_2.0"), last.get("BBU_20_2.0")
        )
        votes["sma_20_50"] = get_sma_crossover_signal(
            last.get("sma_20"),
            last.get("sma_50"),
            prev.get("sma_20"),
            prev.get("sma_50"),
        )
        votes["sma_50_200"] = get_sma_crossover_signal(
            last.get("sma_50"),
            last.get("sma_200"),
            prev.get("sma_50"),
            prev.get("sma_200"),
        )
        votes["vwap"] = get_vwap_signal(last.get("close", 0), last.get("vwap"))
        votes["obv"] = get_obv_signal(
            _safe_float(last.get("obv")),
            _safe_float(last.get("obv_sma_20")),
            _safe_float(last.get("close")),
            _safe_float(prev.get("close")),
        )
        votes["proximity_52w"] = get_52w_proximity_signal(df, rsi=_safe_float(last.get("rsi_14")))
        return votes

    def _get_regime_weights(self, regime: str) -> dict[str, float]:
        """Adjust indicator weights based on market regime."""
        base = dict(self.config["weights"])
        mods = REGIME_WEIGHT_MODS.get(regime, {})

        for indicator, multiplier in mods.items():
            if indicator in base:
                base[indicator] = base[indicator] * multiplier

        # In volatile regime: halve all weights (reduce confidence)
        if regime == "volatile":
            base = {k: v * 0.5 for k, v in base.items()}

        return base

    def _build_metadata(
        self,
        last: pd.Series,
        prev: pd.Series,
        votes: dict[str, int],
        score: float,
        regime: str,
        weights: dict[str, float],
    ) -> dict:
        return {
            "rsi": _safe_float(last.get("rsi_14")),
            "macd": _safe_float(last.get("MACD_12_26_9")),
            "macd_signal": _safe_float(last.get("MACDs_12_26_9")),
            "bb_upper": _safe_float(last.get("BBU_20_2.0")),
            "bb_lower": _safe_float(last.get("BBL_20_2.0")),
            "sma_20": _safe_float(last.get("sma_20")),
            "sma_50": _safe_float(last.get("sma_50")),
            "sma_200": _safe_float(last.get("sma_200")),
            "atr": _safe_float(last.get("atr_14")),
            "adx": _safe_float(last.get("ADX_14")),
            "obv": _safe_float(last.get("obv")),
            "close": _safe_float(last.get("close")),
            "volume": int(last.get("volume", 0)),
            "regime": regime,
            "votes": votes,
            "regime_weights": {k: round(v, 2) for k, v in weights.items()},
            "weighted_score": round(score, 4),
        }

    def _build_reasoning(
        self,
        action: str,
        votes: dict[str, int],
        confidence: float,
        regime: str,
    ) -> str:
        bullish = [k for k, v in votes.items() if v > 0]
        bearish = [k for k, v in votes.items() if v < 0]
        parts = [f"Technical {action} (conf: {confidence:.0%}, regime: {regime})."]
        if bullish:
            parts.append(f"Bullish: {', '.join(bullish)}.")
        if bearish:
            parts.append(f"Bearish: {', '.join(bearish)}.")
        return " ".join(parts)


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if pd.isna(f):
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None
