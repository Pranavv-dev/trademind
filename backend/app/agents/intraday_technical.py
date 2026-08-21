"""Intraday technical agent — mirrors TechnicalAgent weights and logic but runs on intraday bars.

- 15-min candles for regime classification (ADX, ATR, SMA alignment)
- 5-min candles for indicator voting (RSI, MACD, BB, SMA cross, OBV, VWAP, 52w proximity)

Completely isolated from the daily TechnicalAgent: imports weights explicitly to stay in lockstep
but runs in its own scan cycle (see tasks/intraday_scanner.py) with no ensemble / no reasoning.
"""

from datetime import datetime, timezone

import pandas as pd

from app.agents.base import BaseAgent
from app.agents.signals import MarketSnapshot, Signal
from app.agents.technical import (
    DEFAULT_WEIGHTS,
    REGIME_WEIGHT_MODS,
    _safe_float,
)
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

# Minimum bars required before signals can be emitted.
# Both must be ≥50 because indicators.calculate_all() returns df unchanged
# when len(df) < 50 — so regime classification and voting would get Nones.
# 5m: 50 bars = ~4 hours of session data (Day 1 afternoon).
# 15m: 50 bars = ~12.5 hours of session data (Day 2 afternoon).
MIN_BARS_5M = 50
# Soft floor for 15m. Agent works with 5m-derived regime until 15m reaches 50.
MIN_BARS_15M_FOR_REGIME = 50

DEFAULT_CONFIG = {
    "signal_threshold": 0.20,
    "atr_sl_multiplier": 2.0,
    "tp_ratio": 2.0,
    "long_only": True,
    "weights": DEFAULT_WEIGHTS,
}


