# -*- coding: utf-8 -*-
"""
Telegram 今日訊號 — 把 e_report(持股) + watchlist(自訂清單) 的今日訊號發到 Telegram
==============================================================================
- 持股: 用 e_report.collect_e_results(跟網頁同一套候選集, 確保一致)
- 自訂清單: 讀 watchlist.txt → analyze_e
- 只在台北 20:00 的 analyze 跑(analyze.yml 的 step 用時區判斷)
- 只發 TELEGRAM_CHAT_ID(analyze.yml step env 只設 CHAT_ID, 不设 _2)

Usage:
    python telegram_signal.py --db Ezmoney/etf_data.db --watchlist watchlist.txt
    python telegram_signal.py --db ... --watchlist ... --dry-run   # 只印, 不發
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from e_report import analyze_e, collect_e_results
from watchlist_report import read_watchlist, resolve_name
from notify_telegram import _send_message

# 進場訊號列「目標倉位」; 退場訊號只列代號
ENTRY_WITH_TARGET = ('mr', '建倉')


def group_signals(results):
    """results: list[(code, name, res)]. → (groups, counts, total).
    groups: {'mr':[], 'sb':[], '建倉':[], '減碼':[], '離場':[]} (strings '代號 名稱 [目標%]')
    進場訊號(均值回歸/壓縮突破)獨立列; 建倉/減碼/離場 用 action(與網頁 summarize 一致,
    避免 jin_cang + action 雙重計數); 加碼/持有 只計數。
    counts: {'加碼':n, '持有':n}; total = analyzed 檔數."""
    groups = {'mr': [], 'sb': [], '建倉': [], '減碼': [], '離場': []}
    counts = {'加碼': 0, '持有': 0}
    total = 0
    for code, name, res in results:
        if res is None:
            continue
        total += 1
        tag = f'{code} {name}'.strip()
        tgt = f" 目標{res['target']:.0f}%" if res['target'] > 0 else ''
        if res.get('mr_gate'):
            groups['mr'].append(tag + tgt)
        if res.get('sb_entry'):
            groups['sb'].append(tag)
        a = res['action']
        if a in counts:
            counts[a] += 1
        elif a in groups:
            groups[a].append(tag + (tgt if a == '建倉' else ''))
    return groups, counts, total


def build_section(emoji, title, results):
    groups, counts, total = group_signals(results)
    lines = [f'{emoji} {title}({total} 檔)']
    for key, label in (('mr', '🟣 均值回歸買點'), ('sb', '🟣 壓縮突破'),
                       ('建倉', '🟣 建倉'), ('減碼', '🔴 減碼'), ('離場', '🟢 離場')):
        if groups[key]:
            lines.append(f'{label} · {len(groups[key])}\n  ' + ' / '.join(groups[key]))
    lines.append(f'(加碼 {counts["加碼"]} · 持有 {counts["持有"]})')
    return '\n'.join(lines)


def build_message(db_path: str, watchlist_path: str) -> str:
    con = sqlite3.connect(db_path)
    hold_results = collect_e_results(con)
    wl_results = []
    if Path(watchlist_path).exists():
        for code, fname in read_watchlist(watchlist_path):
            wl_results.append((code, resolve_name(con, code, fname), analyze_e(con, code)))
    con.close()

    today = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    return (f'📊 ETF 今日訊號 {today}\n'
            f'━━━━━━━━━━━━\n'
            f'{build_section("🏦", "持股", hold_results)}\n'
            f'━━━━━━━━━━━━\n'
            f'{build_section("📌", "自訂清單", wl_results)}')


def main():
    p = argparse.ArgumentParser(description='Send today\'s E signals to Telegram')
    p.add_argument('--db', required=True, help='Path to etf_data.db')
    p.add_argument('--watchlist', default='watchlist.txt', help='Path to watchlist file')
    p.add_argument('--dry-run', action='store_true', help='Print message, do not send')
    args = p.parse_args()

    if not Path(args.db).exists():
        print(f'[FATAL] DB not found: {args.db}', file=sys.stderr)
        sys.exit(1)

    msg = build_message(args.db, args.watchlist)
    if args.dry_run:
        print('=== DRY RUN (not sent) ===')
        print(msg)
        return

    ok = _send_message(msg)
    if ok:
        print('[TG] 今日訊號 sent')
    else:
        # 通知失敗不阻擋 workflow(報告已部署), 但要清楚印出
        print('[TG] FAILED — check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID', file=sys.stderr)


if __name__ == '__main__':
    main()
