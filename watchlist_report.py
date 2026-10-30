# -*- coding: utf-8 -*-
"""
Watchlist Report — 自訂清單 E 訊號 (standalone page)
====================================================
Reads watchlist.txt (一行一個: 代號 名稱), 對每個 code 跑「定案 E」advisor,
輸出**平鋪表格 + 日期 tab**(最近 N 個交易日, 預設 5), 欄位/顏色/摘要與
e_report_full.html 完全一致。

歷史 = Option A (replay): 用「今天的清單」對每個過去交易日重算(以該日收盤),
不新增 DB table、不改 pipeline — 直接從 daily_prices 全序列 replay
(precompute 一次 + advisor_at 讀歷史 index)。

清單來源: watchlist.txt (進 GitHub 直接編輯即可, 名稱可省略)。
非 ETF 個股要先有價格歷史(由 fetch_prices.py --codes-file 抓入 DB)。

Usage:
    python watchlist_report.py --db Ezmoney/etf_data.db --out docs/watchlist.html
    python watchlist_report.py --db ... --out ... --watchlist watchlist.txt
    python watchlist_report.py --db ... --out ... --days 10
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from e_report import (render_e_row, render_summary, summarize,
                      HEADER, E_EXTRA_CSS, MIN_BARS)
from ta_report import CSS, TAB_JS, load_prices
from position_advisor import precompute, advisor_at

HISTORY_DAYS = 5  # 日期 tab 數量(交易日)


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


# ── As-of E signal (replay past dates from the full price series) ──
def asof_signals(df, asof_dates):
    """For one code's FULL price series, compute the 定案 E signal as-of each
    target date (last bar with date <= target). Returns {date: res_or_None}.

    precompute() runs once per code (vectorized, no-lookahead); advisor_at()
    then reads any historical index in O(1). Same call shape as analyze_e
    (e_report.analyze_e just fixes the index to the last bar)."""
    if df is None or len(df) < MIN_BARS:
        return {d: None for d in asof_dates}
    pc = precompute(df)
    idx = df.index
    closes = df['close'].to_numpy()
    out = {}
    for d in asof_dates:
        i = int(idx.searchsorted(d, side='right')) - 1  # last bar with date <= d
        if i < 0:
            out[d] = None
            continue
        res = advisor_at(pc, i, 'pos', 'down')
        if res is None:
            out[d] = None
            continue
        res['close'] = float(closes[i])
        out[d] = res
    return out


# ── HTML rendering (date tabs next to the header, flat table per date) ──
DATE_TAB_CSS = """
/* 自訂清單: 產出時間 + 日期 tab 同行 */
.wl-head { display: flex; align-items: center; flex-wrap: wrap; gap: .5em .9em; margin: .3em 0 .6em; }
.wl-head .meta { margin: 0; }
.date-tabs { display: inline-flex; flex-wrap: wrap; gap: .35em; }
.date-tab { font-size: .85em; padding: .35em .8em; border-radius: 6px; margin-right: 0; }
@media (max-width: 400px) {
  .date-tab { font-size: .75em; padding: .3em .55em; }
}
"""


def render_watchlist_history(tab_dates, date_results, output_path):
    """tab_dates: ascending list of dates (Timestamps). date_results:
    {date: [(code, name, res_or_None), ...]}. Renders newest-first date tabs,
    each tab = that day's summary bar + full flat table."""
    gen_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    ordered = list(reversed(tab_dates))  # newest first → index 0 = latest = default
    h = ['<!DOCTYPE html>', '<html lang="zh-Hant"><head>', '<meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         '<title>自訂清單 訊號</title>', f'<style>{CSS}{E_EXTRA_CSS}{DATE_TAB_CSS}</style>',
         '<script async src="https://www.googletagmanager.com/gtag/js?id=G-N5G6CVZ1YJ"></script>',
         '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
         'gtag("js",new Date());gtag("config","G-N5G6CVZ1YJ");</script>',
         '</head><body>',
         '<h1>自訂清單 訊號</h1>']

    if not ordered:
        h.append(f'<p class="meta">產出 {gen_time}</p>')
        h.append('<p class="muted">(無資料 — 清單為空或個股歷史不足)</p>')
        h.append('</body></html>')
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text('\n'.join(h), encoding='utf-8')
        return

    # header: 產出時間 + date tabs (newest active by default)
    h.append('<div class="wl-head">')
    h.append(f'<span class="meta">產出 {gen_time}</span>')
    h.append('<nav class="date-tabs">')
    for i, d in enumerate(ordered):
        active = ' active' if i == 0 else ''
        h.append(f'<button class="tab-btn date-tab{active}">{d.strftime("%m-%d")}</button>')
    h.append('</nav></div>')

    # one tab-content per date (newest first); each = summary + flat table
    for i, d in enumerate(ordered):
        results = date_results[d]
        summary = summarize([(code, res) for code, _, res in results if res is not None])
        active_c = ' active' if i == 0 else ''
        h.append(f'<div class="tab-content date-content{active_c}">')
        h.append(render_summary(summary))
        h.append('<p class="ind">倉位: 長條=帶內位置、數字=目標倉位(下降趨勢→0)｜'
                 '<b style="color:#4527a0">深紫粗體=進場訊號</b>(均值回歸買點 / 壓縮突破 / 建倉)｜'
                 '歷史=以「今日清單」用該日收盤重算｜清單來源: watchlist.txt</p>')
        h.append('<div class="section"><div class="table-wrap"><table class="ta-table">')
        h.append(HEADER)
        h.append('<tbody>')
        for code, name, res in results:
            h.append(render_e_row(code, name, res))
        h.append('</tbody></table></div></div>')
        h.append('</div>')

    h += [TAB_JS, '</body></html>']
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text('\n'.join(h), encoding='utf-8')


# ── Main ─────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description='Generate E advisor report for a custom watchlist')
    p.add_argument('--db', required=True, help='Path to etf_data.db')
    p.add_argument('--out', required=True, help='Output HTML path')
    p.add_argument('--watchlist', default='watchlist.txt',
                   help='Path to watchlist file (default: watchlist.txt)')
    p.add_argument('--days', type=int, default=HISTORY_DAYS,
                   help='Number of trading-day history tabs (default: 5)')
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

    # Pass 1: load each code's full series once; collect the trading-day universe.
    per_code = []
    all_dates = set()
    for code, file_name in watchlist:
        name = resolve_name(con, code, file_name)
        df = load_prices(con, code)
        per_code.append((code, name, df))
        if df is not None and len(df) >= MIN_BARS:
            all_dates.update(df.index.tolist())
    con.close()

    tab_dates = sorted(all_dates)[-args.days:]  # most recent N trading days, ascending
    print(f'[WL] {len(tab_dates)} history dates: '
          + ', '.join(d.strftime('%Y-%m-%d') for d in tab_dates))

    # Pass 2: per-date signal for the (current) watchlist.
    date_results = {d: [] for d in tab_dates}
    for code, name, df in per_code:
        sig = asof_signals(df, tab_dates)
        for d in tab_dates:
            date_results[d].append((code, name, sig[d]))

    print(f'[WL] Rendering → {args.out}')
    render_watchlist_history(tab_dates, date_results, args.out)
    print('[DONE]')


if __name__ == '__main__':
    main()