class IntradayTechnicalAgent(BaseAgent):
    """Multi-timeframe technical agent operating on 5m + 15m bars.

    Same 8 indicators and weight ratio as the daily TechnicalAgent — the difference
    is the timeframe of the data. Regime is classified on 15m (more stable); entry
    votes are computed on 5m (higher resolution for timing).
    """

    def __init__(self, agent_id: str, name: str, config: dict):
        merged = {**DEFAULT_CONFIG, **config}
        super().__init__(agent_id, name, merged)

    async def analyze(self, snapshot: MarketSnapshot) -> Signal | None:
        df_5m = self._build_dataframe(snapshot.candles_5m)
        df_15m = self._build_dataframe(snapshot.candles_15m)

        bars_5m = len(df_5m) if df_5m is not None else 0
        bars_15m = len(df_15m) if df_15m is not None else 0

        # 5m is the required minimum — it drives voting.
        if bars_5m < MIN_BARS_5M:
            self.log.info(
                "intraday_warmup",
                symbol=snapshot.symbol,
                bars_5m=bars_5m,
                bars_15m=bars_15m,
                needed_5m=MIN_BARS_5M,
            )
            return None

        df_5m = calculate_all(df_5m)
        df_15m_raw = df_15m

        # Regime: prefer 15m (steadier) once it has enough bars.
        # Fall back to 5m when 15m is still warming up.
        regime_source = "15m"
        if df_15m_raw is not None and bars_15m >= MIN_BARS_15M_FOR_REGIME:
            df_15m = calculate_all(df_15m_raw)
            regime_row = df_15m.iloc[-1]
        else:
            regime_source = "5m_fallback"
            regime_row = df_5m.iloc[-1]

        regime = classify_regime(
            adx=_safe_float(regime_row.get("ADX_14")),
            atr=_safe_float(regime_row.get("atr_14")),
            atr_sma=_safe_float(regime_row.get("atr_sma_20")),
            sma_20=_safe_float(regime_row.get("sma_20")),
            sma_50=_safe_float(regime_row.get("sma_50")),
            sma_200=_safe_float(regime_row.get("sma_200")),
        )

        last_5 = df_5m.iloc[-1]
        prev_5 = df_5m.iloc[-2]

        votes = self._vote(last_5, prev_5, df_5m)
        weights = self._get_regime_weights(regime)
        score = sum(votes.get(k, 0) * weights.get(k, 0) for k in weights)
        max_score = sum(abs(w) for w in weights.values())
        confidence = abs(score) / max_score if max_score > 0 else 0

        if abs(score) < self.config["signal_threshold"] * max_score:
            self.log.debug(
                "intraday_below_threshold",
                symbol=snapshot.symbol,
                score=round(score, 3),
                confidence=round(confidence, 3),
                regime=regime,
            )
            return None

        action = "BUY" if score > 0 else "SELL"

        # Long-only guards — same logic as daily TechnicalAgent
        if self.config.get("long_only", True) and action == "SELL" and regime == "trending_down":
            self.log.debug(
                "intraday_suppress_sell_bear", symbol=snapshot.symbol, score=round(score, 3)
            )
            return None

        if self.config.get("long_only", True) and action == "BUY" and regime == "trending_down":
            rsi = _safe_float(last_5.get("rsi_14"))
            bb_vote = votes.get("bb", 0)
            obv_vote = votes.get("obv", 0)
            oversold = sum(
                1
                for v in [
                    1 if (rsi is not None and rsi < 35) else 0,
                    1 if bb_vote > 0 else 0,
                    1 if obv_vote > 0 else 0,
                ]
                if v > 0
            )
            if oversold < 1:
                self.log.debug(
                    "intraday_suppress_buy_not_oversold",
                    symbol=snapshot.symbol,
                    oversold=oversold,
                    regime=regime,
                )
                return None

        atr = _safe_float(last_5.get("atr_14"))
        entry = snapshot.ltp
        sl = calculate_atr_stop(entry, atr, self.config["atr_sl_multiplier"], side=action)
        if sl is None:
            sl = entry * (0.97 if action == "BUY" else 1.03)

        sl_dist = abs(entry - sl)
        tp = (
            entry + sl_dist * self.config["tp_ratio"]
            if action == "BUY"
            else entry - sl_dist * self.config["tp_ratio"]
        )

        metadata = {
            "rsi": _safe_float(last_5.get("rsi_14")),
            "macd": _safe_float(last_5.get("MACD_12_26_9")),
            "macd_signal": _safe_float(last_5.get("MACDs_12_26_9")),
            "bb_upper": _safe_float(last_5.get("BBU_20_2.0")),
            "bb_lower": _safe_float(last_5.get("BBL_20_2.0")),
            "sma_20": _safe_float(last_5.get("sma_20")),
            "sma_50": _safe_float(last_5.get("sma_50")),
            "atr": atr,
            "adx_regime": _safe_float(regime_row.get("ADX_14")),
            "close": _safe_float(last_5.get("close")),
            "volume": int(last_5.get("volume", 0) or 0),
            "regime": regime,
            "regime_source": regime_source,
            "votes": votes,
            "regime_weights": {k: round(v, 2) for k, v in weights.items()},
            "weighted_score": round(score, 4),
            "timeframe": "intraday",
            "timeframes_used": ["5m", "15m"] if regime_source == "15m" else ["5m"],
            "bars_5m": bars_5m,
            "bars_15m": bars_15m,
        }

        signal = Signal(
            symbol=snapshot.symbol,
            exchange=snapshot.exchange,
            action=action,
            confidence=round(confidence, 4),
            entry_price=entry,
            stop_loss=round(sl, 2),
            take_profit=round(tp, 2),
            reasoning=self._build_reasoning(action, votes, confidence, regime),
            agent_id=self.agent_id,
            agent_name=self.name,
            metadata=metadata,
            timestamp=datetime.now(timezone.utc),
        )

        self.log.info(
            "intraday_signal_generated",
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

    def _build_dataframe(self, candles: list[dict]) -> pd.DataFrame | None:
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
        base = dict(self.config["weights"])
        mods = REGIME_WEIGHT_MODS.get(regime, {})
        for indicator, multiplier in mods.items():
            if indicator in base:
                base[indicator] = base[indicator] * multiplier
        if regime == "volatile":
            base = {k: v * 0.5 for k, v in base.items()}
        return base

    def _build_reasoning(
        self, action: str, votes: dict[str, int], confidence: float, regime: str
    ) -> str:
        bullish = [k for k, v in votes.items() if v > 0]
        bearish = [k for k, v in votes.items() if v < 0]
        parts = [f"Intraday {action} (conf: {confidence:.0%}, regime: {regime}, 5m+15m)."]
        if bullish:
            parts.append(f"Bullish: {', '.join(bullish)}.")
        if bearish:
            parts.append(f"Bearish: {', '.join(bearish)}.")
        return " ".join(parts)
