"""Tests for order state machine transitions."""

from app.execution.order_manager import TRANSITIONS


def test_valid_transitions():
    assert "placed" in TRANSITIONS["pending"]
    assert "cancelled" in TRANSITIONS["pending"]
    assert "rejected" in TRANSITIONS["pending"]
    assert "filled" in TRANSITIONS["placed"]
    assert "cancelled" in TRANSITIONS["placed"]


def test_terminal_states():
    assert len(TRANSITIONS["filled"]) == 0
    assert len(TRANSITIONS["cancelled"]) == 0
    assert len(TRANSITIONS["rejected"]) == 0


def test_no_backward_transitions():
    assert "pending" not in TRANSITIONS["placed"]
    assert "pending" not in TRANSITIONS["filled"]
    assert "placed" not in TRANSITIONS["filled"]
