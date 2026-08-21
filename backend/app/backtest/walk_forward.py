"""Walk-forward backtest orchestrator with point-in-time universe gating.

Pattern #3 from STRATEGY_RESEARCH.md. Orchestrates the existing single-symbol
BacktestEngine across:
  1. multiple symbols (the universe at the time of each window),
  2. multiple time windows (walk-forward).

For each window:
  - Get the universe that existed AT THAT TIME via IndexMembershipRepository
  - Run BacktestEngine on each symbol's candles within the window
  - Aggregate trades + equity across symbols
  - Return aggregated metrics per window

The orchestrator emits a `BacktestRunSummary` with per-window metrics so the
caller can inspect performance stability across regimes — the key safeguard
against curve-fitting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.cv import WalkForwardWindow, walk_forward_windows
from app.backtest.engine import BacktestEngine
from app.backtest.metrics import BacktestMetrics, calculate_metrics
from app.data.universe import NIFTY50
from app.db.repositories.candle_repo import CandleRepository
from app.db.repositories.index_membership_repo import IndexMembershipRepository

log = structlog.get_logger()


@dataclass
class WindowResult:
    """Performance for one walk-forward window across all symbols traded in it."""

    window: WalkForwardWindow
    universe_size: int
    metrics: BacktestMetrics
    symbols_traded: list[str] = field(default_factory=list)


@dataclass
class BacktestRunSummary:
    """Top-level backtest result spanning multiple walk-forward windows."""

    agent_name: str
    index_name: str
    start: date
    end: date
    train_months: int
    test_months: int
    n_windows: int
    overall_metrics: BacktestMetrics
    window_results: list[WindowResult]
    cost_model_active: bool
    universe_method: str  # "point_in_time" | "current_only_fallback"


class WalkForwardBacktest:
    """Run a strategy across walk-forward windows with point-in-time universe.

    Usage:
        bt = WalkForwardBacktest(
            session=db_session,
            agent_factory=lambda: TechnicalAgent(agent_id="bt", name="bt", config={}),
            start=date(2024, 1, 1),
            end=date(2025, 12, 31),
            index_name="NIFTY50",
            initial_capital=200_000.0,
        )
        summary = await bt.run()
    """

    def __init__(
        self,
        session: AsyncSession,
        agent_factory,
        start: date,
        end: date,
        index_name: str = "NIFTY50",
        initial_capital: float = 200_000.0,
        train_months: int = 12,
        test_months: int = 3,
        step_months: int = 1,
        position_size_pct: float = 5.0,
        history_window: int = 60,
    ):
        self.session = session
        self.agent_factory = agent_factory
        self.start = start
        self.end = end
        self.index_name = index_name
        self.initial_capital = initial_capital
        self.train_months = train_months
        self.test_months = test_months
        self.step_months = step_months
        self.position_size_pct = position_size_pct
        self.history_window = history_window
        self.candle_repo = CandleRepository(session)
        self.universe_repo = IndexMembershipRepository(session)

    async def run(self) -> BacktestRunSummary:
        # Check whether index_membership has been seeded; warn if falling back.
        has_membership = await self.universe_repo.has_any(self.index_name)
        universe_method = "point_in_time" if has_membership else "current_only_fallback"
        if not has_membership:
            log.warning(
                "walk_forward_universe_fallback",
                reason="index_membership table empty — using current NIFTY 50 (survivorship bias!)",
            )

        windows = list(
            walk_forward_windows(
                start=self.start,
                end=self.end,
                train_months=self.train_months,
                test_months=self.test_months,
                step_months=self.step_months,
            )
        )

        log.info(
            "walk_forward_starting",
            n_windows=len(windows),
            train_months=self.train_months,
            test_months=self.test_months,
            universe_method=universe_method,
        )

        agent_name = self.agent_factory().__class__.__name__
        all_trades: list[dict] = []
        all_equity_points: list = []  # raw (date, equity) tuples
        window_results: list[WindowResult] = []

        # Track equity across windows by starting each new window's capital where
        # the previous one ended. This makes overall metrics reflect a single
        # continuous account rather than independent windows.
        running_capital = self.initial_capital

        for window in windows:
            # Resolve the universe as of the test_start date — that's what the
            # strategy "knows" when entering trades during this window.
            if universe_method == "point_in_time":
                universe = await self.universe_repo.universe_as_of(
                    self.index_name, window.test_start
                )
            else:
                universe = list(NIFTY50)

            if not universe:
                log.warning("walk_forward_empty_universe", test_start=str(window.test_start))
                continue

            window_trades: list[dict] = []
            window_equity_points: list = []
            symbols_actually_traded: list[str] = []

            for symbol in universe:
                # Fetch candles spanning the test window + a history buffer for warmup
                # so the agent has enough context at window start.
                from datetime import timedelta

                history_start = window.test_start - timedelta(days=int(self.history_window * 1.5))
                history_start_dt = datetime.combine(history_start, datetime.min.time())
                test_end_dt = datetime.combine(window.test_end, datetime.max.time())

                candles_models = await self.candle_repo.get_candles(
                    symbol=symbol,
                    exchange="NSE",
                    timeframe="1d",
                    start=history_start_dt,
                    end=test_end_dt,
                    limit=2000,
                )
                if len(candles_models) < self.history_window + 5:
                    continue

                # Chronological order
                candles_models = list(reversed(candles_models))
                candles = [
                    {
                        "time": c.time,
                        "symbol": symbol,
                        "exchange": "NSE",
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": int(c.volume),
                    }
                    for c in candles_models
                ]

                # Run the single-symbol engine on this window's candles.
                # The engine itself does as-of-date implicit gating since each
                # signal sees only the candles up to the current bar.
                agent = self.agent_factory()
                engine = BacktestEngine(
                    agent=agent,
                    candles=candles,
                    initial_capital=running_capital,
                    position_size_pct=self.position_size_pct,
                    history_window=self.history_window,
                )
                try:
                    # Return value unused; the run populates engine.simulator.
                    await engine.run()
                except Exception:
                    log.exception(
                        "walk_forward_symbol_failed",
                        symbol=symbol,
                        window_test_start=str(window.test_start),
                    )
                    continue

                trades = engine.simulator.get_trade_dicts()
                if trades:
                    window_trades.extend(trades)
                    symbols_actually_traded.append(symbol)
                window_equity_points.extend(engine.simulator.equity_curve)

            # Build per-window metrics
            window_metrics = calculate_metrics(
                trades=window_trades,
                equity_curve=window_equity_points,
                initial_capital=running_capital,
                trading_days=int((window.test_end - window.test_start).days),
            )
            window_results.append(
                WindowResult(
                    window=window,
                    universe_size=len(universe),
                    metrics=window_metrics,
                    symbols_traded=symbols_actually_traded,
                )
            )

            # Advance capital for the next window
            running_capital = max(running_capital + window_metrics.total_pnl, 1.0)

            all_trades.extend(window_trades)
            all_equity_points.extend(window_equity_points)

            log.info(
                "walk_forward_window_complete",
                test_start=str(window.test_start),
                test_end=str(window.test_end),
                universe_size=len(universe),
                trades=window_metrics.total_trades,
                pnl=round(window_metrics.total_pnl, 2),
                sharpe=round(window_metrics.sharpe_ratio, 3),
            )

        # Overall metrics across all windows
        overall = calculate_metrics(
            trades=all_trades,
            equity_curve=all_equity_points,
            initial_capital=self.initial_capital,
            trading_days=int((self.end - self.start).days),
        )

        summary = BacktestRunSummary(
            agent_name=agent_name,
            index_name=self.index_name,
            start=self.start,
            end=self.end,
            train_months=self.train_months,
            test_months=self.test_months,
            n_windows=len(window_results),
            overall_metrics=overall,
            window_results=window_results,
            cost_model_active=True,  # PaperBroker now applies slippage
            universe_method=universe_method,
        )
        log.info(
            "walk_forward_complete",
            agent=agent_name,
            n_windows=len(window_results),
            overall_pnl=round(overall.total_pnl, 2),
            overall_sharpe=round(overall.sharpe_ratio, 3),
            universe_method=universe_method,
        )
        return summary
