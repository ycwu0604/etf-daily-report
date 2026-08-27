"""
Capital ETF 資料庫寫入模組

從群益投信 CFWeb API 取得每日持股資料，寫入與 Ezmoney 共用的 SQLite DB。
讓 Capital 資料也像 Ezmoney 一樣持久化，報表產生時只需讀 DB。

DB schema 與 Ezmoney/etf_processor.py 完全相容:
  - daily_holdings: (date, fund_code, stock_code, stock_name, shares, weight_pct)
  - daily_summary:  (date, fund_code, net_assets, nav, outstanding_units, ...)

API 端點: POST https://www.capitalfund.com.tw/CFWeb/api/etf/buyback
  body: {"fundId": "<id>", "date": "YYYY/MM/DD" | null}
  回傳的 date1 是「申購買回日」，date2 是「實際持股日」（date1 前一交易日）。
  寫入 DB 的日期使用 date2。

使用方式:
    from capital_db_writer import fetch_and_write_capital, ETF_CONFIG

    # 每日增量: 只抓 DB 裡沒有的日期
    fetch_and_write_capital(db, date_from='2026-08-01', date_to='2026-08-03')

    # 回填歷史: 指定月份
    fetch_and_write_capital(db, date_from='2026-07-01', date_to='2026-07-31')

依賴:
    gen_capital_report.py (call_buyback_api, parse_shares, ETF_CONFIG)
    sqlite3 (標準庫)
"""

import os
import sys
import sqlite3
from datetime import datetime, timedelta

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 動態 import ────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from gen_capital_report import call_buyback_api, parse_shares, ETF_CONFIG  # noqa: E402

# ── DB 路徑 (與 Ezmoney 共用) ──────────────────────────────
DB_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), 'Ezmoney', 'etf_data.db')


def _get_existing_dates(db: sqlite3.Connection, fund_code: str) -> set:
    """查詢 DB 中某 ETF 已有的日期集合"""
    rows = db.execute(
        "SELECT DISTINCT date FROM daily_holdings WHERE fund_code=?",
        (fund_code,),
    ).fetchall()
    return {r[0] for r in rows}


def _write_holdings(db: sqlite3.Connection, fund_code: str, date2: str, holdings: dict):
    """
    將一個交易日的持股寫入 DB。
    holdings: {stock_code: {'name': str, 'shares': int, 'weight': float}}
    使用 INSERT OR REPLACE，已有資料會更新。
    weight_pct 直接從 Capital API 的 stocks[i].weight 讀(已是百分比)。
    """
    records = []
    for code, info in holdings.items():
        name = info.get('name', '')
        shares = info.get('shares')
        weight = info.get('weight')   # Capital API 已給(單位 %)
        if shares is None:
            continue
        records.append((date2, fund_code, str(code), name, shares, weight))

    if records:
        db.executemany(
            """INSERT OR REPLACE INTO daily_holdings
               (date, fund_code, stock_code, stock_name, shares, weight_pct)
               VALUES (?, ?, ?, ?, ?, ?)""",
            records,
        )
        db.commit()


def _write_summary(db: sqlite3.Connection, fund_code: str, date2: str, pcf: dict):
    """
    將 pcf 內的 ETF 總體資訊寫入 daily_summary 表。
    pcf 欄位對應:
      nav        → net_assets       (ETF 總淨值,TWD)
      pUnit      → nav              (每單位淨值)
      totUnit    → outstanding_units(總發行單位數)
      equMkvalue → stock_amount     (持股市值,Capital API 單位似為「千」)
    cash_amount / futures_margin / repo_bonds / receivables — Capital API 沒給,留 NULL
    """
    db.execute(
        """INSERT OR REPLACE INTO daily_summary
           (date, fund_code, net_assets, nav, outstanding_units, stock_amount)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            date2,
            fund_code,
            pcf.get('nav'),
            pcf.get('pUnit'),
            pcf.get('totUnit'),
            pcf.get('equMkvalue'),
        ),
    )
    db.commit()


def _write_futures_api(db: sqlite3.Connection, fund_code: str, date2: str, api_futures: list):
    """
    將 API 回傳的期貨資料寫入 daily_futures 表。
    api_futures: list of dict from Capital API, keys: txDesc, weight, lot, txDate
    """
    records = []
    for f in api_futures:
        code = str(f.get('txEname', '')).split('2')[0].strip() if f.get('txEname') else ''
        # 從 txEname 提取代號: "TX202608" → "TX", "TE202608" → "TE"
        import re as _re
        code_match = _re.match(r'^([A-Z]+)', str(f.get('txEname', '')))
        if code_match:
            code = code_match.group(1)
        name = f.get('txDesc', '')
        weight = f.get('weight')
        lots = int(f.get('lot', 0)) if f.get('lot') is not None else 0
        contract = f.get('txDate', '')
        records.append((date2, fund_code, code, name, weight, lots, contract))

    if records:
        db.executemany(
            """INSERT OR REPLACE INTO daily_futures
               (date, fund_code, future_code, future_name, weight_pct, lots, contract_month)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            records,
        )
        db.commit()


