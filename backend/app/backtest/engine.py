"""Backtesting engine — replays historical candles through the agent pipeline.

Walk-forward simulation:
1. Load historical daily candles for the symbol(s)
2. For each trading day, build a MarketSnapshot from the history window
3. Feed snapshot to the selected agent's analyze() method
4. Process the resulting signal through the trade simulator
5. Check stop-loss / take-profit exits using intraday high/low
6. Record equity curve
7. After all days processed, close remaining positions and compute metrics
"""

from datetime import date, datetime, timezone

import structlog

from app.agents.base import BaseAgent
from app.agents.signals import MarketSnapshot
from app.backtest.metrics import BacktestMetrics, calculate_metrics
from app.backtest.simulator import TradeSimulator

log = structlog.get_logger()

# Minimum number of historical candles needed before we start generating signals
MIN_HISTORY_WINDOW = 60


class BacktestEngine:
    """Core backtesting engine.

    Usage:
        engine = BacktestEngine(
            agent=my_agent,
            candles=daily_candle_list,
            initial_capital=200000,
        )
        results = await engine.run()
    """

    def __init__(
        self,
        agent: BaseAgent,
        candles: list[dict],
        initial_capital: float = 200_000.0,
        position_size_pct: float = 5.0,
        history_window: int = MIN_HISTORY_WINDOW,
    ):
        """
        Args:
            agent: The trading agent to test
            candles: List of daily OHLCV dicts sorted oldest-first.
                     Each: {time, open, high, low, close, volume}
            initial_capital: Starting cash
            position_size_pct: % of portfolio per trade
            history_window: How many candles to feed to agent as history
        """
        self.agent = agent
        self.candles = candles
        self.initial_capital = initial_capital
        self.simulator = TradeSimulator(
            initial_capital=initial_capital,
            position_size_pct=position_size_pct,
        )
        self.history_window = max(history_window, MIN_HISTORY_WINDOW)
        self._symbol = ""
        self._exchange = "NSE"

    async def run(self) -> BacktestMetrics:
        """Execute the backtest. Returns computed performance metrics."""
        if not self.candles:
            log.warning("backtest_no_candles")
            return calculate_metrics([], [], self.initial_capital, 0)

        # Extract symbol/exchange from first candle
        self._symbol = self.candles[0].get("symbol", "UNKNOWN")
        self._exchange = self.candles[0].get("exchange", "NSE")

        await self.agent.on_start()

        trading_days = 0

        for i in range(self.history_window, len(self.candles)):
            current = self.candles[i]
            history = self.candles[max(0, i - self.history_window) : i + 1]
            current_date = self._parse_date(current["time"])
            trading_days += 1

            # 1. Check SL/TP exits using today's high/low
            self.simulator.check_exits(
                self._symbol,
                high=float(current["high"]),
                low=float(current["low"]),
                current_date=current_date,
            )

            # 2. Build MarketSnapshot
            snapshot = self._build_snapshot(current, history)

            # 3. Get signal from agent
            try:
                signal = await self.agent.analyze(snapshot)
            except Exception as e:
                log.warning("backtest_agent_error", day=str(current_date), error=str(e))
                signal = None

            # 4. Process signal
            if signal and signal.action != "HOLD":
                self.simulator.process_signal(
                    symbol=self._symbol,
                    exchange=self._exchange,
                    action=signal.action,
                    price=float(current["close"]),  # Execute at close
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    current_date=current_date,
                    confidence=signal.confidence,
                )

            # 5. Record equity at close
            self.simulator.record_equity(
                current_date,
                {self._symbol: float(current["close"])},
            )

        # Close remaining positions at last known price
        if self.candles:
            last = self.candles[-1]
            last_date = self._parse_date(last["time"])
            self.simulator.close_all_positions(
                {self._symbol: float(last["close"])},
                last_date,
            )

        await self.agent.on_stop()

        # Calculate metrics
        metrics = calculate_metrics(
            trades=self.simulator.get_trade_dicts(),
            equity_curve=self.simulator.equity_curve,
            initial_capital=self.initial_capital,
            trading_days=trading_days,
        )

        log.info(
            "backtest_complete",
            symbol=self._symbol,
            trades=metrics.total_trades,
            pnl=metrics.total_pnl,
            sharpe=metrics.sharpe_ratio,
        )

        return metrics

    def _build_snapshot(self, current: dict, history: list[dict]) -> MarketSnapshot:
        """Build a MarketSnapshot from candle data."""
        # Previous close (for change calculation)
        prev_close = float(history[-2]["close"]) if len(history) >= 2 else float(current["open"])

        candles_1d = [
            {
                "time": str(c["time"]),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": int(c["volume"]),
            }
            for c in history
        ]

        return MarketSnapshot(
            symbol=self._symbol,
            exchange=self._exchange,
            ltp=float(current["close"]),
            open=float(current["open"]),
            high=float(current["high"]),
            low=float(current["low"]),
            close=prev_close,
            volume=int(current["volume"]),
            candles_1d=candles_1d,
            timestamp=datetime.now(timezone.utc),
        )

    @staticmethod
    def _parse_date(value) -> date:
        """Parse a date from various formats."""
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            # Try ISO format
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(value.split("+")[0].split(".")[0], fmt).date()
                except ValueError:
                    continue
        return date.today()


async def run_backtest(
    strategy_type: str,
    symbol: str,
    candles: list[dict],
    initial_capital: float = 200_000.0,
    config: dict | None = None,
    position_size_pct: float = 5.0,
) -> BacktestMetrics:
    """Convenience function to run a backtest with a strategy type string.

    Args:
        strategy_type: "technical", "sentiment", or "reasoning"
        symbol: Stock symbol
        candles: Historical daily candles sorted oldest-first
        initial_capital: Starting capital
        config: Agent-specific config overrides
        position_size_pct: % of portfolio per trade
    """
    agent = _create_agent(strategy_type, config or {})

    # Inject symbol into candles if not present
    for c in candles:
        c.setdefault("symbol", symbol)
        c.setdefault("exchange", "NSE")

    engine = BacktestEngine(
        agent=agent,
        candles=candles,
        initial_capital=initial_capital,
        position_size_pct=position_size_pct,
    )

    return await engine.run()


def _create_agent(strategy_type: str, config: dict) -> BaseAgent:
    """Factory to create an agent instance for backtesting."""
    agent_id = f"backtest-{strategy_type}"
    agent_name = f"Backtest {strategy_type.title()}"

    if strategy_type == "technical":
        from app.agents.technical import TechnicalAgent

        return TechnicalAgent(agent_id=agent_id, name=agent_name, config=config)
    elif strategy_type == "sentiment":
        from app.agents.sentiment import SentimentAgent

        return SentimentAgent(agent_id=agent_id, name=agent_name, config=config)
    else:
        # Default to technical for unknown strategy types
        from app.agents.technical import TechnicalAgent

        log.warning("backtest_unknown_strategy", strategy_type=strategy_type, fallback="technical")
        return TechnicalAgent(agent_id=agent_id, name=agent_name, config=config)
