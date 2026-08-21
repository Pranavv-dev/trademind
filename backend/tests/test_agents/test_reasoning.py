"""Tests for the ReasoningAgent (Gemini validation)."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.reasoning import ReasoningAgent, SYSTEM_PROMPT
from app.agents.signals import MarketSnapshot, Signal


@pytest.fixture
def agent():
    with patch("app.agents.reasoning.settings") as mock_settings, \
         patch("app.agents.reasoning.genai") as mock_genai:
        mock_settings.gemini_api_key = "test-key"
        mock_settings.gemini_model = "gemini-2.0-flash"
        mock_model_instance = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model_instance
        agent = ReasoningAgent(
            agent_id="test-reasoning-001",
            name="test-reasoning",
            config={"model": "gemini-2.0-flash", "temperature": 0.3},
        )
        agent.model = mock_model_instance
        return agent


@pytest.fixture
def sample_signal():
    return Signal(
        agent_id="test-tech-001",
        agent_name="test-technical",
        symbol="RELIANCE",
        exchange="NSE",
        action="BUY",
        confidence=0.75,
        entry_price=2500.0,
        stop_loss=2425.0,
        take_profit=2625.0,
        reasoning="RSI oversold, MACD bullish crossover",
        metadata={
            "rsi": 32.5,
            "macd": 5.2,
            "macd_signal": 3.1,
            "bb_upper": 2600.0,
            "bb_lower": 2400.0,
            "atr": 45.0,
        },
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_snapshot():
    return MarketSnapshot(
        symbol="RELIANCE",
        exchange="NSE",
        ltp=2500.0,
        open=2480.0,
        high=2520.0,
        low=2460.0,
        close=2490.0,
        volume=5000000,
        candles_1d=[
            {"open": 2450, "high": 2500, "low": 2440, "close": 2490, "volume": 4000000},
            {"open": 2490, "high": 2510, "low": 2470, "close": 2480, "volume": 3500000},
            {"open": 2480, "high": 2520, "low": 2460, "close": 2500, "volume": 5000000},
        ],
        timestamp=datetime.now(timezone.utc),
    )


def test_analyze_returns_none(agent):
    """ReasoningAgent.analyze() should always return None."""
    import asyncio

    snapshot = MarketSnapshot(
        symbol="TCS", exchange="NSE", ltp=3500, open=3480,
        high=3520, low=3470, close=3490, volume=1000000,
        candles_1d=[], timestamp=datetime.now(timezone.utc),
    )
    result = asyncio.get_event_loop().run_until_complete(agent.analyze(snapshot))
    assert result is None


def test_build_prompt(agent, sample_signal, sample_snapshot):
    """Prompt should contain signal details, indicators, and candle data."""
    prompt = agent._build_prompt(sample_signal, sample_snapshot, "No open positions.")
    assert "RELIANCE" in prompt
    assert "BUY" in prompt
    assert "2,500.00" in prompt
    assert "rsi: 32.5" in prompt
    assert "macd: 5.2" in prompt
    assert "No open positions." in prompt
    # Should include candle data
    assert "O:" in prompt
    assert "H:" in prompt


def test_build_prompt_no_indicators(agent, sample_snapshot):
    """Prompt handles missing indicator metadata gracefully."""
    signal = Signal(
        agent_id="test-001", agent_name="test", symbol="TCS", exchange="NSE",
        action="SELL", confidence=0.6, entry_price=3500, stop_loss=3570,
        take_profit=3360, reasoning="test", metadata={},
        timestamp=datetime.now(timezone.utc),
    )
    prompt = agent._build_prompt(signal, sample_snapshot, "No positions.")
    assert "No indicator data" in prompt


def test_build_prompt_no_candles(agent, sample_signal):
    """Prompt handles missing candle data gracefully."""
    snapshot = MarketSnapshot(
        symbol="RELIANCE", exchange="NSE", ltp=2500, open=2480,
        high=2520, low=2460, close=2490, volume=5000000,
        candles_1d=[], timestamp=datetime.now(timezone.utc),
    )
    prompt = agent._build_prompt(sample_signal, snapshot, "No positions.")
    assert "No recent data" in prompt


@pytest.mark.asyncio
async def test_validate_signal_agree(agent, sample_signal, sample_snapshot):
    """Test successful validation where Gemini agrees."""
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "validation": "AGREE",
        "adjusted_confidence": 0.8,
        "reasoning": "Signal looks solid. RSI confirms oversold.",
        "risk_flags": [],
        "suggested_stop_loss": None,
        "suggested_take_profit": None,
    })
    mock_response.usage_metadata.prompt_token_count = 500
    mock_response.usage_metadata.candidates_token_count = 100

    agent.model.generate_content_async = AsyncMock(return_value=mock_response)

    result = await agent.validate_signal(sample_signal, sample_snapshot)

    assert result["validation"] == "AGREE"
    assert result["adjusted_confidence"] == 0.8
    assert result["risk_flags"] == []
    agent.model.generate_content_async.assert_called_once()


@pytest.mark.asyncio
async def test_validate_signal_disagree(agent, sample_signal, sample_snapshot):
    """Test validation where Gemini disagrees."""
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "validation": "DISAGREE",
        "adjusted_confidence": 0.2,
        "reasoning": "Market is in a strong downtrend, avoid longs.",
        "risk_flags": ["downtrend", "low_volume"],
        "suggested_stop_loss": None,
        "suggested_take_profit": None,
    })
    mock_response.usage_metadata.prompt_token_count = 500
    mock_response.usage_metadata.candidates_token_count = 120

    agent.model.generate_content_async = AsyncMock(return_value=mock_response)

    result = await agent.validate_signal(sample_signal, sample_snapshot)

    assert result["validation"] == "DISAGREE"
    assert result["adjusted_confidence"] == 0.2
    assert "downtrend" in result["risk_flags"]


@pytest.mark.asyncio
async def test_validate_signal_modify(agent, sample_signal, sample_snapshot):
    """Test validation where Gemini suggests modifications."""
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "validation": "MODIFY",
        "adjusted_confidence": 0.65,
        "reasoning": "Signal is valid but SL too tight given recent volatility.",
        "risk_flags": ["tight_stop_loss"],
        "suggested_stop_loss": 2400.0,
        "suggested_take_profit": 2650.0,
    })
    mock_response.usage_metadata.prompt_token_count = 500
    mock_response.usage_metadata.candidates_token_count = 110

    agent.model.generate_content_async = AsyncMock(return_value=mock_response)

    result = await agent.validate_signal(sample_signal, sample_snapshot)

    assert result["validation"] == "MODIFY"
    assert result["suggested_stop_loss"] == 2400.0
    assert result["suggested_take_profit"] == 2650.0


@pytest.mark.asyncio
async def test_validate_signal_json_error(agent, sample_signal, sample_snapshot):
    """Falls back gracefully when Gemini returns invalid JSON."""
    mock_response = MagicMock()
    mock_response.text = "This is not valid JSON {{"

    agent.model.generate_content_async = AsyncMock(return_value=mock_response)

    result = await agent.validate_signal(sample_signal, sample_snapshot)

    assert result["validation"] == "AGREE"
    # The fallback passes the signal through unchanged — no confidence haircut.
    assert result["adjusted_confidence"] == sample_signal.confidence
    assert "llm_unavailable" in result["risk_flags"]


@pytest.mark.asyncio
async def test_validate_signal_api_error(agent, sample_signal, sample_snapshot):
    """Falls back gracefully on API error."""
    agent.model.generate_content_async = AsyncMock(
        side_effect=Exception("server error")
    )

    result = await agent.validate_signal(sample_signal, sample_snapshot)

    assert result["validation"] == "AGREE"
    assert "llm_unavailable" in result["risk_flags"]


def test_fallback_response(agent, sample_signal):
    """Fallback passes the signal through with confidence untouched."""
    result = agent._fallback_response(sample_signal)
    assert result["validation"] == "AGREE"
    # Unchanged, not reduced: an unavailable LLM must not silently alter sizing.
    assert result["adjusted_confidence"] == sample_signal.confidence
    assert "llm_unavailable" in result["risk_flags"]
    assert result["suggested_stop_loss"] is None
    assert result["suggested_take_profit"] is None


def test_config_schema(agent):
    schema = agent.get_config_schema()
    assert schema["type"] == "object"
    assert "model" in schema["properties"]
    assert "temperature" in schema["properties"]
