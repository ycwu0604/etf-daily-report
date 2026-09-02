"""
Two-step technical analysis classification.
============================================
Step 1: Direction (bullish / bearish / neutral) via weighted score.
Step 2: Stage (初/主/末) via MACD histogram slope.

Input: latest row of indicator DataFrame (from ta_indicators.calc_indicators)
       plus the row from 3 days ago (for slope_3d).
Output: dict with direction, stage, score, confidence, signals.
"""


def classify(latest: dict, prev3: dict) -> dict:
    """
    Classify stock's trend stage.

    Args:
        latest: dict with keys from ta_indicators output for the most recent day:
            close, ma5, ma10, ma20, ma60,
            macd_dif, macd_dea, macd_hist,
            rsi14, atr14, bb_upper, bb_mid, bb_lower,
            volume, vol_ma5
        prev3: dict with macd_hist value from 3 trading days ago (for slope calc)

    Returns:
        dict: {
            'direction': 'bullish' | 'bearish' | 'neutral',
            'stage': '初升' | '主升' | '末升' | '初跌' | '主跌' | '末跌' | '整理',
            'score': float (direction score, -1 to +1),
            'confidence': 'high' | 'medium' | 'low',
            'signals': { ... raw indicator values ... }
        }
    """
    # ── Extract values ──
    close = latest.get('close', 0)
    ma5, ma10, ma20, ma60 = latest.get('ma5'), latest.get('ma10'), latest.get('ma20'), latest.get('ma60')
    macd_hist = latest.get('macd_hist', 0)
    macd_hist_prev3 = prev3.get('macd_hist', 0)
    rsi = latest.get('rsi14', 50)
    atr = latest.get('atr14', 0)
    volume = latest.get('volume', 0)
    vol_ma5 = latest.get('vol_ma5', 0)

    # ── Step 1: Direction Score ──
    score = 0.0
    agree = 0  # count of dimensions agreeing with final direction
    total_dims = 0

    # 1a. MACD Histogram position (40%)
    if macd_hist is None:
        macd_pos = 0.0
    elif macd_hist > 0:
        macd_pos = 1.0
    elif macd_hist < 0:
        macd_pos = -1.0
    else:
        macd_pos = 0.0
    score += 0.40 * macd_pos

    # 1b. MA alignment (25%)
    ma_score = 0.0
    if ma5 is not None and ma10 is not None and ma20 is not None and ma60 is not None:
        if ma5 > ma10 > ma20 > ma60 and close > ma20:
            ma_score = 1.0       # perfect bull
        elif ma5 > ma10 and close > ma20:
            ma_score = 0.5       # partially bull
        elif ma5 < ma10 < ma20 < ma60 and close < ma20:
            ma_score = -1.0      # perfect bear
        elif ma5 < ma10 and close < ma20:
            ma_score = -0.5      # partially bear
        else:
            ma_score = 0.0       # mixed
    score += 0.25 * ma_score

    # 1c. RSI (15%)
    if rsi is None:
        rsi_score = 0.0
    elif rsi > 50:
        rsi_score = 1.0
    elif rsi < 50:
        rsi_score = -1.0
    else:
        rsi_score = 0.0
    score += 0.15 * rsi_score

    # 1d. Volume (20%)
    if volume and vol_ma5 and vol_ma5 > 0:
        if volume > vol_ma5 * 1.2:
            # Volume spike — direction depends on price vs MA5
            if ma5 and close > ma5:
                vol_score = 1.0
            elif ma5 and close < ma5:
                vol_score = -1.0
            else:
                vol_score = 0.0
        else:
            vol_score = 0.0
    else:
        vol_score = 0.0
    score += 0.20 * vol_score

    # Clamp to [-1, 1]
    score = max(-1.0, min(1.0, score))

    # Determine direction
    if score > 0.3:
        direction = 'bullish'
    elif score < -0.3:
        direction = 'bearish'
    else:
        direction = 'neutral'

    # ── Step 2: Stage (slope-based) ──
    slope_3d = macd_hist - macd_hist_prev3 if macd_hist is not None and macd_hist_prev3 is not None else 0.0

    # ATR-normalized threshold for 初 vs 主
    # 0.3 × ATR as the threshold (tunable)
    threshold = 0.3 * atr if atr else 0.0

    if direction == 'bullish':
        if slope_3d > 0 and abs(macd_hist) < threshold:
            stage = '初升'
        elif slope_3d > 0 and abs(macd_hist) >= threshold:
            stage = '主升'
        elif slope_3d <= 0:
            stage = '末升'
        else:
            stage = '主升'  # fallback
    elif direction == 'bearish':
        if slope_3d < 0 and abs(macd_hist) < threshold:
            stage = '初跌'
        elif slope_3d < 0 and abs(macd_hist) >= threshold:
            stage = '主跌'
        elif slope_3d >= 0:
            stage = '末跌'
        else:
            stage = '主跌'  # fallback
    else:
        stage = '整理'

    # ── Confidence ──
    # Count how many of the 4 dimensions agree with the direction
    signs = [macd_pos, ma_score, rsi_score, vol_score]
    if direction == 'bullish':
        agree = sum(1 for s in signs if s > 0)
    elif direction == 'bearish':
        agree = sum(1 for s in signs if s < 0)
    else:
        agree = 2  # neutral → medium by default

    if agree >= 3:
        confidence = 'high'
    elif agree == 2:
        confidence = 'medium'
    else:
        confidence = 'low'

    return {
        'direction': direction,
        'stage': stage,
        'score': round(score, 3),
        'confidence': confidence,
        'signals': {
            'close': close,
            'ma5': ma5,
            'ma10': ma10,
            'ma20': ma20,
            'ma60': ma60,
            'macd_hist': macd_hist,
            'macd_hist_prev3': macd_hist_prev3,
            'slope_3d': round(slope_3d, 4),
            'rsi14': rsi,
            'atr14': atr,
            'volume': volume,
            'vol_ma5': vol_ma5,
        }
    }
