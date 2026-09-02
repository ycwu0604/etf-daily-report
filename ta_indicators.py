"""
Technical analysis indicator calculations (pandas-based).
==========================================================
Pure computation — no I/O. Input: pandas DataFrame with columns
[open, high, low, close, volume] indexed by date.

Usage:
    from ta_indicators import calc_indicators
    df = ...  # 80+ rows, ascending date
    ind = calc_indicators(df)
    # ind has columns: ma5, ma10, ma20, ma60, macd_dif, macd_dea,
    #                  macd_hist, rsi14, atr14, bb_upper, bb_mid, bb_lower, vol_ma5
"""

import pandas as pd


def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate all TA indicators on an OHLCV DataFrame.
    Returns a copy of df with indicator columns added.
    """
    out = df.copy()
    close = out['close']
    high = out['high']
    low = out['low']
    vol = out['volume']

    # ── Moving Averages ──
    out['ma5'] = close.rolling(5).mean()
    out['ma10'] = close.rolling(10).mean()
    out['ma20'] = close.rolling(20).mean()
    out['ma60'] = close.rolling(60).mean()

    # ── MACD (12, 26, 9) ──
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out['macd_dif'] = ema12 - ema26
    out['macd_dea'] = out['macd_dif'].ewm(span=9, adjust=False).mean()
    out['macd_hist'] = out['macd_dif'] - out['macd_dea']

    # ── RSI (14) ──
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float('nan'))
    out['rsi14'] = 100 - (100 / (1 + rs))

    # ── ATR (14) ──
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    out['atr14'] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    # ── Bollinger Bands (20, 2) ──
    out['bb_mid'] = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    out['bb_upper'] = out['bb_mid'] + 2 * bb_std
    out['bb_lower'] = out['bb_mid'] - 2 * bb_std

    # ── Volume MA (5) ──
    out['vol_ma5'] = vol.rolling(5).mean()

    return out
