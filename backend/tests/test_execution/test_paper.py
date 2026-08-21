"""Tests for the paper trading broker.

PaperBroker fills at LTP ± slippage (square-root market-impact model, floored at
MIN_SLIPPAGE_BPS) so that paper P&L doesn't flatter itself against mid. The
position-accounting tests below therefore disable slippage — as
`PAPER_SLIPPAGE_DISABLED=1` does in production — so the arithmetic stays exact and
the assertions describe the accounting rather than the fill model. Slippage itself
is covered by its own tests at the bottom.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.execution.paper import PaperBroker
from app.execution.slippage import MIN_SLIPPAGE_BPS


@pytest.fixture
def broker():
    """Broker with slippage disabled — for exact position/P&L arithmetic."""
    with patch("app.execution.paper.SLIPPAGE_DISABLED", True):
        yield PaperBroker()


@pytest.fixture
def slipping_broker():
    """Broker with the slippage model active — the production default."""
    with patch("app.execution.paper.SLIPPAGE_DISABLED", False):
        yield PaperBroker()


# ── Order placement and position accounting (slippage off) ──


async def test_place_buy_order(broker):
    result = await broker.place_order(
        symbol="RELIANCE",
        exchange="NSE",
        side="BUY",
        quantity=10,
        price=Decimal("2500.00"),
        order_type="MARKET",
        product="CNC",
    )
    assert result["status"] == "filled"
    assert result["fill_price"] == 2500.0
    assert result["slippage_bps"] == 0.0
    assert result["order_id"].startswith("PAPER-")


async def test_position_tracking(broker):
    await broker.place_order("RELIANCE", "NSE", "BUY", 10, Decimal("2500"), "MARKET", "CNC")
    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "RELIANCE"
    assert positions[0]["quantity"] == 10
    assert positions[0]["avg_price"] == 2500.0


async def test_close_position_pnl(broker):
    await broker.place_order("TCS", "NSE", "BUY", 5, Decimal("3500"), "MARKET", "CNC")
    await broker.place_order("TCS", "NSE", "SELL", 5, Decimal("3600"), "MARKET", "CNC")
    positions = await broker.get_positions()
    # Position should be flat (quantity 0 filtered out)
    assert len(positions) == 0


async def test_partial_close(broker):
    await broker.place_order("INFY", "NSE", "BUY", 20, Decimal("1500"), "MARKET", "CNC")
    await broker.place_order("INFY", "NSE", "SELL", 10, Decimal("1550"), "MARKET", "CNC")
    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0]["quantity"] == 10
    # Realized PnL = (1550 - 1500) * 10 = 500
    assert positions[0]["realized_pnl"] == 500.0


async def test_add_to_position_averages(broker):
    await broker.place_order("SBIN", "NSE", "BUY", 10, Decimal("600"), "MARKET", "CNC")
    await broker.place_order("SBIN", "NSE", "BUY", 10, Decimal("620"), "MARKET", "CNC")
    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0]["quantity"] == 20
    # Avg = (600*10 + 620*10) / 20 = 610
    assert positions[0]["avg_price"] == 610.0


async def test_order_status(broker):
    result = await broker.place_order("ITC", "NSE", "BUY", 100, Decimal("450"), "MARKET", "CNC")
    status = await broker.get_order_status(result["order_id"])
    assert status["status"] == "filled"


async def test_unknown_order_status(broker):
    status = await broker.get_order_status("NONEXISTENT")
    assert status["status"] == "not_found"


# ── Slippage model (slippage on — the production default) ──


async def test_buy_fills_above_mid(slipping_broker):
    """A BUY pays up: fill must be worse than mid, never better."""
    result = await slipping_broker.place_order(
        "RELIANCE", "NSE", "BUY", 10, Decimal("2500.00"), "MARKET", "CNC"
    )
    assert result["fill_price"] > 2500.0
    assert result["slippage_bps"] >= MIN_SLIPPAGE_BPS


async def test_sell_fills_below_mid(slipping_broker):
    """A SELL gives up the spread: fill must be worse than mid, never better."""
    result = await slipping_broker.place_order(
        "RELIANCE", "NSE", "SELL", 10, Decimal("2500.00"), "MARKET", "CNC"
    )
    assert result["fill_price"] < 2500.0
    assert result["slippage_bps"] >= MIN_SLIPPAGE_BPS


async def test_slippage_floor_applies_to_tiny_orders(slipping_broker):
    """Even a 1-share order pays the half-spread floor."""
    result = await slipping_broker.place_order(
        "RELIANCE", "NSE", "BUY", 1, Decimal("2500.00"), "MARKET", "CNC"
    )
    assert result["slippage_bps"] == pytest.approx(MIN_SLIPPAGE_BPS)


async def test_larger_orders_slip_more(slipping_broker):
    """Square-root market impact: size costs more, monotonically."""
    small = await slipping_broker.place_order(
        "RELIANCE", "NSE", "BUY", 100, Decimal("2500.00"), "MARKET", "CNC"
    )
    large = await slipping_broker.place_order(
        "RELIANCE", "NSE", "BUY", 500_000, Decimal("2500.00"), "MARKET", "CNC"
    )
    assert large["slippage_bps"] > small["slippage_bps"]


async def test_round_trip_costs_money(slipping_broker):
    """Buy then sell at an unchanged price must realise a loss, not breakeven."""
    await slipping_broker.place_order("TCS", "NSE", "BUY", 10, Decimal("3500"), "MARKET", "CNC")
    await slipping_broker.place_order("TCS", "NSE", "SELL", 10, Decimal("3500"), "MARKET", "CNC")
    # Position is flat, so read the realised P&L off the closed book.
    orders = [await slipping_broker.get_order_status(o) for o in slipping_broker._orders]
    buy = next(o for o in orders if o["side"] == "BUY")
    sell = next(o for o in orders if o["side"] == "SELL")
    assert buy["fill_price"] > sell["fill_price"]
