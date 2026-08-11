"""
ETF 資料庫讀取模組（Ezmoney + Capital 共用）

從 SQLite 資料庫讀取每日持股資料，供外部腳本 import 使用。
現在 DB 裡同時包含 Ezmoney (49YTW, 63YTW) 和 Capital (00982A, 00992A) 的資料。

使用方式:
    from ezmoney_db_reader import fetch_ezmoney_data, fetch_capital_data, fetch_all_etf_data
    
    # 只讀 Ezmoney
    data = fetch_ezmoney_data()                   # 當月
    data = fetch_ezmoney_data('2026-07-01', '2026-07-31')  # 指定範圍
    
    # 只讀 Capital
    data = fetch_capital_data('2026-07-01', '2026-07-31')
    
    # 讀全部 4 個 ETF
    data = fetch_all_etf_data('2026-07-01', '2026-07-31')
    
    # 回傳格式: {fund_code: {date_str: {stock_code: {name: str, shares: int}}}}
    # 例如: {'49YTW': {'2026-07-08': {'2303': {'name': '聯電', 'shares': 86550000}}}}

依賴:
    sqlite3 (標準庫)
"""

import os
import sys
import sqlite3

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 路徑設定 ──────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'etf_data.db')

# ── ETF 代號 ──────────────────────────────────────────────
EZMONEY_ETF_CODES = ['49YTW', '63YTW']
CAPITAL_ETF_CODES = ['00982A', '00992A']
ALL_ETF_CODES = EZMONEY_ETF_CODES + CAPITAL_ETF_CODES

# 向後相容 alias
ETF_CODES = EZMONEY_ETF_CODES


def fetch_db_data(fund_codes, date_from=None, date_to=None, db_path=None, label='DB'):
    """
    從 SQLite DB 讀取指定期間的持股資料。

    參數:
        fund_codes: ETF 代號列表，例如 ['49YTW', '63YTW'] 或 ['00982A', '00992A']
        date_from:  開始日期 (YYYY-MM-DD)，None = 不限
        date_to:    結束日期 (YYYY-MM-DD)，None = 不限
        db_path:    自訂 DB 路徑，None = 使用預設
        label:      log 顯示的標籤

    回傳:
        dict，{fund_code: {date_str: {stock_code: {name: str, shares: int}}}}
    """
    _db_path = db_path or DB_PATH

    if not os.path.exists(_db_path):
        print(f'  [{label}] WARN: DB not found at {_db_path}')
        return {code: {} for code in fund_codes}

    result = {code: {} for code in fund_codes}

    conn = sqlite3.connect(_db_path)
    cur = conn.cursor()

    # 日期條件
    where = ['fund_code IN ({})'.format(','.join(['?'] * len(fund_codes)))]
    params = list(fund_codes)
    if date_from:
        where.append('date >= ?')
        params.append(date_from)
    if date_to:
        where.append('date <= ?')
        params.append(date_to)
    where_sql = ' WHERE ' + ' AND '.join(where)

    rows = cur.execute(
        f'SELECT date, fund_code, stock_code, stock_name, shares '
        f'FROM daily_holdings{where_sql} ORDER BY date',
        params,
    ).fetchall()
    conn.close()

    for date_str, fund_code, stock_code, stock_name, shares in rows:
        if fund_code not in result:
            continue
        day = result[fund_code].setdefault(date_str, {})
        day[str(stock_code).strip()] = {
            'name': stock_name,
            'shares': int(shares) if shares is not None else None,
        }

    for fc in fund_codes:
        dates = sorted(result[fc].keys())
        if dates:
            print(f'  [{label}] {fc}: {len(dates)} dates, {dates[0]} ~ {dates[-1]}')
        else:
            print(f'  [{label}] {fc}: no data')

    return result


def fetch_ezmoney_data(date_from=None, date_to=None, db_path=None):
    """讀取 Ezmoney ETF (49YTW, 63YTW) 資料 — 向後相容"""
    return fetch_db_data(EZMONEY_ETF_CODES, date_from, date_to, db_path, label='Ezmoney DB')


def fetch_capital_data(date_from=None, date_to=None, db_path=None):
    """讀取 Capital ETF (00982A, 00992A) 資料 — 從 DB 讀取"""
    return fetch_db_data(CAPITAL_ETF_CODES, date_from, date_to, db_path, label='Capital DB')


def fetch_all_etf_data(date_from=None, date_to=None, db_path=None):
    """讀取全部 4 個 ETF 資料"""
    return fetch_db_data(ALL_ETF_CODES, date_from, date_to, db_path, label='ETF DB')


def fetch_futures_data(fund_codes=None, date_from=None, date_to=None, db_path=None):
    """
    讀取期貨持倉資料。

    參數:
        fund_codes: ETF 代號列表，None = 全部
        date_from:  開始日期 (YYYY-MM-DD)，None = 不限
        date_to:    結束日期 (YYYY-MM-DD)，None = 不限

    回傳:
        dict，{fund_code: {date_str: [{'code': str, 'name': str, 'weight_pct': float, 'lots': int, 'contract_month': str}]}}
    """
    _db_path = db_path or DB_PATH
    _codes = fund_codes or ALL_ETF_CODES

    if not os.path.exists(_db_path):
        return {code: {} for code in _codes}

    result = {code: {} for code in _codes}

    conn = sqlite3.connect(_db_path)
    cur = conn.cursor()

    where = ['fund_code IN ({})'.format(','.join(['?'] * len(_codes)))]
    params = list(_codes)
    if date_from:
        where.append('date >= ?')
        params.append(date_from)
    if date_to:
        where.append('date <= ?')
        params.append(date_to)
    where_sql = ' WHERE ' + ' AND '.join(where)

    rows = cur.execute(
        f'SELECT date, fund_code, future_code, future_name, weight_pct, lots, contract_month '
        f'FROM daily_futures{where_sql} ORDER BY date',
        params,
    ).fetchall()
    conn.close()

    for date_str, fund_code, fut_code, fut_name, weight, lots, contract in rows:
        if fund_code not in result:
            continue
        day_list = result[fund_code].setdefault(date_str, [])
        day_list.append({
            'code': fut_code,
            'name': fut_name,
            'weight_pct': weight,
            'lots': lots,
            'contract_month': contract,
        })

    for fc in _codes:
        dates = sorted(result[fc].keys())
        if dates:
            total_contracts = sum(len(result[fc][d]) for d in dates)
            print(f'  [Futures] {fc}: {len(dates)} dates, {total_contracts} contract records')

    return result


if __name__ == '__main__':
    # 測試用
    data = fetch_all_etf_data()
    for fc, dates_data in data.items():
        dates = sorted(dates_data.keys())
        print(f'{fc}: {len(dates)} dates')
        if dates:
            first = dates[0]
            stocks = dates_data[first]
            for code, info in sorted(stocks.items(), key=lambda x: -x[1]['shares'])[:3]:
                print(f'  {first} top: {code} {info["name"]} {info["shares"]:,}')
