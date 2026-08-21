"""Performance metrics for backtesting — Sharpe, Sortino, drawdown, etc."""

import math
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()


@dataclass
class BacktestMetrics:
    """Calculated performance metrics from a backtest run."""

    total_pnl: float
    total_pnl_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    max_drawdown: float  # As a fraction (e.g. 0.05 = 5%)
    max_drawdown_amount: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    cagr: float | None
    # Equity curve (daily portfolio values)
    equity_curve: list[dict]  # [{date, value}]
    # Individual trade records
    trades: list[dict] = field(default_factory=list)


def calculate_metrics(
    trades: list[dict],
    equity_curve: list[dict],
    initial_capital: float,
    trading_days: int,
) -> BacktestMetrics:
    """Calculate all performance metrics from trade history and equity curve.

    Args:
        trades: List of executed trades with 'pnl' field
        equity_curve: Daily portfolio values [{date, value}]
        initial_capital: Starting capital
        trading_days: Number of trading days in the backtest period
    """
    if not trades:
        return BacktestMetrics(
            total_pnl=0.0,
            total_pnl_pct=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            profit_factor=0.0,
            max_drawdown=0.0,
            max_drawdown_amount=0.0,
            sharpe_ratio=None,
            sortino_ratio=None,
            cagr=None,
            equity_curve=equity_curve,
            trades=trades,
        )

    # ── Trade-level metrics ──
    pnls = [t["pnl"] for t in trades if t.get("pnl") is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_pnl = sum(pnls)
    total_pnl_pct = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0.0
    total_trades = len(pnls)
    winning_trades = len(wins)
    losing_trades = len(losses)
    win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
    avg_win = sum(wins) / winning_trades if wins else 0.0
    avg_loss = sum(losses) / losing_trades if losses else 0.0
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = (
        gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
    )

    # ── Drawdown ──
    max_dd, max_dd_amount = _calculate_max_drawdown(equity_curve)

    # ── Daily returns for Sharpe/Sortino ──
    daily_returns = _calculate_daily_returns(equity_curve)

    sharpe = _sharpe_ratio(daily_returns) if len(daily_returns) >= 5 else None
    sortino = _sortino_ratio(daily_returns) if len(daily_returns) >= 5 else None

    # ── CAGR ──
    years = trading_days / 252 if trading_days > 0 else 0
    final_value = equity_curve[-1]["value"] if equity_curve else initial_capital
    cagr = _cagr(initial_capital, final_value, years) if years > 0 else None

    return BacktestMetrics(
        total_pnl=round(total_pnl, 2),
        total_pnl_pct=round(total_pnl_pct, 2),
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=round(win_rate, 4),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        profit_factor=round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
        max_drawdown=round(max_dd, 4),
        max_drawdown_amount=round(max_dd_amount, 2),
        sharpe_ratio=round(sharpe, 2) if sharpe is not None else None,
        sortino_ratio=round(sortino, 2) if sortino is not None else None,
        cagr=round(cagr, 4) if cagr is not None else None,
        equity_curve=equity_curve,
        trades=trades,
    )


def _calculate_max_drawdown(equity_curve: list[dict]) -> tuple[float, float]:
    """Return (max_drawdown_pct, max_drawdown_amount) from equity curve."""
    if not equity_curve:
        return 0.0, 0.0

    peak = equity_curve[0]["value"]
    max_dd_pct = 0.0
    max_dd_amount = 0.0

    for point in equity_curve:
        value = point["value"]
        if value > peak:
            peak = value
        drawdown = peak - value
        drawdown_pct = drawdown / peak if peak > 0 else 0.0
        if drawdown_pct > max_dd_pct:
            max_dd_pct = drawdown_pct
            max_dd_amount = drawdown

    return max_dd_pct, max_dd_amount


def _calculate_daily_returns(equity_curve: list[dict]) -> list[float]:
    """Calculate daily returns from equity curve."""
    if len(equity_curve) < 2:
        return []

    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]["value"]
        curr = equity_curve[i]["value"]
        if prev > 0:
            returns.append((curr - prev) / prev)
    return returns


def _sharpe_ratio(daily_returns: list[float], risk_free_rate: float = 0.065) -> float:
    """Annualized Sharpe ratio. risk_free_rate is annualized (6.5% for Indian govt bonds)."""
    if not daily_returns:
        return 0.0

    daily_rf = (1 + risk_free_rate) ** (1 / 252) - 1
    excess = [r - daily_rf for r in daily_returns]

    mean_excess = sum(excess) / len(excess)
    variance = sum((r - mean_excess) ** 2 for r in excess) / len(excess)
    std = math.sqrt(variance)

    if std == 0:
        return 0.0

    return (mean_excess / std) * math.sqrt(252)


def _sortino_ratio(daily_returns: list[float], risk_free_rate: float = 0.065) -> float:
    """Annualized Sortino ratio — only penalizes downside volatility."""
    if not daily_returns:
        return 0.0

    daily_rf = (1 + risk_free_rate) ** (1 / 252) - 1
    excess = [r - daily_rf for r in daily_returns]

    mean_excess = sum(excess) / len(excess)
    downside = [min(r, 0) ** 2 for r in excess]
    downside_dev = math.sqrt(sum(downside) / len(downside))

    if downside_dev == 0:
        return 0.0

    return (mean_excess / downside_dev) * math.sqrt(252)


def _cagr(initial: float, final: float, years: float) -> float:
    """Compound Annual Growth Rate."""
    if initial <= 0 or years <= 0:
        return 0.0
    return (final / initial) ** (1 / years) - 1
