"""Backtest API routes — run strategy backtests on historical data."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    BacktestRequest,
    BacktestResults,
    BacktestStatus,
    BacktestTradeRecord,
)
from app.backtest.engine import run_backtest
from app.db.repositories.candle_repo import CandleRepository
from app.dependencies import get_db

router = APIRouter()


@router.post("/run", response_model=BacktestResults)
async def run_backtest_endpoint(
    body: BacktestRequest,
    session: AsyncSession = Depends(get_db),
):
    """Run a backtest synchronously and return results.

    Accepts either a single `symbol` or a `universe` list.
    For single-symbol backtests, runs the agent on historical candles and
    returns full performance metrics.
    """
    # Determine symbols to backtest
    symbols = []
    if body.symbol:
        symbols = [body.symbol.upper()]
    elif body.universe:
        symbols = [s.upper() for s in body.universe]
    else:
        raise HTTPException(status_code=400, detail="Provide 'symbol' or 'universe'")

    if len(symbols) > 1:
        raise HTTPException(
            status_code=400,
            detail="Multi-symbol backtests not yet supported. Provide a single symbol.",
        )

    symbol = symbols[0]

    # Fetch historical candles from DB
    candle_repo = CandleRepository(session)
    start_dt = datetime.combine(body.start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_dt = datetime.combine(body.end_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    candles_raw = await candle_repo.get_candles(
        symbol=symbol,
        exchange="NSE",
        timeframe="1d",
        start=start_dt,
        end=end_dt,
        limit=10000,
    )

    if not candles_raw:
        raise HTTPException(
            status_code=404,
            detail=f"No historical data found for {symbol} between "
            f"{body.start_date} and {body.end_date}. "
            f"Download data first via the data downloader.",
        )

    # Convert ORM objects to dicts, sorted oldest-first
    candles = sorted(
        [
            {
                "symbol": c.symbol,
                "exchange": c.exchange,
                "time": c.time,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": int(c.volume),
            }
            for c in candles_raw
        ],
        key=lambda x: x["time"],
    )

    if len(candles) < 60:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient data: {len(candles)} candles found, minimum 60 required. "
            f"Try a wider date range or download more data.",
        )

    # Run backtest
    metrics = await run_backtest(
        strategy_type=body.strategy_type,
        symbol=symbol,
        candles=candles,
        initial_capital=float(body.initial_capital),
        config=body.config,
        position_size_pct=float(body.position_size_pct),
    )

    # Build trade records from metrics
    trade_records = [BacktestTradeRecord(**t) for t in metrics.trades]

    return BacktestResults(
        total_trades=metrics.total_trades,
        winning_trades=metrics.winning_trades,
        losing_trades=metrics.losing_trades,
        win_rate=metrics.win_rate,
        total_pnl=metrics.total_pnl,
        total_pnl_pct=metrics.total_pnl_pct,
        max_drawdown=metrics.max_drawdown,
        max_drawdown_amount=metrics.max_drawdown_amount,
        sharpe_ratio=metrics.sharpe_ratio,
        sortino_ratio=metrics.sortino_ratio,
        cagr=metrics.cagr,
        profit_factor=metrics.profit_factor,
        avg_win=metrics.avg_win,
        avg_loss=metrics.avg_loss,
        equity_curve=metrics.equity_curve,
        trades=trade_records,
    )


# ── Walk-forward (Phase B / Pattern #3) ──


@router.post("/walk-forward")
async def run_walk_forward(
    body: dict,
    session: AsyncSession = Depends(get_db),
):
    """Run a walk-forward backtest with point-in-time universe gating.

    Body shape:
        {
          "strategy_type": "proactive",     # or "technical" / "intraday_technical"
          "start_date": "2024-01-01",
          "end_date":   "2025-12-31",
          "index_name": "NIFTY50",
          "initial_capital": 200000,
          "train_months": 12,
          "test_months": 3,
          "step_months": 1,
          "agent_config": {}                # optional, passed to agent constructor
        }
    """
    from datetime import date as _date

    from app.agents.orchestrator import AGENT_CLASSES
    from app.backtest.walk_forward import WalkForwardBacktest

    strategy_type = body.get("strategy_type")
    if strategy_type not in AGENT_CLASSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy_type '{strategy_type}'. "
            f"Choices: {list(AGENT_CLASSES.keys())}",
        )

    def _parse_date(v):
        if isinstance(v, _date):
            return v
        return _date.fromisoformat(str(v))

    try:
        start = _parse_date(body["start_date"])
        end = _parse_date(body["end_date"])
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"start_date / end_date invalid: {e}")

    cls = AGENT_CLASSES[strategy_type]
    agent_config = body.get("agent_config") or {}

    def agent_factory():
        return cls(
            agent_id=f"bt-{strategy_type}",
            name=f"backtest-{strategy_type}",
            config=agent_config,
        )

    bt = WalkForwardBacktest(
        session=session,
        agent_factory=agent_factory,
        start=start,
        end=end,
        index_name=body.get("index_name", "NIFTY50"),
        initial_capital=float(body.get("initial_capital", 200_000.0)),
        train_months=int(body.get("train_months", 12)),
        test_months=int(body.get("test_months", 3)),
        step_months=int(body.get("step_months", 1)),
        position_size_pct=float(body.get("position_size_pct", 5.0)),
    )
    summary = await bt.run()

    return {
        "agent_name": summary.agent_name,
        "index_name": summary.index_name,
        "start": summary.start.isoformat(),
        "end": summary.end.isoformat(),
        "n_windows": summary.n_windows,
        "universe_method": summary.universe_method,
        "cost_model_active": summary.cost_model_active,
        "overall": {
            "total_trades": summary.overall_metrics.total_trades,
            "win_rate": summary.overall_metrics.win_rate,
            "total_pnl": summary.overall_metrics.total_pnl,
            "sharpe_ratio": summary.overall_metrics.sharpe_ratio,
            "sortino_ratio": summary.overall_metrics.sortino_ratio,
            "max_drawdown": summary.overall_metrics.max_drawdown,
            "profit_factor": summary.overall_metrics.profit_factor,
            "cagr": summary.overall_metrics.cagr,
        },
        "windows": [
            {
                "test_start": w.window.test_start.isoformat(),
                "test_end": w.window.test_end.isoformat(),
                "universe_size": w.universe_size,
                "symbols_traded": w.symbols_traded,
                "trades": w.metrics.total_trades,
                "win_rate": w.metrics.win_rate,
                "pnl": w.metrics.total_pnl,
                "sharpe": w.metrics.sharpe_ratio,
                "max_dd": w.metrics.max_drawdown,
            }
            for w in summary.window_results
        ],
    }


# ── Async backtest tracking (for future use) ──

_backtests: dict[uuid.UUID, dict] = {}


@router.post("/run/async", response_model=BacktestStatus, status_code=202)
async def run_backtest_async(body: BacktestRequest):
    """Queue a backtest for async execution (future: Celery task)."""
    backtest_id = uuid.uuid4()
    _backtests[backtest_id] = {
        "status": "queued",
        "request": body.model_dump(mode="json"),
    }
    return BacktestStatus(backtest_id=backtest_id, status="queued")


@router.get("/{backtest_id}/status", response_model=BacktestStatus)
async def get_backtest_status(backtest_id: uuid.UUID):
    bt = _backtests.get(backtest_id)
    if not bt:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return BacktestStatus(backtest_id=backtest_id, status=bt["status"])
