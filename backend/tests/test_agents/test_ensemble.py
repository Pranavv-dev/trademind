"""Tests for the EnsembleAgent."""

from datetime import datetime, timezone

import pytest

from app.agents.ensemble import (
    DEFAULT_MIN_ENSEMBLE_CONFIDENCE,
    DEFAULT_WEIGHTS,
    EnsembleAgent,
)
from app.agents.signals import Signal


def _sig(
    symbol="RELIANCE",
    action="BUY",
    confidence=0.7,
    agent_name="test-technical",
    agent_id="t1",
    entry_price=2500.0,
    stop_loss=2425.0,
    take_profit=2625.0,
    metadata=None,
) -> Signal:
    return Signal(
        symbol=symbol,
        exchange="NSE",
        action=action,
        confidence=confidence,
        agent_name=agent_name,
        agent_id=agent_id,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reasoning=f"{agent_name} {action} signal",
        metadata=metadata or {},
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def ensemble():
    return EnsembleAgent()


# ── Basic combine tests ──


def test_empty_signals(ensemble):
    assert ensemble.combine([]) == []


def test_single_buy_signal(ensemble):
    """A single BUY signal should pass through as an ensemble signal."""
    signals = [_sig(action="BUY", confidence=0.7)]
    result = ensemble.combine(signals)
    assert len(result) == 1
    assert result[0].action == "BUY"
    assert result[0].metadata["ensemble"] is True


def test_single_sell_signal(ensemble):
    signals = [_sig(action="SELL", confidence=0.65)]
    result = ensemble.combine(signals)
    assert len(result) == 1
    assert result[0].action == "SELL"


def test_hold_signal_ignored(ensemble):
    """HOLD signals should not produce an ensemble signal."""
    signals = [_sig(action="HOLD", confidence=0.5)]
    result = ensemble.combine(signals)
    assert len(result) == 0


# ── Multi-agent agreement tests ──


def test_two_agents_agree_buy(ensemble):
    """Two agents agreeing on BUY should produce a consensus signal."""
    signals = [
        _sig(action="BUY", confidence=0.7, agent_name="test-technical", agent_id="t1"),
        _sig(action="BUY", confidence=0.6, agent_name="test-sentiment", agent_id="s1"),
    ]
    result = ensemble.combine(signals)
    assert len(result) == 1
    assert result[0].action == "BUY"
    assert len(result[0].metadata["contributing_agents"]) == 2
    assert len(result[0].metadata["opposing_agents"]) == 0


def test_two_agents_agree_sell(ensemble):
    signals = [
        _sig(action="SELL", confidence=0.7, agent_name="test-technical", agent_id="t1"),
        _sig(action="SELL", confidence=0.65, agent_name="test-sentiment", agent_id="s1"),
    ]
    result = ensemble.combine(signals)
    assert len(result) == 1
    assert result[0].action == "SELL"


def test_agents_disagree(ensemble):
    """Conflicting signals should resolve to the higher-weighted direction."""
    # Technical (weight=2.0) says BUY at 0.7, Sentiment (weight=1.0) says SELL at 0.6
    # BUY weighted: 0.7 * 2.0 = 1.4
    # SELL weighted: 0.6 * 1.0 = 0.6
    # BUY wins
    signals = [
        _sig(action="BUY", confidence=0.7, agent_name="test-technical", agent_id="t1"),
        _sig(action="SELL", confidence=0.6, agent_name="test-sentiment", agent_id="s1"),
    ]
    result = ensemble.combine(signals)
    assert len(result) == 1
    assert result[0].action == "BUY"
    assert len(result[0].metadata["opposing_agents"]) == 1


def test_technical_outweighs_more_confident_sentiment(ensemble):
    """Direction is decided by weighted score, not raw confidence.

    Technical carries DEFAULT_WEIGHTS["technical"] and sentiment only
    DEFAULT_WEIGHTS["sentiment"]; with today's 6:1 ratio a maximally confident
    sentiment signal still cannot outvote a middling technical one.
    """
    w_tech = DEFAULT_WEIGHTS["technical"]
    w_sent = DEFAULT_WEIGHTS["sentiment"]
    tech_conf, sent_conf = 0.6, 0.9
    assert tech_conf * w_tech > sent_conf * w_sent, "premise: technical outweighs sentiment"

    signals = [
        _sig(action="SELL", confidence=tech_conf, agent_name="test-technical", agent_id="t1"),
        _sig(action="BUY", confidence=sent_conf, agent_name="test-sentiment", agent_id="s1"),
    ]
    result = ensemble.combine(signals)
    assert len(result) == 1
    assert result[0].action == "SELL"


def test_losing_direction_below_threshold_is_dropped(ensemble):
    """Winning the vote is not enough — the winner's own confidence must clear the bar."""
    # Technical SELL at 0.3 outweighs sentiment BUY at 0.9, but 0.3 is below
    # DEFAULT_MIN_ENSEMBLE_CONFIDENCE, so nothing is emitted.
    signals = [
        _sig(action="SELL", confidence=0.3, agent_name="test-technical", agent_id="t1"),
        _sig(action="BUY", confidence=0.9, agent_name="test-sentiment", agent_id="s1"),
    ]
    assert 0.3 < DEFAULT_MIN_ENSEMBLE_CONFIDENCE
    assert ensemble.combine(signals) == []


# ── Weighted confidence calculation ──


def test_weighted_average_confidence(ensemble):
    """Ensemble confidence is the weight-weighted mean of the winning signals.

    Expected value is derived from DEFAULT_WEIGHTS rather than hardcoded, so
    retuning the weights doesn't silently invalidate this test.
    """
    tech_conf, sent_conf = 0.8, 0.6
    w_tech = DEFAULT_WEIGHTS["technical"]
    w_sent = DEFAULT_WEIGHTS["sentiment"]
    expected = (tech_conf * w_tech + sent_conf * w_sent) / (w_tech + w_sent)

    signals = [
        _sig(action="BUY", confidence=tech_conf, agent_name="test-technical", agent_id="t1"),
        _sig(action="BUY", confidence=sent_conf, agent_name="test-sentiment", agent_id="s1"),
    ]
    result = ensemble.combine(signals)
    assert len(result) == 1
    assert result[0].confidence == pytest.approx(expected, abs=0.01)
    # A weighted mean must sit between the two inputs.
    assert sent_conf < result[0].confidence < tech_conf


# ── Low confidence filtering ──


def test_low_confidence_filtered(ensemble):
    """Signals below min_ensemble_confidence are dropped."""
    signals = [
        _sig(action="BUY", confidence=0.3, agent_name="test-technical", agent_id="t1"),
    ]
    result = ensemble.combine(signals)
    # Weighted avg = 0.3, below default threshold of 0.5
    assert len(result) == 0


def test_just_above_threshold():
    ensemble = EnsembleAgent({"min_ensemble_confidence": 0.5})
    signals = [
        _sig(action="BUY", confidence=0.51, agent_name="test-technical"),
    ]
    result = ensemble.combine(signals)
    assert len(result) == 1


# ── Min agreement tests ──


def test_min_agreement_requirement():
    ensemble = EnsembleAgent({"min_agreement": 2})
    # Only 1 agent — below min_agreement of 2
    signals = [
        _sig(action="BUY", confidence=0.8, agent_name="test-technical"),
    ]
    result = ensemble.combine(signals)
    assert len(result) == 0


def test_min_agreement_met():
    ensemble = EnsembleAgent({"min_agreement": 2})
    signals = [
        _sig(action="BUY", confidence=0.8, agent_name="test-technical", agent_id="t1"),
        _sig(action="BUY", confidence=0.7, agent_name="test-sentiment", agent_id="s1"),
    ]
    result = ensemble.combine(signals)
    assert len(result) == 1


# ── Multiple symbols ──


def test_multiple_symbols(ensemble):
    """Produces one signal per symbol."""
    signals = [
        _sig(symbol="RELIANCE", action="BUY", confidence=0.7, agent_name="test-technical"),
        _sig(symbol="TCS", action="SELL", confidence=0.65, agent_name="test-technical"),
        _sig(symbol="INFY", action="BUY", confidence=0.3, agent_name="test-technical"),  # too low
    ]
    result = ensemble.combine(signals)
    symbols = {s.symbol for s in result}
    assert "RELIANCE" in symbols
    assert "TCS" in symbols
    assert "INFY" not in symbols  # below threshold


# ── Lead agent selection ──


def test_lead_agent_is_highest_confidence(ensemble):
    """SL/TP should come from the highest-confidence contributing agent."""
    signals = [
        _sig(
            action="BUY", confidence=0.6, agent_name="test-sentiment",
            agent_id="s1", stop_loss=2400, take_profit=2700,
        ),
        _sig(
            action="BUY", confidence=0.8, agent_name="test-technical",
            agent_id="t1", stop_loss=2425, take_profit=2625,
        ),
    ]
    result = ensemble.combine(signals)
    assert len(result) == 1
    # Lead agent should be technical (higher confidence)
    assert result[0].stop_loss == 2425
    assert result[0].take_profit == 2625
    assert result[0].metadata["lead_agent"] == "test-technical"


# ── Reasoning merging ──


def test_reasoning_merged(ensemble):
    signals = [
        _sig(action="BUY", confidence=0.7, agent_name="test-technical", agent_id="t1"),
        _sig(action="BUY", confidence=0.6, agent_name="test-sentiment", agent_id="s1"),
    ]
    result = ensemble.combine(signals)
    assert "Ensemble BUY" in result[0].reasoning
    assert "2 agents agree" in result[0].reasoning
    assert "technical" in result[0].reasoning
    assert "sentiment" in result[0].reasoning


# ── Custom weights ──


def test_custom_weights():
    ensemble = EnsembleAgent({
        "weights": {"technical": 1.0, "sentiment": 3.0},
    })
    # Technical (1.0) SELL at 0.7 → 0.7
    # Sentiment (3.0) BUY at 0.6 → 1.8
    # BUY wins with sentiment weight override
    signals = [
        _sig(action="SELL", confidence=0.7, agent_name="test-technical"),
        _sig(action="BUY", confidence=0.6, agent_name="test-sentiment"),
    ]
    result = ensemble.combine(signals)
    assert len(result) == 1
    assert result[0].action == "BUY"


# ── Agent type inference ──


def test_infer_agent_type(ensemble):
    sig_tech = _sig(agent_name="my-technical-agent")
    sig_sent = _sig(agent_name="news-sentiment-v2")
    sig_unknown = _sig(agent_name="custom-agent", metadata={"agent_type": "momentum"})

    assert ensemble._infer_agent_type(sig_tech) == "technical"
    assert ensemble._infer_agent_type(sig_sent) == "sentiment"
    assert ensemble._infer_agent_type(sig_unknown) == "momentum"
