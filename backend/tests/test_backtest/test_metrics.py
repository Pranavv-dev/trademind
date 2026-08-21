"""Tests for backtest performance metrics."""

import pytest

from app.backtest.metrics import (
    BacktestMetrics,
    calculate_metrics,
    _calculate_max_drawdown,
    _sharpe_ratio,
    _sortino_ratio,
    _cagr,
    _calculate_daily_returns,
)


class TestCalculateMetrics:
    def test_empty_trades(self):
        m = calculate_metrics([], [], 100000, 0)
        assert m.total_trades == 0
        assert m.total_pnl == 0.0
        assert m.win_rate == 0.0
        assert m.sharpe_ratio is None

    def test_all_winning_trades(self):
        trades = [
            {"pnl": 1000},
            {"pnl": 2000},
            {"pnl": 500},
        ]
        equity = [
            {"date": "2024-01-01", "value": 100000},
            {"date": "2024-01-02", "value": 101000},
            {"date": "2024-01-03", "value": 103000},
            {"date": "2024-01-04", "value": 103500},
        ]
        m = calculate_metrics(trades, equity, 100000, 4)
        assert m.total_trades == 3
        assert m.winning_trades == 3
        assert m.losing_trades == 0
        assert m.win_rate == 1.0
        assert m.total_pnl == 3500.0
        assert m.avg_win > 0
        assert m.avg_loss == 0.0

    def test_mixed_trades(self):
        trades = [
            {"pnl": 2000},
            {"pnl": -500},
            {"pnl": 1000},
            {"pnl": -200},
        ]
        equity = [
            {"date": "2024-01-01", "value": 100000},
            {"date": "2024-01-02", "value": 102000},
            {"date": "2024-01-03", "value": 101500},
            {"date": "2024-01-04", "value": 102500},
            {"date": "2024-01-05", "value": 102300},
        ]
        m = calculate_metrics(trades, equity, 100000, 5)
        assert m.total_trades == 4
        assert m.winning_trades == 2
        assert m.losing_trades == 2
        assert m.win_rate == 0.5
        assert m.total_pnl == 2300.0
        assert m.profit_factor > 0

    def test_all_losing_trades(self):
        trades = [
            {"pnl": -1000},
            {"pnl": -2000},
        ]
        equity = [
            {"date": "2024-01-01", "value": 100000},
            {"date": "2024-01-02", "value": 99000},
            {"date": "2024-01-03", "value": 97000},
        ]
        m = calculate_metrics(trades, equity, 100000, 3)
        assert m.winning_trades == 0
        assert m.losing_trades == 2
        assert m.win_rate == 0.0
        assert m.profit_factor == 0.0
        assert m.total_pnl == -3000.0

    def test_pnl_percentage(self):
        trades = [{"pnl": 5000}]
        equity = [
            {"date": "2024-01-01", "value": 100000},
            {"date": "2024-01-02", "value": 105000},
        ]
        m = calculate_metrics(trades, equity, 100000, 2)
        assert m.total_pnl_pct == 5.0

    def test_trades_stored_on_metrics(self):
        trades = [{"pnl": 100}, {"pnl": -50}]
        m = calculate_metrics(trades, [], 100000, 1)
        assert m.trades == trades


class TestMaxDrawdown:
    def test_no_drawdown(self):
        equity = [
            {"value": 100},
            {"value": 110},
            {"value": 120},
        ]
        dd_pct, dd_amt = _calculate_max_drawdown(equity)
        assert dd_pct == 0.0
        assert dd_amt == 0.0

    def test_simple_drawdown(self):
        equity = [
            {"value": 100},
            {"value": 120},
            {"value": 100},  # 16.67% drawdown from peak of 120
            {"value": 130},
        ]
        dd_pct, dd_amt = _calculate_max_drawdown(equity)
        assert abs(dd_pct - 0.1667) < 0.001
        assert dd_amt == 20.0

    def test_empty_curve(self):
        dd_pct, dd_amt = _calculate_max_drawdown([])
        assert dd_pct == 0.0

    def test_deep_drawdown(self):
        equity = [
            {"value": 200000},
            {"value": 250000},
            {"value": 180000},  # 28% drawdown from 250000
        ]
        dd_pct, dd_amt = _calculate_max_drawdown(equity)
        assert abs(dd_pct - 0.28) < 0.001
        assert dd_amt == 70000.0


class TestDailyReturns:
    def test_returns_calculation(self):
        equity = [
            {"value": 100},
            {"value": 110},
            {"value": 105},
        ]
        returns = _calculate_daily_returns(equity)
        assert len(returns) == 2
        assert abs(returns[0] - 0.1) < 0.001  # 10%
        assert abs(returns[1] - (-0.0455)) < 0.001  # -4.55%

    def test_single_point(self):
        assert _calculate_daily_returns([{"value": 100}]) == []


class TestSharpeRatio:
    def test_positive_returns(self):
        # Positive returns with real dispersion should give positive Sharpe.
        # They must vary — a constant series has std == 0, which is the
        # zero-volatility case asserted in test_zero_volatility below.
        returns = [0.01 + 0.002 * ((i % 5) - 2) for i in range(100)]
        sharpe = _sharpe_ratio(returns)
        assert sharpe > 0

    def test_zero_volatility(self):
        # All same returns = zero std dev
        returns = [0.001] * 100
        # Note: after subtracting risk-free, these are all the same
        sharpe = _sharpe_ratio(returns)
        assert sharpe == 0.0  # std = 0

    def test_empty(self):
        assert _sharpe_ratio([]) == 0.0


class TestSortinoRatio:
    def test_all_positive(self):
        returns = [0.01, 0.02, 0.015, 0.008]
        sortino = _sortino_ratio(returns)
        # With only positive excess returns, downside dev is small
        assert sortino == 0.0 or sortino > 0

    def test_empty(self):
        assert _sortino_ratio([]) == 0.0


class TestCAGR:
    def test_basic_cagr(self):
        # 100000 -> 200000 in 3 years
        c = _cagr(100000, 200000, 3)
        assert abs(c - 0.2599) < 0.001  # ~26%

    def test_no_growth(self):
        c = _cagr(100000, 100000, 1)
        assert c == 0.0

    def test_negative_growth(self):
        c = _cagr(100000, 80000, 1)
        assert c < 0

    def test_zero_initial(self):
        assert _cagr(0, 100000, 1) == 0.0

    def test_zero_years(self):
        assert _cagr(100000, 200000, 0) == 0.0