def fetch_and_write_capital(
    db: sqlite3.Connection,
    date_from: str | None = None,
    date_to: str | None = None,
    fund_codes: list[str] | None = None,
    force: bool = False,
):
    """
    從 Capital API 取得持股資料並寫入 DB（增量模式）。

    參數:
        db:         SQLite 連線
        date_from:  開始日期 (YYYY-MM-DD)，None = 當月1號
        date_to:    結束日期 (YYYY-MM-DD)，None = 今天
        fund_codes: 要處理的 ETF 代號列表，None = ETF_CONFIG 全部
        force:      True = 即使 DB 已有該日資料也重新寫入
    """
    today = datetime.now().strftime('%Y-%m-%d')
    if date_from is None:
        date_from = datetime.now().replace(day=1).strftime('%Y-%m-%d')
    if date_to is None:
        date_to = today

    codes = fund_codes or list(ETF_CONFIG.keys())

    for etf_code in codes:
        if etf_code not in ETF_CONFIG:
            print(f'  [WARN] {etf_code} not in ETF_CONFIG, skipping')
            continue

        fund_id = ETF_CONFIG[etf_code]['fundId']
        existing = _get_existing_dates(db, etf_code) if not force else set()

        print(f'  [{etf_code}] fundId={fund_id}, fetching {date_from} ~ {date_to}')
        print(f'    DB already has {len(existing)} dates')

        # 逐日掃描，與 fetch_etf_data_api 邏輯一致
        start = datetime.strptime(date_from, '%Y-%m-%d')
        end = datetime.strptime(date_to, '%Y-%m-%d')
        # 為了涵蓋 date_to 的持股，需多查幾天
        # (7/31 持股 → date1=8/3，需查到 8/3)
        query_end = end + timedelta(days=5)

        current = start
        new_dates = []
        while current <= query_end:
            api_date = current.strftime('%Y/%m/%d')
            try:
                result = call_buyback_api(fund_id, api_date)
                data = result.get('data', {})
                stocks = data.get('stocks', []) if isinstance(data, dict) else []
                pcf = data.get('pcf', {}) if isinstance(data, dict) else {}
                date2 = pcf.get('date2', '')

                if stocks and date2:
                    # date2 是實際持股日；只收在指定範圍內的日期
                    if date_from <= date2 <= date_to:
                        if date2 not in existing or force:
                            holdings = {}
                            for s in stocks:
                                code = str(s.get('stocNo', '')).strip()
                                name = s.get('stocName', '')
                                shares = parse_shares(s.get('shareFormat', ''))
                                weight = s.get('weight')   # Capital API 直接給 % (例如 8.4905)
                                if code and shares is not None:
                                    holdings[code] = {
                                        'name': name,
                                        'shares': shares,
                                        'weight': weight,
                                    }
                            if holdings:
                                _write_holdings(db, etf_code, date2, holdings)
                                # daily_summary: ETF 總體 (NAV / 單位 / 持股市值)
                                if pcf:
                                    _write_summary(db, etf_code, date2, pcf)
                                existing.add(date2)
                                new_dates.append(date2)

                            # 期貨資料
                            api_futures = data.get('futures', []) if isinstance(data, dict) else []
                            if api_futures:
                                _write_futures_api(db, etf_code, date2, api_futures)
                        # else: 已存在，跳過
            except Exception as e:
                # 非交易日或網路問題 — 跳過
                pass

            current += timedelta(days=1)

        if new_dates:
            print(f'    ✅ Wrote {len(new_dates)} new dates: {new_dates}')
        else:
            print(f'    ⏭️  No new dates to write')

    # 報告目前 DB 狀態
    print()
    for etf_code in codes:
        if etf_code not in ETF_CONFIG:
            continue
        rows = db.execute(
            "SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM daily_holdings WHERE fund_code=?",
            (etf_code,),
        ).fetchone()
        if rows and rows[0]:
            print(f'  [DB] {etf_code}: {rows[2]} dates, {rows[0]} ~ {rows[1]}')
        else:
            print(f'  [DB] {etf_code}: no data')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Capital ETF → SQLite DB writer')
    parser.add_argument('--from', dest='date_from', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--to', dest='date_to', help='End date (YYYY-MM-DD)')
    parser.add_argument('--month', help='Month (YYYY-MM), shorthand for --from/to')
    parser.add_argument('--force', action='store_true', help='Overwrite existing data')
    args = parser.parse_args()

    date_from = args.date_from
    date_to = args.date_to

    if args.month and not date_from and not date_to:
        y, m = int(args.month[:4]), int(args.month[5:7])
        date_from = f'{args.month}-01'
        if m == 12:
            last_day = 31
        else:
            last_day = (datetime(y, m + 1, 1) - timedelta(days=1)).day
        date_to = f'{args.month}-{last_day:02d}'

    db = sqlite3.connect(DB_PATH)
    # 確保 schema 存在
    from Ezmoney.etf_processor import init_db
    init_db(db)

    fetch_and_write_capital(db, date_from, date_to, force=args.force)
    db.close()

