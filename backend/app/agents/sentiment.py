"""Sentiment analysis agent — generates signals from news headlines using keyword analysis."""

from datetime import datetime, timezone

import structlog

from app.agents.base import BaseAgent
from app.agents.signals import MarketSnapshot, Signal
from app.data.feeds.news import NewsItem, NewsScraper

log = structlog.get_logger()

# Keyword-based sentiment scoring
BULLISH_KEYWORDS = [
    "surge",
    "rally",
    "jump",
    "gain",
    "up",
    "rise",
    "high",
    "profit",
    "growth",
    "beat",
    "outperform",
    "upgrade",
    "buy",
    "strong",
    "bullish",
    "record",
    "breakout",
    "positive",
    "boom",
    "recover",
    "soar",
    "dividend",
]
BEARISH_KEYWORDS = [
    "fall",
    "drop",
    "crash",
    "decline",
    "loss",
    "down",
    "low",
    "miss",
    "underperform",
    "downgrade",
    "sell",
    "weak",
    "bearish",
    "slump",
    "negative",
    "plunge",
    "cut",
    "warning",
    "risk",
    "debt",
    "default",
]


class SentimentAgent(BaseAgent):
    """Analyzes news sentiment to generate trading signals via keyword scoring.

    Fast, free, zero API cost. Suitable as one input to the ensemble layer
    where it's weighted below the technical agent.
    """

    DEFAULT_CONFIG = {
        "min_headlines": 4,
        # Keyword-only confidence is capped at 0.70 inside _analyze_with_keywords
        # (min(0.4 + bull_ratio*0.3, 0.70)). A min_confidence of 0.70 required
        # bull_ratio == 1.0 exactly — mathematically near-impossible — making the
        # agent functionally dead. 0.55 corresponds to bull_ratio ≥ 0.5, which is
        # a sensible "majority bullish" threshold and matches the schema default.
        "min_confidence": 0.55,
        # ATR-based SL; falls back to 5% fixed (widened from 3% — NIFTY large-cap
        # ATR is 2.5-3.5%, a 3% SL is below noise and gets whipsawed).
        "sl_pct": 0.05,
        "tp_ratio": 2.0,
        "sources": None,  # None = all sources
    }

    def __init__(self, agent_id: str, name: str, config: dict):
        super().__init__(agent_id, name, config)
        merged = {**self.DEFAULT_CONFIG, **config}
        self.min_headlines = merged["min_headlines"]
        self.min_confidence = merged["min_confidence"]
        self.sl_pct = merged["sl_pct"]
        self.tp_ratio = merged["tp_ratio"]
        self.sources = merged["sources"]
        self.scraper = NewsScraper()

    async def analyze(self, snapshot: MarketSnapshot) -> Signal | None:
        """Fetch news for the symbol and analyze sentiment."""
        symbol = snapshot.symbol

        # Fetch recent headlines
        headlines = await self.scraper.fetch_headlines(self.sources)
        mentions = self.scraper.find_symbol_mentions(headlines, [symbol])
        symbol_news = mentions.get(symbol, [])

        if len(symbol_news) < self.min_headlines:
            self.log.debug(
                "insufficient_news",
                symbol=symbol,
                headlines=len(symbol_news),
                min_required=self.min_headlines,
            )
            return None

        # Analyze sentiment with keywords (no Gemini — zero cost)
        result = self._analyze_with_keywords(symbol_news)

        if result is None:
            return None

        action = result["action"]
        confidence = result["confidence"]

        if action == "HOLD" or confidence < self.min_confidence:
            self.log.info(
                "sentiment_no_signal",
                symbol=symbol,
                action=action,
                confidence=confidence,
            )
            return None

        # Calculate SL/TP
        ltp = snapshot.ltp
        if action == "BUY":
            stop_loss = ltp * (1 - self.sl_pct)
            take_profit = ltp * (1 + self.sl_pct * self.tp_ratio)
        else:  # SELL
            stop_loss = ltp * (1 + self.sl_pct)
            take_profit = ltp * (1 - self.sl_pct * self.tp_ratio)

        signal = Signal(
            agent_id=self.agent_id,
            agent_name=self.name,
            symbol=symbol,
            exchange=snapshot.exchange,
            action=action,
            confidence=confidence,
            entry_price=ltp,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            reasoning=result["reasoning"],
            metadata={
                "sentiment": result["sentiment"],
                "headlines_count": len(symbol_news),
                "key_headlines": result.get("key_headlines", []),
                "risk_factors": result.get("risk_factors", []),
                "analysis_method": "keywords",
            },
            timestamp=datetime.now(timezone.utc),
        )

        self.log.info(
            "sentiment_signal",
            symbol=symbol,
            action=action,
            confidence=confidence,
            sentiment=result["sentiment"],
        )
        return signal

    def get_config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "min_headlines": {
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "description": "Minimum headlines required to generate a signal",
                },
                "min_confidence": {
                    "type": "number",
                    "default": 0.55,
                    "minimum": 0,
                    "maximum": 1,
                },
                "sl_pct": {
                    "type": "number",
                    "default": 0.03,
                    "description": "Stop-loss as percentage of entry price",
                },
                "tp_ratio": {
                    "type": "number",
                    "default": 2.0,
                    "description": "Take-profit as multiple of stop-loss distance",
                },
            },
        }

    # ── Private helpers ──

    def _analyze_with_keywords(self, news_items: list[NewsItem]) -> dict:
        """Keyword-based sentiment analysis — fast, free, zero API cost."""
        bullish_score = 0
        bearish_score = 0
        key_headlines = []

        for item in news_items:
            title_lower = item.title.lower()
            item_bull = sum(1 for kw in BULLISH_KEYWORDS if kw in title_lower)
            item_bear = sum(1 for kw in BEARISH_KEYWORDS if kw in title_lower)

            bullish_score += item_bull
            bearish_score += item_bear

            if item_bull > 0 or item_bear > 0:
                key_headlines.append(item.title)

        total = bullish_score + bearish_score
        if total == 0:
            return {
                "sentiment": "NEUTRAL",
                "action": "HOLD",
                "confidence": 0.3,
                "reasoning": "No strong sentiment keywords found in headlines.",
                "key_headlines": [],
                "risk_factors": ["low_signal_quality"],
            }

        bull_ratio = bullish_score / total
        bear_ratio = bearish_score / total

        if bull_ratio > 0.65:
            sentiment = "BULLISH"
            action = "BUY"
            confidence = min(0.4 + bull_ratio * 0.3, 0.7)  # Cap at 0.7 for keyword-only
        elif bear_ratio > 0.65:
            sentiment = "BEARISH"
            action = "SELL"
            confidence = min(0.4 + bear_ratio * 0.3, 0.7)
        else:
            sentiment = "NEUTRAL"
            action = "HOLD"
            confidence = 0.3

        return {
            "sentiment": sentiment,
            "action": action,
            "confidence": round(confidence, 2),
            "reasoning": (
                f"Keyword analysis: {bullish_score} bullish vs {bearish_score} bearish "
                f"signals across {len(news_items)} headlines."
            ),
            "key_headlines": key_headlines[:3],
            "risk_factors": ["keyword_only_analysis"],
        }
