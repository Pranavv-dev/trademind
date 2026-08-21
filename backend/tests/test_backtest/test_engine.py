"""Tests for the backtesting engine."""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.signals import MarketSnapshot, Signal
from app.backtest.engine import BacktestEngine, run_backtest, _create_agent


def _make_candles(start_price: float, days: int, trend: float = 0.5) -> list[dict]:
    """Generate synthetic daily candles for testing.

    Args:
        start_price: Starting close price
        days: Number of days
        trend: Daily price change (positive = uptrend)
    """
    candles = []
    price = start_price
    for i in range(days):
        # Month-cycling dates: 28 synthetic "days" per month, capped at December.
        day_num = i
        month = 1 + day_num // 28
        day = 1 + day_num % 28
        if month > 12:
            month = 12
            day = min(day, 28)
        d = date(2024, month, day)

        o = price
        h = price + abs(trend) * 2
        l = price - abs(trend)
        c = price + trend
        candles.append({
            "symbol": "TESTSTOCK",
            "exchange": "NSE",
            "time": d,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 100000 + i * 1000,
        })
        price = c
    return candles


class MockAgent:
    """Mock agent that generates signals based on simple logic."""

    def __init__(self, buy_every_n: int = 10, sell_every_n: int = 15):
        self.agent_id = "mock-agent"
        self.name = "Mock Agent"
        self.config = {}
        self.call_count = 0
        self.buy_every_n = buy_every_n
        self.sell_every_n = sell_every_n

    async def on_start(self):
        pass

    async def on_stop(self):
        pass

    async def analyze(self, snapshot: MarketSnapshot) -> Signal | None:
        self.call_count += 1
        if self.call_count % self.sell_every_n == 0:
            return Signal(
                symbol=snapshot.symbol,
                exchange=snapshot.exchange,
                action="SELL",
                confidence=0.7,
                entry_price=snapshot.ltp,
                stop_loss=snapshot.ltp * 1.03,
                take_profit=snapshot.ltp * 0.95,
                reasoning="Mock sell signal",
                agent_id=self.agent_id,
                agent_name=self.name,
            )
        if self.call_count % self.buy_every_n == 0:
            return Signal(
                symbol=snapshot.symbol,
                exchange=snapshot.exchange,
                action="BUY",
                confidence=0.8,
                entry_price=snapshot.ltp,
                stop_loss=snapshot.ltp * 0.97,
                take_profit=snapshot.ltp * 1.05,
                reasoning="Mock buy signal",
                agent_id=self.agent_id,
                agent_name=self.name,
            )
        return None


class TestBacktestEngine:
    @pytest.mark.asyncio
    async def test_run_empty_candles(self):
        agent = MockAgent()
        engine = BacktestEngine(agent=agent, candles=[], initial_capital=100000)
        metrics = await engine.run()
        assert metrics.total_trades == 0
        assert metrics.total_pnl == 0.0

    @pytest.mark.asyncio
    async def test_run_insufficient_candles(self):
        candles = _make_candles(100, 30)  # Less than MIN_HISTORY_WINDOW
        agent = MockAgent()
        engine = BacktestEngine(agent=agent, candles=candles, initial_capital=100000)
        metrics = await engine.run()
        # With only 30 candles and history_window=60, no signals generated
        assert metrics.total_trades == 0

    @pytest.mark.asyncio
    async def test_run_with_trades(self):
        candles = _make_candles(100, 200, trend=0.2)
        agent = MockAgent(buy_every_n=5, sell_every_n=8)
        engine = BacktestEngine(
            agent=agent, candles=candles, initial_capital=100000, position_size_pct=10.0
        )
        metrics = await engine.run()
        # Should have some trades
        assert metrics.total_trades > 0
        assert len(metrics.equity_curve) > 0

    @pytest.mark.asyncio
    async def test_equity_curve_recorded(self):
        candles = _make_candles(100, 100, trend=0.1)
        agent = MockAgent()
        engine = BacktestEngine(agent=agent, candles=candles, initial_capital=100000)
        metrics = await engine.run()
        # Should have equity curve entries for each trading day past the window
        expected_days = len(candles) - engine.history_window
        assert len(metrics.equity_curve) == expected_days

    @pytest.mark.asyncio
    async def test_positions_closed_at_end(self):
        candles = _make_candles(100, 100, trend=0.5)
        agent = MockAgent(buy_every_n=3, sell_every_n=999)  # Only buys, never sells
        engine = BacktestEngine(agent=agent, candles=candles, initial_capital=100000)
        metrics = await engine.run()
        assert len(engine.simulator.positions) == 0  # All closed at end

    @pytest.mark.asyncio
    async def test_agent_error_handled(self):
        candles = _make_candles(100, 100, trend=0.1)

        class ErrorAgent:
            agent_id = "error"
            name = "Error Agent"
            config = {}

            async def on_start(self): pass
            async def on_stop(self): pass

            async def analyze(self, snapshot):
                raise ValueError("Agent crashed!")

        engine = BacktestEngine(agent=ErrorAgent(), candles=candles, initial_capital=100000)
        # Should not raise — errors are caught per-day
        metrics = await engine.run()
        assert metrics.total_trades == 0

    @pytest.mark.asyncio
    async def test_stop_loss_triggered(self):
        # Create candles with a big drop
        candles = _make_candles(100, 70, trend=0.5)
        # Add a crash candle
        candles.append({
            "symbol": "TESTSTOCK", "exchange": "NSE",
            "time": date(2024, 4, 1),
            "open": candles[-1]["close"],
            "high": candles[-1]["close"],
            "low": candles[-1]["close"] * 0.90,  # 10% drop
            "close": candles[-1]["close"] * 0.92,
            "volume": 500000,
        })

        # Agent that always buys
        class AlwaysBuyAgent:
            agent_id = "always-buy"
            name = "Always Buy"
            config = {}
            _bought = False

            async def on_start(self): pass
            async def on_stop(self): pass

            async def analyze(self, snapshot):
                if not self._bought:
                    self._bought = True
                    return Signal(
                        symbol=snapshot.symbol, exchange=snapshot.exchange,
                        action="BUY", confidence=0.9,
                        entry_price=snapshot.ltp,
                        stop_loss=snapshot.ltp * 0.95,  # 5% SL
                        take_profit=snapshot.ltp * 1.10,
                        reasoning="Buy",
                        agent_id=self.agent_id, agent_name=self.name,
                    )
                return None

        engine = BacktestEngine(
            agent=AlwaysBuyAgent(), candles=candles, initial_capital=100000
        )
        metrics = await engine.run()
        # Should have at least one trade (the SL exit or the final close)
        assert metrics.total_trades >= 1


