"""Trade simulator for backtesting — tracks positions, cash, and P&L."""

from dataclasses import dataclass
from datetime import date

import structlog

log = structlog.get_logger()


@dataclass
class SimPosition:
    """An open simulated position."""

    symbol: str
    exchange: str
    side: str  # "LONG" or "SHORT"
    quantity: int
    avg_price: float
    stop_loss: float
    take_profit: float
    entry_date: date


@dataclass
class SimTrade:
    """A completed (round-trip) simulated trade."""

    symbol: str
    exchange: str
    side: str  # "BUY" or "SELL"
    quantity: int
    entry_price: float
    exit_price: float
    entry_date: date
    exit_date: date
    pnl: float
    pnl_pct: float
    exit_reason: str  # "signal", "stop_loss", "take_profit"


class TradeSimulator:
    """Simulates trade execution for backtesting.

    Manages portfolio state: cash, positions, and generates trade records
    when positions are opened/closed.
    """

    def __init__(self, initial_capital: float, position_size_pct: float = 5.0):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.position_size_pct = position_size_pct
        self.positions: dict[str, SimPosition] = {}  # symbol -> position
        self.trades: list[SimTrade] = []
        self.equity_curve: list[dict] = []

    @property
    def portfolio_value(self) -> float:
        """Current portfolio value = cash + unrealized positions at avg_price."""
        position_value = sum(pos.quantity * pos.avg_price for pos in self.positions.values())
        return self.cash + position_value

    def calculate_quantity(self, price: float) -> int:
        """Calculate position size based on portfolio value and position_size_pct."""
        if price <= 0:
            return 0
        max_value = self.portfolio_value * (self.position_size_pct / 100)
        qty = int(max_value / price)
        return max(qty, 0)

    def process_signal(
        self,
        symbol: str,
        exchange: str,
        action: str,
        price: float,
        stop_loss: float,
        take_profit: float,
        current_date: date,
        confidence: float = 0.5,
    ) -> SimTrade | None:
        """Process a trading signal. Returns a SimTrade if a position was closed.

        - BUY signal: Opens long if no position, closes short if existing
        - SELL signal: Opens short if no position, closes long if existing
        - HOLD: No action
        """
        if action == "HOLD" or price <= 0:
            return None

        existing = self.positions.get(symbol)

        if existing is not None:
            # Check if signal is in opposite direction → close
            if (existing.side == "LONG" and action == "SELL") or (
                existing.side == "SHORT" and action == "BUY"
            ):
                return self._close_position(symbol, price, current_date, "signal")
            # Same direction → ignore (already in position)
            return None

        # Open new position
        quantity = self.calculate_quantity(price)
        if quantity <= 0:
            return None

        cost = quantity * price
        if cost > self.cash:
            # Not enough cash
            quantity = int(self.cash / price)
            if quantity <= 0:
                return None
            cost = quantity * price

        self.cash -= cost
        side = "LONG" if action == "BUY" else "SHORT"

        self.positions[symbol] = SimPosition(
            symbol=symbol,
            exchange=exchange,
            side=side,
            quantity=quantity,
            avg_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_date=current_date,
        )

        log.debug(
            "bt_position_opened",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
        )
        return None

    def check_exits(
        self,
        symbol: str,
        high: float,
        low: float,
        current_date: date,
    ) -> SimTrade | None:
        """Check if an open position should be closed by stop-loss or take-profit.

        Uses high/low to check if SL or TP was hit during the bar.
        SL is checked first (conservative approach).
        """
        pos = self.positions.get(symbol)
        if pos is None:
            return None

        if pos.side == "LONG":
            # Stop loss: close if low went below SL
            if pos.stop_loss > 0 and low <= pos.stop_loss:
                return self._close_position(symbol, pos.stop_loss, current_date, "stop_loss")
            # Take profit: close if high reached TP
            if pos.take_profit > 0 and high >= pos.take_profit:
                return self._close_position(symbol, pos.take_profit, current_date, "take_profit")
        else:  # SHORT
            # Stop loss: close if high went above SL
            if pos.stop_loss > 0 and high >= pos.stop_loss:
                return self._close_position(symbol, pos.stop_loss, current_date, "stop_loss")
            # Take profit: close if low reached TP
            if pos.take_profit > 0 and low <= pos.take_profit:
                return self._close_position(symbol, pos.take_profit, current_date, "take_profit")

        return None

    def _close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_date: date,
        reason: str,
    ) -> SimTrade:
        """Close an open position and record the trade."""
        pos = self.positions.pop(symbol)

        if pos.side == "LONG":
            pnl = (exit_price - pos.avg_price) * pos.quantity
        else:
            pnl = (pos.avg_price - exit_price) * pos.quantity

        pnl_pct = (pnl / (pos.avg_price * pos.quantity) * 100) if pos.avg_price > 0 else 0.0

        # Return cash
        self.cash += pos.quantity * exit_price

        trade = SimTrade(
            symbol=symbol,
            exchange=pos.exchange,
            side="BUY" if pos.side == "LONG" else "SELL",
            quantity=pos.quantity,
            entry_price=pos.avg_price,
            exit_price=exit_price,
            entry_date=pos.entry_date,
            exit_date=exit_date,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            exit_reason=reason,
        )

        self.trades.append(trade)

        log.debug(
            "bt_position_closed",
            symbol=symbol,
            pnl=trade.pnl,
            reason=reason,
        )
        return trade

    def close_all_positions(self, prices: dict[str, float], close_date: date) -> list[SimTrade]:
        """Close all open positions at given prices (end of backtest)."""
        closed = []
        for symbol in list(self.positions.keys()):
            price = prices.get(symbol, self.positions[symbol].avg_price)
            trade = self._close_position(symbol, price, close_date, "backtest_end")
            closed.append(trade)
        return closed

    def record_equity(self, current_date: date, prices: dict[str, float]) -> None:
        """Record a point on the equity curve using current market prices."""
        position_value = 0.0
        for symbol, pos in self.positions.items():
            price = prices.get(symbol, pos.avg_price)
            position_value += pos.quantity * price

        total_value = self.cash + position_value
        self.equity_curve.append(
            {
                "date": str(current_date),
                "value": round(total_value, 2),
            }
        )

    def get_trade_dicts(self) -> list[dict]:
        """Return trades as plain dicts for serialization."""
        return [
            {
                "symbol": t.symbol,
                "exchange": t.exchange,
                "side": t.side,
                "quantity": t.quantity,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "entry_date": str(t.entry_date),
                "exit_date": str(t.exit_date),
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "exit_reason": t.exit_reason,
            }
            for t in self.trades
        ]
