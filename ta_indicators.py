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


# ── Support / Resistance ──────────────────────────────────
def calc_support_resistance(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Calculate support and resistance levels using:
      - Bollinger Bands (already in df)
      - Swing High/Low (lookback days)
      - Pivot Points (from latest bar)

    Returns:
        dict: {
            'support': float,
            'resistance': float,
            'position': float (0-1, where 0=at support, 1=at resistance),
            'pivot': float,
            'r1': float,
            's1': float,
            'swing_high': float,
            'swing_low': float,
        }
    """
    latest = df.iloc[-1]
    prev_close = df.iloc[-2]['close'] if len(df) >= 2 else latest['close']

    # Pivot Points (from latest day)
    h, l, c = latest['high'], latest['low'], latest['close']
    pivot = (h + l + c) / 3
    r1 = 2 * pivot - l
    s1 = 2 * pivot - h

    # Swing High/Low (lookback days)
    recent = df.tail(lookback)
    swing_high = recent['high'].max()
    swing_low = recent['low'].min()

    # Combine: resistance = max of candidates, support = min of candidates
    bb_upper = latest.get('bb_upper')
    bb_lower = latest.get('bb_lower')

    res_candidates = [x for x in [r1, swing_high, bb_upper] if x is not None and x == x]  # filter NaN
    sup_candidates = [x for x in [s1, swing_low, bb_lower] if x is not None and x == x]

    resistance = max(res_candidates) if res_candidates else pivot * 1.05
    support = min(sup_candidates) if sup_candidates else pivot * 0.95

    # Position: 0 = at support, 1 = at resistance
    price = latest['close']
    range_size = resistance - support
    if range_size > 0:
        position = (price - support) / range_size
        position = max(0.0, min(1.0, position))  # clamp to [0, 1]
    else:
        position = 0.5

    return {
        'support': round(support, 2),
        'resistance': round(resistance, 2),
        'position': round(position, 4),
        'pivot': round(pivot, 2),
        'r1': round(r1, 2),
        's1': round(s1, 2),
        'swing_high': round(swing_high, 2),
        'swing_low': round(swing_low, 2),
    }
