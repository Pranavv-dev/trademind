"""Tests for the backtest trade simulator."""

from datetime import date

import pytest

from app.backtest.simulator import TradeSimulator, SimPosition, SimTrade


@pytest.fixture
def sim():
    return TradeSimulator(initial_capital=100000, position_size_pct=10.0)


class TestSimulatorInit:
    def test_initial_state(self, sim):
        assert sim.cash == 100000
        assert sim.initial_capital == 100000
        assert sim.portfolio_value == 100000
        assert len(sim.positions) == 0
        assert len(sim.trades) == 0


class TestCalculateQuantity:
    def test_quantity_from_portfolio(self, sim):
        # 10% of 100000 = 10000, at price 500 = 20 shares
        qty = sim.calculate_quantity(500)
        assert qty == 20

    def test_quantity_zero_price(self, sim):
        assert sim.calculate_quantity(0) == 0

    def test_quantity_high_price(self, sim):
        # 10% of 100000 = 10000, at price 15000 = 0 shares
        qty = sim.calculate_quantity(15000)
        assert qty == 0


class TestProcessSignal:
    def test_buy_opens_position(self, sim):
        result = sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="BUY",
            price=2500, stop_loss=2400, take_profit=2700,
            current_date=date(2024, 1, 1),
        )
        assert result is None  # Opening doesn't return a trade
        assert "RELIANCE" in sim.positions
        assert sim.positions["RELIANCE"].side == "LONG"
        assert sim.positions["RELIANCE"].quantity == 4  # 10000/2500 = 4

    def test_sell_opens_short(self, sim):
        sim.process_signal(
            symbol="TCS", exchange="NSE", action="SELL",
            price=3500, stop_loss=3600, take_profit=3300,
            current_date=date(2024, 1, 1),
        )
        assert sim.positions["TCS"].side == "SHORT"

    def test_hold_does_nothing(self, sim):
        result = sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="HOLD",
            price=2500, stop_loss=2400, take_profit=2700,
            current_date=date(2024, 1, 1),
        )
        assert result is None
        assert len(sim.positions) == 0

    def test_opposite_signal_closes_position(self, sim):
        sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="BUY",
            price=2500, stop_loss=2400, take_profit=2700,
            current_date=date(2024, 1, 1),
        )
        trade = sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="SELL",
            price=2600, stop_loss=0, take_profit=0,
            current_date=date(2024, 1, 5),
        )
        assert trade is not None
        assert trade.pnl > 0
        assert trade.exit_reason == "signal"
        assert "RELIANCE" not in sim.positions

    def test_same_direction_ignored(self, sim):
        sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="BUY",
            price=2500, stop_loss=2400, take_profit=2700,
            current_date=date(2024, 1, 1),
        )
        result = sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="BUY",
            price=2550, stop_loss=2450, take_profit=2750,
            current_date=date(2024, 1, 2),
        )
        assert result is None  # Already in position

    def test_insufficient_cash(self, sim):
        # Spend most of the cash
        sim.cash = 100
        result = sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="BUY",
            price=2500, stop_loss=2400, take_profit=2700,
            current_date=date(2024, 1, 1),
        )
        assert result is None
        assert len(sim.positions) == 0

    def test_cash_deducted_on_buy(self, sim):
        initial_cash = sim.cash
        sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="BUY",
            price=2500, stop_loss=2400, take_profit=2700,
            current_date=date(2024, 1, 1),
        )
        assert sim.cash < initial_cash
        qty = sim.positions["RELIANCE"].quantity
        assert sim.cash == initial_cash - (qty * 2500)


