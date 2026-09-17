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
    con.execute('''
        CREATE TABLE IF NOT EXISTS stock_fundamentals (
            stock_code   TEXT NOT NULL,
            date         TEXT NOT NULL,
            pe_ratio     REAL,
            high_52w     REAL,
            low_52w      REAL,
            market_cap   INTEGER,
            PRIMARY KEY (stock_code, date)
        )
    ''')
    con.commit()


def fetch_stock(session: requests.Session, stock_code: str, verify: bool = True, range_: str = '1y') -> list[tuple]:
    """
    Fetch OHLCV from Yahoo chart API.
    Tries .TW first, falls back to .TWO for 4-digit codes (OTC stocks).
    Returns list of (date_str, open, high, low, close, volume) tuples.
    """
    # Build candidate tickers: 4-digit → try .TW then .TWO; 9-digit → .TWO
    if len(stock_code) == 4:
        tickers = [f"{stock_code}.TW", f"{stock_code}.TWO"]
    elif len(stock_code) == 9:
        tickers = [f"{stock_code}.TWO"]
    else:
        return []

    for ticker in tickers:
        rows = _fetch_ticker(session, stock_code, ticker, verify, range_)
        if rows:
            return rows
    return []


def _fetch_ticker(session: requests.Session, stock_code: str, ticker: str, verify: bool, range_: str) -> list[tuple]:
    """Fetch single ticker. Returns [] on failure."""
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
            print(f'  [429] {stock_code} ({ticker}): rate limited, waiting 10s...')
            time.sleep(10)
            r = session.get(
                YAHOO_CHART_URL.format(ticker=ticker),
                params=params,
                headers=HEADERS,
                timeout=15,
                verify=verify,
            )
        if r.status_code != 200:
            return []

        data = r.json()
        if 'chart' not in data or 'result' not in data['chart'] or not data['chart']['result']:
            return []

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


def get_yahoo_crumb(session: requests.Session, verify: bool = True) -> str | None:
    """Get Yahoo Finance crumb token (required for quoteSummary API)."""
    try:
        # Step 1: Get cookie
        session.get('https://fc.yahoo.com', timeout=10, verify=verify, allow_redirects=False)
        # Step 2: Get crumb
        r = session.get('https://query1.finance.yahoo.com/v1/test/getcrumb', timeout=10, verify=verify)
        if r.status_code == 200:
            crumb = r.text.strip()
            print(f'  [Crumb] OK: {crumb[:10]}...')
            return crumb
    except Exception as e:
        print(f'  [Crumb] Failed: {e}')
    return None


def fetch_fundamentals(session: requests.Session, stock_code: str, ticker: str, crumb: str | None = None, verify: bool = True) -> dict:
    """Fetch P/E, 52-week high/low, market cap from Yahoo quoteSummary."""
    url = f'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}'
    params = {'modules': 'price,summaryDetail,defaultKeyStatistics'}
    if crumb:
        params['crumb'] = crumb
    try:
        r = session.get(url, params=params, headers=HEADERS, timeout=10, verify=verify)
        if r.status_code != 200:
            return {}
        data = r.json()
        if 'quoteSummary' not in data or not data['quoteSummary'].get('result'):
            return {}
        result = data['quoteSummary']['result'][0]
        price = result.get('price', {})
        detail = result.get('summaryDetail', {})
        stats = result.get('defaultKeyStatistics', {})

        pe = pe_t = detail.get('trailingPE', {})
        pe_val = pe_t.get('raw') if isinstance(pe_t, dict) else pe_t

        mc = price.get('marketCap', {})
        mc_val = mc.get('raw') if isinstance(mc, dict) else mc

        # 52-week from price module
        h52 = price.get('fiftyTwoWeekHigh', {})
        l52 = price.get('fiftyTwoWeekLow', {})
        h52_val = h52.get('raw') if isinstance(h52, dict) else h52
        l52_val = l52.get('raw') if isinstance(l52, dict) else l52

        return {
            'pe_ratio': pe_val,
            'high_52w': h52_val,
            'low_52w': l52_val,
            'market_cap': mc_val,
        }
    except Exception:
        return {}


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

    # Get crumb for quoteSummary API
    crumb = get_yahoo_crumb(session, verify=verify_ssl)

    if args.codes:
        codes = args.codes
    else:
        codes = [r[0] for r in con.execute(
            'SELECT DISTINCT stock_code FROM daily_holdings').fetchall()]

    print(f'Fetching {len(codes)} stocks from Yahoo Finance (1y + fundamentals)...')
    ok, fail = 0, 0
    pe_ok = 0
    today = datetime.now().strftime('%Y-%m-%d')
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

        # Fetch fundamentals (P/E, market cap) + calc 52w high/low from price data
        ticker = f"{code}.TW" if len(code) == 4 else f"{code}.TWO"
        fund = fetch_fundamentals(session, code, ticker, crumb=crumb, verify=verify_ssl)
        if fund and fund.get('pe_ratio') is not None:
            pe_ok += 1
            if pe_ok <= 3:
                print(f'  [P/E] {code} ({ticker}): {fund["pe_ratio"]:.2f}')

        # Calculate 52-week high/low from fetched prices
        h52 = max(r[2] for r in rows) if rows else None  # max of highs
        l52 = min(r[3] for r in rows) if rows else None  # min of lows

        if fund and any(fund.get(k) is not None for k in ('pe_ratio', 'market_cap')):
            con.execute(
                'INSERT OR REPLACE INTO stock_fundamentals (stock_code, date, pe_ratio, high_52w, low_52w, market_cap) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (code, today, fund.get('pe_ratio'), h52, l52, fund.get('market_cap')),
            )
        elif h52 and l52:
            con.execute(
                'INSERT OR REPLACE INTO stock_fundamentals (stock_code, date, pe_ratio, high_52w, low_52w, market_cap) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (code, today, None, h52, l52, None),
            )

        if i % 10 == 0:
            con.commit()
            print(f'  [{i}/{len(codes)}] ok={ok} fail={fail}')

        # Rate limit: 500ms between requests
        if i < len(codes):
            time.sleep(0.5)

    con.commit()
    con.close()
    print(f'[DONE] prices: ok={ok} fail={fail} | P/E: {pe_ok}/{len(codes)} | total={len(codes)}')


if __name__ == '__main__':
    main()
