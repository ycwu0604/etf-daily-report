"""
ETF Rotation Signal (Intraday / Post-Market)
=============================================
Runs the rotation strategy on both pairs, compares with DB,
sends Telegram notification if allocation change >= threshold.

Usage:
    python rotation_signal.py --db Ezmoney/etf_data.db

Environment variables (for Telegram):
    TELEGRAM_BOT_TOKEN : Bot API token
    TELEGRAM_CHAT_ID   : Chat ID to send to
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from rotation_strategy import (
    MA_SHORT, MA_LONG, MOM_WINDOW, MOM_THRESHOLD, MIN_DATA, PAIRS,
    EXCHANGE_TZ, determine_allocation,
    classify_session, is_weekend,
    init_rotation_table, get_latest_allocation, record_allocation,
)
from notify_telegram import send_rotation_alert

# ── Config ─────────────────────────────────────────────────────────────
ALERT_THRESHOLD = 10  # notify if allocation changes by >= this %

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
VERIFY_SSL = True  # Set False with --insecure (local corporate proxy)


# ── Price Fetching ─────────────────────────────────────────────────────
def fetch_data(ticker: str) -> dict | None:
    """Fetch 1y daily data.
    Returns {'dates', 'closes', 'last_date', 'intraday'} or None.
    `dates` are exchange-local dates; `intraday` is True when the last bar is
    today's still-open session (i.e. not yet a final close)."""
    url = YAHOO_CHART_URL.format(ticker=ticker)
    params = {"range": "1y", "interval": "1d"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15, verify=VERIFY_SSL)
        if r.status_code == 429:
            time.sleep(5)
            r = requests.get(url, params=params, headers=HEADERS, timeout=15, verify=VERIFY_SSL)
        if r.status_code != 200:
            return None
        data = r.json()
        if "chart" not in data or not data["chart"].get("result"):
            return None
        result = data["chart"]["result"][0]
        ts = result["timestamp"]
        raw_closes = result["indicators"]["quote"][0]["close"]
        pairs = [(t, c) for t, c in zip(ts, raw_closes) if c is not None]
        if len(pairs) < MIN_DATA:
            return None
        # Use the exchange's timezone so bar dates land on the real trading day.
        tzname = result.get("meta", {}).get("exchangeTimezoneName") or EXCHANGE_TZ
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tzname)
        except Exception:
            tz = timezone(timedelta(hours=8))
        dates = [datetime.fromtimestamp(t, tz=tz).strftime('%Y-%m-%d') for t, c in pairs]
        closes = [c for t, c in pairs]
        last_date = dates[-1]
        intraday = classify_session(last_date, tzname=tzname) == "intraday"
        return {"dates": dates, "closes": closes, "last_date": last_date, "intraday": intraday}
    except Exception as e:
        print(f"  [ERR] {ticker}: {e}")
        return None


def fetch_with_fallback(code: str) -> dict | None:
    for suffix in (".TW", ".TWO", ""):
        ticker = f"{code}{suffix}"
        data = fetch_data(ticker)
        if data:
            return data
    return None


# ── Main ───────────────────────────────────────────────────────────────
def main():
    global VERIFY_SSL
    p = argparse.ArgumentParser(description='ETF Rotation Signal + Notification')
    p.add_argument('--db', required=True, help='Path to etf_data.db')
    p.add_argument('--insecure', action='store_true', help='Skip SSL verification (local corporate proxy)')
    args = p.parse_args()
    if args.insecure:
        VERIFY_SSL = False
        requests.packages.urllib3.disable_warnings()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f'[FATAL] DB not found: {db_path}', file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(str(db_path))
    init_rotation_table(con)

    # Fetch prices for all unique ETFs
    all_codes = set()
    for pair in PAIRS:
        all_codes.add(pair["equity_code"])
        all_codes.add(pair["bond_code"])

    print(f'[Signal] Fetching {len(all_codes)} ETFs: {sorted(all_codes)}')
    price_cache = {}
    for code in sorted(all_codes):
        price_cache[code] = fetch_with_fallback(code)
        if price_cache[code]:
            print(f'  {code}: {len(price_cache[code]["closes"])} pts, last=${price_cache[code]["closes"][-1]:.2f} ({price_cache[code]["dates"][-1]})')
        else:
            print(f'  {code}: FAILED')
        time.sleep(0.5)

    # Process each pair
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    alerts = []

    for pair in PAIRS:
        eq_data = price_cache.get(pair["equity_code"])
        bd_data = price_cache.get(pair["bond_code"])

        if not eq_data:
            print(f'  [WARN] Skip {pair["id"]}: no equity data')
            continue

        eq_closes = eq_data["closes"]
        eq_dates = eq_data["dates"]
        trading_date = eq_data["last_date"]   # exchange-local date of the last equity bar
        intraday = eq_data["intraday"]        # True => last bar is today's live session
        result = determine_allocation(eq_closes, dates=eq_dates)
        previous = get_latest_allocation(con, pair["id"])

        prev_pct = previous["equity_pct"] if previous else 50
        new_pct = result["equity_pct"]
        delta = new_pct - prev_pct

        # Determine action
        if abs(delta) >= ALERT_THRESHOLD:
            if delta > 0:
                action = f'BUY_EQ_{delta}%'
            else:
                action = f'SELL_EQ_{abs(delta)}%'
            alerts.append({
                "pair_id": pair["id"],
                "label": pair["label"],
                "equity_code": pair["equity_code"],
                "equity_name": pair["equity_name"],
                "bond_code": pair["bond_code"],
                "bond_name": pair["bond_name"],
                "prev_pct": prev_pct,
                "new_pct": new_pct,
                "delta": delta,
                "price": result["price"],
                "regime": result["regime"],
                "momentum": result["momentum"],
                "reason": result["reason"],
                "time": now_str,
            })
            print(f'  [{pair["id"]}] ALERT: {prev_pct}% → {new_pct}% ({delta:+d}%)')
        else:
            action = 'HOLD'
            print(f'  [{pair["id"]}] HOLD: {prev_pct}% → {new_pct}% (diff={delta:+d}%)')

        # Record in DB only on a final, valid trading day (avoids intraday/weekend
        # ghost rows). Intraday runs still evaluate + alert; they just don't persist.
        persist_ok = (not intraday) and (not is_weekend(trading_date)) and (result["price"] or 0) > 0
        if persist_ok:
            record_allocation(con, trading_date, pair["id"], result, action)
        else:
            print(f'  [{pair["id"]}] skip persist (intraday={intraday}, '
                  f'weekend={is_weekend(trading_date)})')

    con.close()

    # Send Telegram notification if any alerts
    if alerts:
        print(f'\n[Signal] {len(alerts)} alert(s) to send via Telegram')
        ok = send_rotation_alert(alerts)
        if ok:
            print('[Signal] Telegram sent ✓')
        else:
            print('[Signal] Telegram FAILED (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)')
    else:
        print('\n[Signal] No alerts (all within threshold)')

    # Write signal JSON (for debugging / future use)
    signal = {
        'time': now_str,
        'threshold': ALERT_THRESHOLD,
        'alerts': alerts,
    }
    json_path = Path('rotation_signal_latest.json')
    json_path.write_text(json.dumps(signal, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[Signal] JSON → {json_path}')
    print('[DONE]')


if __name__ == '__main__':
    main()
