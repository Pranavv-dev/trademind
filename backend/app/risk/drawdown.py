"""Max drawdown monitor and circuit breaker."""

from decimal import Decimal

import structlog

log = structlog.get_logger()


class DrawdownMonitor:
    """Tracks equity peak and current drawdown. Triggers circuit breaker if threshold exceeded."""

    def __init__(self, max_drawdown_pct: Decimal = Decimal("15.0")):
        self.max_drawdown_pct = max_drawdown_pct
        self.equity_peak: Decimal = Decimal("0")
        self.circuit_breaker_active: bool = False
        self._notify_callback = None

    def set_notify_callback(self, callback) -> None:
        """Set an async callback for circuit breaker notifications."""
        self._notify_callback = callback

    def update(self, current_equity: Decimal) -> Decimal:
        """Update with current equity value. Returns current drawdown percentage."""
        if current_equity > self.equity_peak:
            self.equity_peak = current_equity

        if self.equity_peak <= 0:
            return Decimal("0")

        drawdown_pct = (self.equity_peak - current_equity) / self.equity_peak * 100

        if drawdown_pct >= self.max_drawdown_pct:
            if not self.circuit_breaker_active:
                log.warning(
                    "circuit_breaker_triggered",
                    drawdown_pct=float(drawdown_pct),
                    equity_peak=float(self.equity_peak),
                    current=float(current_equity),
                )
                self.circuit_breaker_active = True
                self._fire_notification(
                    "triggered",
                    float(drawdown_pct),
                    float(self.equity_peak),
                    float(current_equity),
                )
        elif self.circuit_breaker_active and drawdown_pct < self.max_drawdown_pct * Decimal("0.5"):
            # Reset circuit breaker when drawdown recovers to half the threshold
            log.info("circuit_breaker_reset", drawdown_pct=float(drawdown_pct))
            self.circuit_breaker_active = False
            self._fire_notification("reset", float(drawdown_pct), 0, 0)

        return drawdown_pct

    def _fire_notification(
        self,
        event: str,
        drawdown_pct: float,
        equity_peak: float,
        current: float,
    ) -> None:
        """Fire notification callback (non-blocking)."""
        if self._notify_callback is None:
            return
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._notify_callback(event, drawdown_pct, equity_peak, current))
        except RuntimeError:
            # No running loop — skip notification
            pass

    def reset(self, initial_equity: Decimal) -> None:
        """Reset the monitor with a new equity peak."""
        self.equity_peak = initial_equity
        self.circuit_breaker_active = False