class TestRunBacktest:
    @pytest.mark.asyncio
    async def test_run_backtest_technical(self):
        candles = _make_candles(100, 120, trend=0.3)

        with patch("app.backtest.engine._create_agent") as mock_create:
            mock_agent = MockAgent(buy_every_n=10, sell_every_n=20)
            mock_create.return_value = mock_agent

            metrics = await run_backtest(
                strategy_type="technical",
                symbol="TESTSTOCK",
                candles=candles,
                initial_capital=100000,
            )
            assert metrics is not None
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_symbol_injected(self):
        candles = [
            {"time": date(2024, 1, i + 1), "open": 100, "high": 105, "low": 95, "close": 100, "volume": 10000}
            for i in range(5)
        ]

        with patch("app.backtest.engine._create_agent") as mock_create:
            mock_agent = MockAgent()
            mock_create.return_value = mock_agent

            await run_backtest("technical", "RELIANCE", candles, 100000)

            # Symbol should be injected into candles
            assert candles[0]["symbol"] == "RELIANCE"
            assert candles[0]["exchange"] == "NSE"


class TestCreateAgent:
    def test_create_technical(self):
        with patch("app.agents.technical.TechnicalAgent") as MockTech:
            MockTech.return_value = MagicMock()
            _create_agent("technical", {"signal_threshold": 0.2})
            MockTech.assert_called_once()

    def test_create_sentiment(self):
        with patch("app.agents.sentiment.SentimentAgent") as MockSent:
            MockSent.return_value = MagicMock()
            _create_agent("sentiment", {})
            MockSent.assert_called_once()

    def test_create_unknown_fallback(self):
        with patch("app.agents.technical.TechnicalAgent") as MockTech:
            MockTech.return_value = MagicMock()
            _create_agent("unknown_strategy", {})
            MockTech.assert_called_once()  # Falls back to technical


class TestParseDate:
    def test_parse_date_obj(self):
        d = date(2024, 1, 15)
        assert BacktestEngine._parse_date(d) == d

    def test_parse_datetime_obj(self):
        dt = datetime(2024, 1, 15, 10, 30)
        assert BacktestEngine._parse_date(dt) == date(2024, 1, 15)

    def test_parse_string_iso(self):
        assert BacktestEngine._parse_date("2024-01-15") == date(2024, 1, 15)

    def test_parse_string_with_time(self):
        assert BacktestEngine._parse_date("2024-01-15 10:30:00") == date(2024, 1, 15)

    def test_parse_string_with_tz(self):
        assert BacktestEngine._parse_date("2024-01-15T10:30:00+05:30") == date(2024, 1, 15)
