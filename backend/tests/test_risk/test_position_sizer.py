from decimal import Decimal

from app.risk.position_sizer import atr_based_size, fixed_percentage_size, kelly_criterion_size


def test_fixed_percentage_basic():
    qty = fixed_percentage_size(Decimal("100000"), 500.0, Decimal("10"))
    # 10% of 100000 = 10000, 10000/500 = 20 shares
    assert qty == 20


def test_fixed_percentage_rounds_down():
    qty = fixed_percentage_size(Decimal("100000"), 333.0, Decimal("10"))
    # 10000 / 333 = 30.03 → 30
    assert qty == 30


def test_fixed_percentage_zero_price():
    assert fixed_percentage_size(Decimal("100000"), 0, Decimal("10")) == 0


def test_fixed_percentage_zero_capital():
    assert fixed_percentage_size(Decimal("0"), 500.0, Decimal("10")) == 0


def test_atr_based_size_basic():
    # capital=100k, price=1000, atr=20, risk=1%, multiplier=2
    # risk_amount = 100000 * 0.01 = 1000
    # risk_per_share = 20 * 2 = 40
    # qty = 1000 / 40 = 25
    qty = atr_based_size(Decimal("100000"), 1000.0, 20.0, 1.0, 2.0)
    assert qty == 25


def test_atr_based_size_zero_atr():
    assert atr_based_size(Decimal("100000"), 1000.0, 0, 1.0, 2.0) == 0


def test_kelly_basic():
    # win_rate=0.6, avg_win=100, avg_loss=50, fraction=0.25
    # w/l ratio = 2.0, kelly = 0.6 - 0.4/2 = 0.4, quarter = 0.1
    # max_value = 100000 * 0.1 = 10000, qty = 10000/500 = 20
    qty = kelly_criterion_size(Decimal("100000"), 500.0, 0.6, 100, 50, 0.25)
    assert qty == 20


def test_kelly_negative_edge():
    # win_rate=0.3, avg_win=50, avg_loss=100 → negative kelly → 0
    qty = kelly_criterion_size(Decimal("100000"), 500.0, 0.3, 50, 100, 0.25)
    assert qty == 0