class TestCheckExits:
    def test_stop_loss_long(self, sim):
        sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="BUY",
            price=2500, stop_loss=2400, take_profit=2700,
            current_date=date(2024, 1, 1),
        )
        trade = sim.check_exits("RELIANCE", high=2550, low=2380, current_date=date(2024, 1, 2))
        assert trade is not None
        assert trade.exit_reason == "stop_loss"
        assert trade.exit_price == 2400
        assert trade.pnl < 0

    def test_take_profit_long(self, sim):
        sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="BUY",
            price=2500, stop_loss=2400, take_profit=2700,
            current_date=date(2024, 1, 1),
        )
        trade = sim.check_exits("RELIANCE", high=2750, low=2480, current_date=date(2024, 1, 2))
        assert trade is not None
        assert trade.exit_reason == "take_profit"
        assert trade.exit_price == 2700
        assert trade.pnl > 0

    def test_stop_loss_checked_before_take_profit(self, sim):
        sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="BUY",
            price=2500, stop_loss=2400, take_profit=2700,
            current_date=date(2024, 1, 1),
        )
        # Both SL and TP hit in same bar — SL should win (conservative)
        trade = sim.check_exits("RELIANCE", high=2750, low=2350, current_date=date(2024, 1, 2))
        assert trade is not None
        assert trade.exit_reason == "stop_loss"

    def test_no_exit_within_range(self, sim):
        sim.process_signal(
            symbol="RELIANCE", exchange="NSE", action="BUY",
            price=2500, stop_loss=2400, take_profit=2700,
            current_date=date(2024, 1, 1),
        )
        trade = sim.check_exits("RELIANCE", high=2600, low=2450, current_date=date(2024, 1, 2))
        assert trade is None

    def test_stop_loss_short(self, sim):
        sim.process_signal(
            symbol="TCS", exchange="NSE", action="SELL",
            price=3500, stop_loss=3600, take_profit=3300,
            current_date=date(2024, 1, 1),
        )
        trade = sim.check_exits("TCS", high=3650, low=3480, current_date=date(2024, 1, 2))
        assert trade is not None
        assert trade.exit_reason == "stop_loss"
        assert trade.pnl < 0

    def test_no_position_returns_none(self, sim):
        result = sim.check_exits("UNKNOWN", high=100, low=50, current_date=date(2024, 1, 1))
        assert result is None


class TestCloseAllPositions:
    def test_close_all(self, sim):
        sim.process_signal("RELIANCE", "NSE", "BUY", 2500, 2400, 2700, date(2024, 1, 1))
        sim.process_signal("TCS", "NSE", "BUY", 3500, 3400, 3700, date(2024, 1, 1))

        closed = sim.close_all_positions(
            {"RELIANCE": 2600, "TCS": 3400},
            date(2024, 3, 1),
        )
        assert len(closed) == 2
        assert len(sim.positions) == 0
        assert all(t.exit_reason == "backtest_end" for t in closed)


class TestEquityCurve:
    def test_record_equity(self, sim):
        sim.process_signal("RELIANCE", "NSE", "BUY", 2500, 2400, 2700, date(2024, 1, 1))

        sim.record_equity(date(2024, 1, 1), {"RELIANCE": 2500})
        sim.record_equity(date(2024, 1, 2), {"RELIANCE": 2600})

        assert len(sim.equity_curve) == 2
        # Second day: cash + position_value at 2600
        assert sim.equity_curve[1]["value"] > sim.equity_curve[0]["value"]

    def test_equity_without_positions(self, sim):
        sim.record_equity(date(2024, 1, 1), {})
        assert sim.equity_curve[0]["value"] == 100000


class TestGetTradeDicts:
    def test_serialization(self, sim):
        sim.process_signal("RELIANCE", "NSE", "BUY", 2500, 2400, 2700, date(2024, 1, 1))
        sim.process_signal("RELIANCE", "NSE", "SELL", 2600, 0, 0, date(2024, 1, 5))

        dicts = sim.get_trade_dicts()
        assert len(dicts) == 1
        t = dicts[0]
        assert t["symbol"] == "RELIANCE"
        assert t["entry_price"] == 2500
        assert t["exit_price"] == 2600
        assert t["pnl"] > 0
        assert "entry_date" in t
        assert "exit_date" in t
        assert "exit_reason" in t
