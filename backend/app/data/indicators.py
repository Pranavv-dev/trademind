"""Technical indicator calculations using pandas-ta."""

import pandas as pd
import pandas_ta as ta


def calculate_all(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate all indicators on an OHLCV DataFrame.

    Expects columns: open, high, low, close, volume
    Returns the same DataFrame with indicator columns appended.
    """
    if len(df) < 50:
        return df

    # RSI (14-period)
    df["rsi_14"] = ta.rsi(df["close"], length=14)

    # MACD (12, 26, 9)
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df = pd.concat([df, macd], axis=1)

    # Bollinger Bands (20, 2)
    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None:
        df = pd.concat([df, bb], axis=1)

    # SMA (20, 50, 200)
    df["sma_20"] = ta.sma(df["close"], length=20)
    df["sma_50"] = ta.sma(df["close"], length=50)
    df["sma_200"] = ta.sma(df["close"], length=200)

    # EMA (20, 50)
    df["ema_20"] = ta.ema(df["close"], length=20)
    df["ema_50"] = ta.ema(df["close"], length=50)

    # ATR (14-period) — for stop-loss and position sizing
    df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    # ATR 20-period SMA for volatility regime detection
    df["atr_sma_20"] = ta.sma(df["atr_14"], length=20)

    # ADX (14-period) — trend strength
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
    if adx_df is not None:
        df = pd.concat([df, adx_df], axis=1)

    # OBV (On-Balance Volume) — volume confirmation
    df["obv"] = ta.obv(df["close"], df["volume"])
    df["obv_sma_20"] = ta.sma(df["obv"], length=20)

    # VWAP (intraday only — needs cumulative calc per day)
    if "volume" in df.columns and df["volume"].sum() > 0:
        df["vwap"] = ta.vwap(df["high"], df["low"], df["close"], df["volume"])

    return df


def get_rsi_signal(rsi: float | None) -> int:
    """RSI signal: +1 (below 40, oversold zone), -1 (above 60, overbought zone), 0 (neutral)."""
    if rsi is None:
        return 0
    if rsi < 40:
        return 1
    if rsi > 60:
        return -1
    return 0


def get_macd_signal(
    macd_val: float | None,
    macd_signal: float | None,
    prev_macd: float | None,
    prev_signal: float | None,
) -> int:
    """MACD signal: position-based (+1 if MACD > signal, -1 if MACD < signal).
    Crossovers carry extra weight via a 2x return value.
    """
    if any(v is None for v in [macd_val, macd_signal, prev_macd, prev_signal]):
        return 0
    bullish_crossover = prev_macd <= prev_signal and macd_val > macd_signal
    bearish_crossover = prev_macd >= prev_signal and macd_val < macd_signal
    if bullish_crossover:
        return 1
    if bearish_crossover:
        return -1
    # Position-based (weaker signal, no crossover)
    if macd_val > macd_signal:
        return 1
    if macd_val < macd_signal:
        return -1
    return 0


def get_bb_signal(close: float, bb_lower: float | None, bb_upper: float | None) -> int:
    """Bollinger Band signal: +1 (at lower band), -1 (at upper band), 0 (in middle)."""
    if bb_lower is None or bb_upper is None:
        return 0
    if close <= bb_lower:
        return 1
    if close >= bb_upper:
        return -1
    return 0


def get_sma_crossover_signal(
    fast: float | None, slow: float | None, prev_fast: float | None, prev_slow: float | None
) -> int:
    """SMA position signal: +1 if fast > slow (bullish), -1 if fast < slow (bearish).
    Crossovers on daily data are too rare; position-based fires every day.
    """
    if fast is None or slow is None:
        return 0
    if fast > slow:
        return 1
    if fast < slow:
        return -1
    return 0


def get_vwap_signal(close: float, vwap: float | None) -> int:
    """VWAP signal: +1 (below VWAP, potential buy), -1 (above VWAP, potential sell).
    Returns 0 if VWAP is None (happens with daily candles without DatetimeIndex).
    """
    if vwap is None:
        return 0
    pct_diff = (close - vwap) / vwap
    if pct_diff < -0.01:
        return 1
    if pct_diff > 0.01:
        return -1
    return 0


def classify_regime(
    adx: float | None,
    atr: float | None,
    atr_sma: float | None,
    sma_20: float | None,
    sma_50: float | None,
    sma_200: float | None,
) -> str:
    """Classify market regime based on ADX, ATR volatility, and SMA alignment.

    Returns: 'trending_up', 'trending_down', 'ranging', or 'volatile'
    """
    # Volatile: ATR expanding beyond 1.5x its own 20-period average
    if atr is not None and atr_sma is not None and atr_sma > 0:
        if atr > atr_sma * 1.5:
            return "volatile"

    # Trending: ADX > 25, direction from SMA alignment
    if adx is not None and adx > 25:
        if sma_20 is not None and sma_50 is not None:
            if sma_20 > sma_50:
                return "trending_up"
            else:
                return "trending_down"
        # ADX high but can't determine direction — assume bearish (conservative)
        return "trending_down"

    # Ranging: ADX < 20
    if adx is not None and adx < 20:
        return "ranging"

    # ADX 20-25: transitional — treat as ranging
    return "ranging"


def get_obv_signal(
    obv: float | None, obv_sma: float | None, close: float | None, prev_close: float | None
) -> int:
    """OBV divergence signal.

    Price up + OBV falling = bearish divergence (-1)
    Price down + OBV rising = bullish divergence (+1)
    Otherwise = 0 (no divergence)
    """
    if any(v is None for v in [obv, obv_sma, close, prev_close]):
        return 0

    price_up = close > prev_close
    obv_up = obv > obv_sma

    if price_up and not obv_up:
        return -1  # Bearish divergence
    if not price_up and obv_up:
        return 1  # Bullish divergence
    return 0


def get_52w_proximity_signal(df: "pd.DataFrame", rsi: float | None = None) -> int:
    """52-week high/low proximity signal from existing candle data.

    Within 5% of 52-week high → breakout candidate (+1)
    Within 5% of 52-week low → bounce candidate (+1 if RSI oversold, else -1)
    Otherwise → 0
    """
    if len(df) < 200:
        return 0

    close = df["close"].iloc[-1]
    high_52w = df["high"].tail(252).max()
    low_52w = df["low"].tail(252).min()

    if high_52w <= 0:
        return 0

    pct_from_high = (high_52w - close) / high_52w
    pct_from_low = (close - low_52w) / low_52w if low_52w > 0 else 1.0

    if pct_from_high <= 0.05:
        return 1  # Near 52-week high — breakout candidate
    if pct_from_low <= 0.05:
        # Near 52-week low — bounce if oversold, else breakdown
        if rsi is not None and rsi < 35:
            return 1  # Oversold bounce candidate
        return -1  # Likely breakdown
    return 0


def calculate_atr_stop(
    close: float, atr: float | None, multiplier: float = 2.0, side: str = "BUY"
) -> float | None:
    """Calculate ATR-based stop-loss. Direction-aware for BUY (below) and SELL (above)."""
    if atr is None:
        return None
    if side == "SELL":
        return close + (atr * multiplier)
    return close - (atr * multiplier)
