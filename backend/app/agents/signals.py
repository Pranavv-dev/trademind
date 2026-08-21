from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Signal:
    symbol: str
    exchange: str
    action: str  # 'BUY', 'SELL', 'HOLD'
    confidence: float  # 0.0 to 1.0
    entry_price: float
    stop_loss: float
    take_profit: float
    reasoning: str
    agent_id: str
    agent_name: str
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class MarketSnapshot:
    symbol: str
    exchange: str
    ltp: float
    open: float
    high: float
    low: float
    close: float  # Previous close
    volume: int
    candles_5m: list[dict] = field(default_factory=list)
    candles_15m: list[dict] = field(default_factory=list)
    candles_1d: list[dict] = field(default_factory=list)
    regime: str = "unknown"  # trending_up, trending_down, ranging, volatile
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class TradeProposal:
    signal: Signal
    order_type: str  # 'MARKET', 'LIMIT'
    product: str  # 'CNC', 'MIS', 'NRML'
    quantity: int
    stop_loss: float
    take_profit: float
