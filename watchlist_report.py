# -*- coding: utf-8 -*-
"""
Watchlist Report — 自訂清單 E 訊號 (standalone page)
====================================================
Reads watchlist.txt (一行一個: 代號 名稱), 對每個 code 跑「定案 E」advisor,
輸出**單一平鋪表格** — 欄位/顏色/摘要與 e_report_full.html 完全一致。

清單來源: watchlist.txt (進 GitHub 直接編輯即可, 名稱可省略)。
非 ETF 個股要先有價格歷史(由 fetch_prices.py --codes-file 抓入 DB)。

Usage:
    python watchlist_report.py --db Ezmoney/etf_data.db --out docs/watchlist.html
    python watchlist_report.py --db ... --out ... --watchlist watchlist.txt
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from e_report import (analyze_e, render_e_row, render_summary, summarize,
                      HEADER, E_EXTRA_CSS)
from ta_report import CSS


# ── Watchlist file ───────────────────────────────────────────
def read_watchlist(path: str) -> list:
    """watchlist.txt → [(code, name), ...] (name may be empty)."""
    items = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split(None, 1)
        code = parts[0]
        name = parts[1].strip() if len(parts) > 1 else ''
        items.append((code, name))
    return items


def resolve_name(con: sqlite3.Connection, code: str, file_name: str) -> str:
    """Display name: file_name → daily_holdings.stock_name → code."""
    if file_name:
        return file_name
    r = con.execute('SELECT stock_name FROM daily_holdings WHERE stock_code=? LIMIT 1',
                    (code,)).fetchone()
    if r and r[0]:
        return r[0]
    return code


# ── HTML rendering (single flat table, no tabs) ──────────────
def render_watchlist(results: list, summary: dict, output_path: str):
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    h = ['<!DOCTYPE html>', '<html lang="zh-Hant"><head>', '<meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         '<title>自訂清單 訊號</title>', f'<style>{CSS}{E_EXTRA_CSS}</style>',
         '<script async src="https://www.googletagmanager.com/gtag/js?id=G-N5G6CVZ1YJ"></script>',
         '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
         'gtag("js",new Date());gtag("config","G-N5G6CVZ1YJ");</script>',
         '</head><body>',
         '<h1>自訂清單 訊號</h1>', f'<p class="meta">{today}</p>',
         render_summary(summary),
         '<p class="ind">倉位: 長條=帶內位置、數字=目標倉位(下降趨勢→0)｜'
         '<b style="color:#4527a0">深紫粗體=進場訊號</b>(均值回歸買點 / 壓縮突破 / 建倉)｜'
         '清單來源: watchlist.txt (進 GitHub 直接編輯)</p>',
         '<div class="section">', '<div class="table-wrap">', '<table class="ta-table">',
         HEADER, '<tbody>']
    if results:
        for code, name, res in results:
            h.append(render_e_row(code, name, res))
    else:
        h.append('<tr><td colspan="11" class="muted">(清單為空)</td></tr>')
    h += ['</tbody></table>', '</div>', '</div>', '</body></html>']
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text('\n'.join(h), encoding='utf-8')


# ── Main ─────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description='Generate E advisor report for a custom watchlist')
    p.add_argument('--db', required=True, help='Path to etf_data.db')
    p.add_argument('--out', required=True, help='Output HTML path')
    p.add_argument('--watchlist', default='watchlist.txt',
                   help='Path to watchlist file (default: watchlist.txt)')
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f'[FATAL] DB not found: {db_path}', file=sys.stderr)
        sys.exit(1)
    wl_path = Path(args.watchlist)
    if not wl_path.exists():
        print(f'[FATAL] Watchlist not found: {wl_path}', file=sys.stderr)
        sys.exit(1)

    watchlist = read_watchlist(str(wl_path))
    print(f'[WL] {len(watchlist)} codes from {wl_path}')

    con = sqlite3.connect(str(db_path))
    con.execute('''CREATE TABLE IF NOT EXISTS daily_prices (
        stock_code TEXT NOT NULL, date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume INTEGER,
        PRIMARY KEY (stock_code, date))''')

    results = []
    for code, file_name in watchlist:
        name = resolve_name(con, code, file_name)
        res = analyze_e(con, code)
        if res is None:
            print(f'  [SKIP] {code} {name} — 資料不足')
        results.append((code, name, res))
    con.close()

    summary = summarize([(code, res) for code, _, res in results if res is not None])
    print(f'[WL] Rendering → {args.out}')
    render_watchlist(results, summary, args.out)
    print('[DONE]')


if __name__ == '__main__':
    main()
