"""Tests for the SentimentAgent.

The agent is keyword-only by design (`_analyze_with_keywords` — "fast, free, zero
API cost"); the Gemini path was removed, so there is no `settings`/`genai` to patch
and no `use_llm` config key.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.sentiment import BEARISH_KEYWORDS, BULLISH_KEYWORDS, SentimentAgent
from app.agents.signals import MarketSnapshot
from app.data.feeds.news import NewsItem


def _make_snapshot(symbol="RELIANCE", ltp=2500.0, prev_close=2490.0):
    return MarketSnapshot(
        symbol=symbol,
        exchange="NSE",
        ltp=ltp,
        open=2480.0,
        high=2520.0,
        low=2460.0,
        close=prev_close,
        volume=5000000,
        candles_1d=[],
        timestamp=datetime.now(timezone.utc),
    )


def _make_news_items(titles: list[str], symbol="RELIANCE") -> list[NewsItem]:
    return [
        NewsItem(
            title=title,
            source="moneycontrol",
            url="https://example.com",
            published=datetime.now(timezone.utc),
            symbols=[symbol],
        )
        for title in titles
    ]


@pytest.fixture
def agent():
    # min_headlines is lowered from the default of 4 so the fixtures below stay small.
    return SentimentAgent(
        agent_id="test-sentiment-001",
        name="test-sentiment",
        config={"min_headlines": 2},
    )


# ── Keyword-based analysis ──


def test_keyword_bullish(agent):
    """Bullish keywords should produce bullish sentiment."""
    news = _make_news_items(
        [
            "RELIANCE shares surge 5% on strong quarterly profit",
            "RELIANCE rallies as profit beats estimates",
            "RELIANCE hits record high on growth outlook",
        ]
    )
    result = agent._analyze_with_keywords(news)
    assert result["sentiment"] == "BULLISH"
    assert result["action"] == "BUY"
    assert result["confidence"] > 0.4


def test_keyword_bearish(agent):
    """Bearish keywords should produce bearish sentiment."""
    news = _make_news_items(
        [
            "RELIANCE shares drop 4% on weak earnings",
            "RELIANCE slumps amid debt concerns and risk warning",
            "RELIANCE falls to yearly bottom, bearish outlook",
        ]
    )
    result = agent._analyze_with_keywords(news)
    assert result["sentiment"] == "BEARISH"
    assert result["action"] == "SELL"
    assert result["confidence"] > 0.4


def test_keyword_mixed_is_neutral(agent):
    """Balanced bullish/bearish keywords land in the neutral band."""
    news = _make_news_items(
        [
            "RELIANCE profit rises but debt risk grows",
            "RELIANCE gains on strong sales, weak margins warning",
        ]
    )
    result = agent._analyze_with_keywords(news)
    assert result["sentiment"] == "NEUTRAL"
    assert result["action"] == "HOLD"


def test_keyword_no_sentiment_words(agent):
    """Headlines with zero sentiment keywords return the neutral floor."""
    news = _make_news_items(
        [
            "RELIANCE annual general meeting date confirmed",
            "RELIANCE names new board member",
        ]
    )
    result = agent._analyze_with_keywords(news)
    assert result["sentiment"] == "NEUTRAL"
    assert result["action"] == "HOLD"
    assert result["confidence"] == 0.3
    assert result["risk_factors"] == ["low_signal_quality"]


def test_keyword_confidence_capped(agent):
    """Keyword-only confidence is capped at 0.70 however lopsided the input."""
    news = _make_news_items(
        ["RELIANCE surge rally jump gain rise profit growth strong bullish record"] * 5
    )
    result = agent._analyze_with_keywords(news)
    assert result["confidence"] <= 0.7


def test_keyword_lists_are_disjoint():
    """A keyword in both lists would score both directions for one headline."""
    assert not set(BULLISH_KEYWORDS) & set(BEARISH_KEYWORDS)


# ── Full analyze() ──


async def test_analyze_insufficient_news(agent):
    """Returns None when fewer than min_headlines are found."""
    snapshot = _make_snapshot()
    with patch.object(agent.scraper, "fetch_headlines", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = []
        signal = await agent.analyze(snapshot)
    assert signal is None


async def _analyze_with_news(agent, snapshot, news):
    with patch.object(agent.scraper, "fetch_headlines", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = news
        with patch.object(agent.scraper, "find_symbol_mentions") as mock_mentions:
            mock_mentions.return_value = {snapshot.symbol: news}
            return await agent.analyze(snapshot)


async def test_analyze_bullish_signal(agent):
    """Generates a BUY signal on bullish news."""
    snapshot = _make_snapshot()
    news = _make_news_items(
        [
            "RELIANCE shares surge on strong quarterly profit growth",
            "RELIANCE rallies 5% as profit beats estimates, bullish outlook",
            "RELIANCE hits record high on surge in revenue",
        ]
    )
    signal = await _analyze_with_news(agent, snapshot, news)

    assert signal is not None
    assert signal.action == "BUY"
    assert signal.symbol == "RELIANCE"
    assert signal.stop_loss < signal.entry_price
    assert signal.take_profit > signal.entry_price
    assert signal.metadata["analysis_method"] == "keywords"
    assert signal.metadata["sentiment"] == "BULLISH"
    assert signal.metadata["headlines_count"] == 3


async def test_analyze_bearish_signal(agent):
    """Generates a SELL signal on bearish news."""
    snapshot = _make_snapshot()
    news = _make_news_items(
        [
            "RELIANCE shares crash on unexpected loss and debt warning",
            "RELIANCE plunges 7% on weak earnings, bearish outlook",
            "RELIANCE falls to yearly low amid negative sentiment",
        ]
    )
    signal = await _analyze_with_news(agent, snapshot, news)

    assert signal is not None
    assert signal.action == "SELL"
    assert signal.stop_loss > signal.entry_price
    assert signal.take_profit < signal.entry_price
    assert signal.metadata["sentiment"] == "BEARISH"


async def test_analyze_hold_returns_none(agent):
    """Returns None when sentiment is neutral, since HOLD is not tradeable."""
    snapshot = _make_snapshot()
    news = _make_news_items(
        [
            "RELIANCE annual general meeting date confirmed",
            "RELIANCE names new board member",
            "RELIANCE to release results tomorrow",
        ]
    )
    signal = await _analyze_with_news(agent, snapshot, news)
    assert signal is None


async def test_analyze_respects_min_confidence(agent):
    """A signal below min_confidence is suppressed even when directional."""
    snapshot = _make_snapshot()
    agent.min_confidence = 0.99
    news = _make_news_items(
        [
            "RELIANCE shares surge on strong quarterly profit growth",
            "RELIANCE rallies 5% as profit beats estimates, bullish outlook",
        ]
    )
    signal = await _analyze_with_news(agent, snapshot, news)
    assert signal is None


# ── Config schema ──


def test_config_schema(agent):
    schema = agent.get_config_schema()
    assert schema["type"] == "object"
    for key in ("min_headlines", "min_confidence", "sl_pct", "tp_ratio"):
        assert key in schema["properties"], key


def test_default_min_confidence_is_reachable():
    """min_confidence must sit under the 0.70 keyword cap or the agent is dead code."""
    assert SentimentAgent.DEFAULT_CONFIG["min_confidence"] < 0.7
