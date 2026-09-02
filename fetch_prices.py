"""
Fetch stock OHLCV from Yahoo Finance chart API → daily_prices table.
=====================================================================
Uses requests directly (no yfinance dependency). Handles corporate proxy
SSL issues with --insecure flag.

Usage:
    python fetch_prices.py --db Ezmoney/etf_data.db
    python fetch_prices.py --db Ezmoney/etf_data.db --codes 2330 2454
    python fetch_prices.py --db Ezmoney/etf_data.db --insecure  # corporate proxy
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

# Suppress InsecureRequestWarning when --insecure is used
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

YAHOO_CHART_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}


def to_yahoo_ticker(stock_code: str) -> str | None:
    """4-digit → .TW (TWSE), 9-digit → .TWO (OTC)."""
    if len(stock_code) == 4:
        return f"{stock_code}.TW"
    elif len(stock_code) == 9:
        return f"{stock_code}.TWO"
    return None


def init_prices_table(con: sqlite3.Connection):
    con.execute('''
        CREATE TABLE IF NOT EXISTS daily_prices (
            stock_code TEXT NOT NULL,
            date       TEXT NOT NULL,
            open       REAL,
            high       REAL,
            low        REAL,
            close      REAL,
            volume     INTEGER,
            PRIMARY KEY (stock_code, date)
        )
    ''')
    con.commit()


def fetch_stock(session: requests.Session, stock_code: str, verify: bool = True, range_: str = '4mo') -> list[tuple]:
    """
    Fetch OHLCV from Yahoo chart API.
    Returns list of (date_str, open, high, low, close, volume) tuples.
    """
    ticker = to_yahoo_ticker(stock_code)
    if not ticker:
        return []

    params = {'range': range_, 'interval': '1d'}
    try:
        r = session.get(
            YAHOO_CHART_URL.format(ticker=ticker),
            params=params,
            headers=HEADERS,
            timeout=15,
            verify=verify,
        )
        if r.status_code == 429:
            print(f'  [429] {stock_code}: rate limited, waiting 10s...')
            time.sleep(10)
            r = session.get(
                YAHOO_CHART_URL.format(ticker=ticker),
                params=params,
                headers=HEADERS,
                timeout=15,
                verify=verify,
            )
        if r.status_code != 200:
            print(f'  [HTTP {r.status_code}] {stock_code} ({ticker})')
            return []

        data = r.json()
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        quote = result['indicators']['quote'][0]

        rows = []
        for i, ts in enumerate(timestamps):
            o, h, l, c = quote['open'][i], quote['high'][i], quote['low'][i], quote['close'][i]
            v = quote['volume'][i]
            if o is None or c is None:
                continue
            d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
            rows.append((d, o, h, l, c, int(v) if v else 0))
        return rows

    except Exception as e:
        print(f'  [ERR] {stock_code} ({ticker}): {e}')
        return []


def main():
    p = argparse.ArgumentParser(description='Fetch stock prices from Yahoo Finance')
    p.add_argument('--db', required=True, help='Path to etf_data.db')
    p.add_argument('--codes', nargs='*', help='Specific stock codes (default: all in DB)')
    p.add_argument('--insecure', action='store_true', help='Disable SSL verification (corporate proxy)')
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f'[FATAL] DB not found: {db_path}', file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(str(db_path))
    init_prices_table(con)

    session = requests.Session()
    verify_ssl = not args.insecure

    if args.codes:
        codes = args.codes
    else:
        codes = [r[0] for r in con.execute(
            'SELECT DISTINCT stock_code FROM daily_holdings').fetchall()]

    print(f'Fetching {len(codes)} stocks from Yahoo Finance (4mo)...')
    ok, fail = 0, 0
    for i, code in enumerate(codes, 1):
        rows = fetch_stock(session, code, verify=verify_ssl)
        if rows:
            con.executemany(
                'INSERT OR REPLACE INTO daily_prices (stock_code, date, open, high, low, close, volume) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                [(code, d, o, h, l, c, v) for d, o, h, l, c, v in rows],
            )
            ok += 1
        else:
            fail += 1

        if i % 10 == 0:
            con.commit()
            print(f'  [{i}/{len(codes)}] ok={ok} fail={fail}')

        # Rate limit: 500ms between requests
        if i < len(codes):
            time.sleep(0.5)

    con.commit()
    con.close()
    print(f'[DONE] ok={ok} fail={fail} total={len(codes)}')


if __name__ == '__main__':
    main()
