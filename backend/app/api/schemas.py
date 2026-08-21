import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

# ── Agent Schemas ──


class AgentCreate(BaseModel):
    name: str = Field(..., max_length=100)
    strategy_type: str = Field(
        ..., pattern="^(technical|sentiment|reasoning|ensemble|intraday_technical|proactive)$"
    )
    config: dict = Field(default_factory=dict)
    market: str = Field(..., pattern="^(NSE|BSE|NFO)$")
    universe: dict | None = None
    risk_params: dict | None = None
    capital_allocated: Decimal = Decimal("0")


class AgentUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    config: dict | None = None
    universe: dict | None = None
    risk_params: dict | None = None
    capital_allocated: Decimal | None = None


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    strategy_type: str
    config: dict
    status: str
    market: str
    universe: dict | None
    risk_params: dict | None
    capital_allocated: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Trade Schemas ──


class TradeResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str = ""
    symbol: str
    exchange: str
    side: str
    quantity: int
    price: Decimal
    order_type: str
    product: str
    status: str
    broker_order_id: str | None
    fill_price: Decimal | None
    pnl: Decimal | None
    brokerage: Decimal | None
    ai_reasoning: str | None
    signal_data: dict | None
    risk_check: dict | None
    is_paper: bool
    executed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TradeSummary(BaseModel):
    total_trades: int
    buy_count: int
    sell_count: int
    total_pnl: Decimal
    wins: int
    losses: int
    win_rate: float


# ── Position Schemas ──


class PositionResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    symbol: str
    exchange: str
    side: str
    quantity: int
    avg_price: Decimal
    current_price: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    is_paper: bool
    opened_at: datetime
    closed_at: datetime | None

    model_config = {"from_attributes": True}


# ── Dashboard Schemas ──


class DashboardOverview(BaseModel):
    total_capital: Decimal
    deployed_capital: Decimal
    available_capital: Decimal
    total_pnl_today: Decimal
    total_pnl_overall: Decimal
    active_agents: int
    total_trades_today: int
    win_rate: Decimal
    sharpe_ratio: Decimal | None
    max_drawdown: Decimal | None


class EquityCurvePoint(BaseModel):
    date: date
    portfolio_value: Decimal
    daily_pnl: Decimal


class AgentSummary(BaseModel):
    id: uuid.UUID
    name: str
    strategy_type: str
    status: str
    pnl_today: Decimal
    trades_today: int
    win_rate: Decimal
    last_signal: str | None


# ── Risk Schemas ──


class RiskConfig(BaseModel):
    max_position_size_pct: Decimal = Decimal("10.0")
    max_daily_loss_pct: Decimal = Decimal("3.0")
    max_drawdown_pct: Decimal = Decimal("15.0")
    min_confidence: Decimal = Decimal("0.60")
    max_open_positions: int = 10
    max_sector_exposure_pct: Decimal = Decimal("30.0")
    max_order_rate: int = 10
    max_single_order_value: Decimal = Decimal("200000")


class RiskStatus(BaseModel):
    circuit_breaker_active: bool
    daily_loss: Decimal
    daily_loss_limit: Decimal
    open_positions: int
    max_positions: int
    drawdown_pct: Decimal
    max_drawdown_pct: Decimal


class RiskRejection(BaseModel):
    trade_id: uuid.UUID
    symbol: str
    side: str
    reason: str
    timestamp: datetime


# ── Backtest Schemas ──


class BacktestRequest(BaseModel):
    strategy_type: str
    symbol: str | None = None  # Single symbol (from frontend)
    universe: list[str] | None = None  # Multi-symbol (API)
    config: dict = Field(default_factory=dict)
    start_date: date
    end_date: date
    initial_capital: Decimal = Decimal("200000")
    position_size_pct: Decimal = Decimal("5.0")


class BacktestStatus(BaseModel):
    backtest_id: uuid.UUID
    status: str


class BacktestTradeRecord(BaseModel):
    symbol: str
    exchange: str
    side: str
    quantity: int
    entry_price: float
    exit_price: float
    entry_date: str
    exit_date: str
    pnl: float
    pnl_pct: float
    exit_reason: str


class BacktestResults(BaseModel):
    backtest_id: uuid.UUID | None = None
    total_trades: int
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float
    total_pnl: float
    total_pnl_pct: float = 0.0
    max_drawdown: float
    max_drawdown_amount: float = 0.0
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    cagr: float | None = None
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    equity_curve: list[dict] = Field(default_factory=list)
    trades: list[BacktestTradeRecord] = Field(default_factory=list)


# ── Market Schemas ──


class QuoteResponse(BaseModel):
    symbol: str
    exchange: str
    ltp: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    change: Decimal
    change_pct: Decimal


class StockSearchResult(BaseModel):
    symbol: str
    name: str
    exchange: str


# ── WebSocket Schemas ──


class WSMessage(BaseModel):
    type: str
    data: dict
    timestamp: datetime
