from app.risk.stop_loss import atr_stop, fixed_percentage_stop, trailing_stop


def test_fixed_pct_buy():
    sl = fixed_percentage_stop(1000.0, pct=3.0, side="BUY")
    assert sl == 970.0


def test_fixed_pct_sell():
    sl = fixed_percentage_stop(1000.0, pct=3.0, side="SELL")
    assert sl == 1030.0


def test_atr_stop_buy():
    sl = atr_stop(1000.0, atr=20.0, multiplier=2.0, side="BUY")
    assert sl == 960.0


def test_atr_stop_sell():
    sl = atr_stop(1000.0, atr=20.0, multiplier=2.0, side="SELL")
    assert sl == 1040.0


def test_trailing_stop_buy():
    sl = trailing_stop(980.0, highest_since_entry=1050.0, trail_pct=3.0, side="BUY")
    # 1050 * 0.97 = 1018.5
    assert sl == 1018.5
