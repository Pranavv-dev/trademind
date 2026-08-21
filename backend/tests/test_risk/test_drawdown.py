from decimal import Decimal

from app.risk.drawdown import DrawdownMonitor


def test_no_drawdown_at_peak():
    monitor = DrawdownMonitor(max_drawdown_pct=Decimal("15"))
    dd = monitor.update(Decimal("100000"))
    assert dd == Decimal("0")
    assert not monitor.circuit_breaker_active


def test_drawdown_tracks_peak():
    monitor = DrawdownMonitor(max_drawdown_pct=Decimal("15"))
    monitor.update(Decimal("100000"))
    monitor.update(Decimal("110000"))  # New peak
    dd = monitor.update(Decimal("100000"))
    # (110000 - 100000) / 110000 * 100 ≈ 9.09%
    assert float(dd) > 9.0
    assert not monitor.circuit_breaker_active


def test_circuit_breaker_triggers():
    monitor = DrawdownMonitor(max_drawdown_pct=Decimal("15"))
    monitor.update(Decimal("100000"))
    dd = monitor.update(Decimal("84000"))
    # (100000 - 84000) / 100000 * 100 = 16%
    assert float(dd) == 16.0
    assert monitor.circuit_breaker_active


def test_circuit_breaker_resets():
    monitor = DrawdownMonitor(max_drawdown_pct=Decimal("10"))
    monitor.update(Decimal("100000"))
    monitor.update(Decimal("89000"))  # 11% drawdown → circuit breaker
    assert monitor.circuit_breaker_active
    # Recover to less than 5% drawdown (half of 10%)
    monitor.update(Decimal("96000"))  # 4% drawdown
    assert not monitor.circuit_breaker_active


def test_reset():
    monitor = DrawdownMonitor()
    monitor.update(Decimal("100000"))
    monitor.update(Decimal("50000"))  # Massive drawdown
    assert monitor.circuit_breaker_active
    monitor.reset(Decimal("200000"))
    assert not monitor.circuit_breaker_active
    assert monitor.equity_peak == Decimal("200000")
