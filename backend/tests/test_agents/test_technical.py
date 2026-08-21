"""Tests for the TechnicalAgent."""

import random
from datetime import datetime, timezone

import pytest

from app.agents.signals import MarketSnapshot
from app.agents.technical import TechnicalAgent


def _make_candles(n: int = 100, base_price: float = 1000.0) -> list[dict]:
    """Generate synthetic OHLCV candles for testing."""
    candles = []
    price = base_price
    for i in range(n):
        change = random.uniform(-20, 20)
        o = price
        c = price + change
        h = max(o, c) + random.uniform(0, 10)
        l = min(o, c) - random.uniform(0, 10)  # noqa: E741
        vol = random.randint(100000, 5000000)
        candles.append({
            "time": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": vol,
        })
        price = c
    return candles


def _make_snapshot(candles: list[dict], symbol: str = "RELIANCE") -> MarketSnapshot:
    last = candles[-1]
    return MarketSnapshot(
        symbol=symbol,
        exchange="NSE",
        ltp=last["close"],
        open=last["open"],
        high=last["high"],
        low=last["low"],
        close=candles[-2]["close"] if len(candles) > 1 else last["close"],
        volume=last["volume"],
        candles_1d=candles,
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def agent():
    return TechnicalAgent(
        agent_id="test-tech-001",
        name="test-technical",
        config={},
    )


@pytest.mark.asyncio
async def test_insufficient_data_returns_none(agent):
    """Agent should return None if fewer than 50 candles."""
    candles = _make_candles(10)
    snapshot = _make_snapshot(candles)
    signal = await agent.analyze(snapshot)
    assert signal is None


@pytest.mark.asyncio
async def test_analyze_returns_signal_or_none(agent):
    """Agent should return a Signal or None (never raise) with valid data."""
    random.seed(42)
    candles = _make_candles(200)
    snapshot = _make_snapshot(candles)
    signal = await agent.analyze(snapshot)
    # May or may not generate a signal depending on random data
    if signal is not None:
        assert signal.symbol == "RELIANCE"
        assert signal.exchange == "NSE"
        assert signal.action in ("BUY", "SELL")
        assert 0 <= signal.confidence <= 1
        assert signal.stop_loss > 0
        assert signal.take_profit > 0
        assert signal.agent_id == "test-tech-001"
        assert signal.reasoning
        assert "votes" in signal.metadata


@pytest.mark.asyncio
async def test_signal_has_correct_metadata(agent):
    """If a signal is generated, metadata should contain indicator values."""
    random.seed(123)
    candles = _make_candles(250)
    snapshot = _make_snapshot(candles)
    signal = await agent.analyze(snapshot)
    if signal is not None:
        meta = signal.metadata
        assert "rsi" in meta
        assert "macd" in meta
        assert "atr" in meta
        assert "votes" in meta
        assert "weighted_score" in meta


@pytest.mark.asyncio
async def test_config_schema(agent):
    schema = agent.get_config_schema()
    assert schema["type"] == "object"
    # TechnicalAgent is a confirmer now: it exposes vote-threshold and exit
    # parameters, not per-indicator periods like the old rsi_period.
    for key in ("signal_threshold", "atr_sl_multiplier", "tp_ratio", "long_only"):
        assert key in schema["properties"], key


@pytest.mark.asyncio
async def test_empty_candles_returns_none(agent):
    snapshot = MarketSnapshot(
        symbol="TCS",
        exchange="NSE",
        ltp=3500,
        open=3480,
        high=3520,
        low=3470,
        close=3490,
        volume=1000000,
        candles_1d=[],
        timestamp=datetime.now(timezone.utc),
    )
    signal = await agent.analyze(snapshot)
    assert signal is None
