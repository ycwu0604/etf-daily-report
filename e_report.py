# -*- coding: utf-8 -*-
"""
E Report — 定案 E advisor 訊號 (pos->% + down->0) for ETF holdings
===================================================================
Mirror of ta_report.py (same DB, same candidate selection, same styling),
but per-stock signal = 定案 E advisor (position_advisor), not TA classify.
Columns: 代號/名稱/收盤/趨勢/階段/位置/目標倉位/動作/建倉

Usage:
    python e_report.py --db Ezmoney/etf_data.db --out docs/e_report.html
    python e_report.py --db ... --out ... --codes 2330 2454 2308   # flat test mode (skip ETF candidate selection)
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from analyze import (ALL_ETFS, ETFS_WITH_WEIGHT, ETF_DISPLAY,
                     fetch_etf_history, analyze_view)
from ta_report import (get_candidates, load_prices, CSS, TAB_JS,
                       STAGE_COLORS, STAGE_TEXT)
from position_advisor import precompute, advisor_at

MIN_BARS = 60  # enough for ADX14 + MACD26 + BB20 to warm up
ACTION_COLOR = {'加碼': '#c62828', '建倉': '#7b1fa2', '持有': '#546e7a',
                '減碼': '#e65100', '離場': '#2e7d32'}

HEADER = ('<thead><tr><th>代號</th><th>名稱</th><th>收盤</th><th>趨勢</th>'
          '<th>階段</th><th>位置</th><th>目標倉位</th><th>動作</th><th>建倉</th></tr></thead>')


# ── E signal for one stock ──────────────────────────────────
def analyze_e(con, stock_code):
    """Run 定案 E on one stock. Returns signal dict (+close) or None."""
    df = load_prices(con, stock_code)
    if df is None or len(df) < MIN_BARS:
        return None
    pc = precompute(df)
    res = advisor_at(pc, len(df) - 1, 'pos', 'down')
    res['close'] = float(df['close'].iloc[-1])
    return res


# ── HTML rendering (mirrors ta_report style) ─────────────────
def render_e_row(code, name, res):
    if res is None:
        return (f'<tr><td class="code">{code}</td><td>{name}</td>'
                f'<td colspan="7" class="muted">(資料不足)</td></tr>')
    stage, regime = res['stage'], res['regime']
    pos, target, action, jin, close = res['position'], res['target'], res['action'], res['jin_cang'], res['close']
    flags = ' | '.join(res['flags']) if res.get('flags') else ''
    bg, tc = STAGE_COLORS.get(stage, '#fff'), STAGE_TEXT.get(stage, '#333')
    ac = ACTION_COLOR.get(action, '#555')
    jin_cell = '<b style="color:#7b1fa2">Y</b>' if jin else 'N'
    note = f'<span class="sig-note">{flags}</span>' if flags else ''
    return f'''<tr>
  <td class="code">{code}</td>
  <td>{name}</td>
  <td>{close:.2f}</td>
  <td>{regime}</td>
  <td><span class="stage" style="background:{bg};color:{tc}">{stage}</span></td>
  <td>{pos*100:.0f}%</td>
  <td><b>{target:.0f}%</b></td>
  <td><span style="color:{ac};font-weight:700">{action}</span>{note}</td>
  <td>{jin_cell}</td>
</tr>'''


def render_section(title, cls, results):
    html = [f'<div class="section">', f'<h3 class="{cls}">{title}</h3>',
            '<div class="table-wrap">', '<table class="ta-table">', HEADER, '<tbody>']
    if results:
        for code, name, res in results:
            html.append(render_e_row(code, name, res))
    else:
        html.append('<tr><td colspan="9" class="muted">(無候選)</td></tr>')
    html += ['</tbody></table>', '</div>', '</div>']
    return '\n'.join(html)


def render_etf_tab(key, pos_results, neg_results):
    html = [f'<div class="tab-content" id="tab-{key}">']
    html.append(render_section('▲ 斜率正候選', 'pos-title', pos_results))
    html.append(render_section('▼ 斜率負候選', 'neg-title', neg_results))
    html.append('</div>')
    return '\n'.join(html)


def render_html(tabs, output_path):
    """tabs: list of (key, label, pos_results, neg_results)"""
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    h = ['<!DOCTYPE html>', '<html lang="zh-Hant"><head>', '<meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         '<title>ETF 持股 E 訊號</title>', f'<style>{CSS}</style>', '</head><body>',
         '<h1>ETF 持股 E 訊號</h1>', f'<p class="meta">{today}</p>',
         '<p class="ind">目標倉位=帶內位置×100 (下降趨勢→0)｜建倉=獨立訊號 (ADX↑ + 初升/主升 + 位置&lt;70% + MACD↑)</p>',
         '<div class="tabs">']
    for i, (key, label, _, _) in enumerate(tabs):
        active = ' active' if i == 0 else ''
        h.append(f'  <button class="tab-btn{active}" data-tab="tab-{key}">{label}</button>')
    h.append('</div>')
    for (key, label, pos_r, neg_r) in tabs:
        h.append(render_etf_tab(key, pos_r, neg_r))
    h += [TAB_JS, '</body></html>']
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text('\n'.join(h), encoding='utf-8')


# ── Main ─────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description='Generate E advisor report for ETF holdings')
    p.add_argument('--db', required=True, help='Path to etf_data.db')
    p.add_argument('--out', required=True, help='Output HTML path')
    p.add_argument('--codes', nargs='*', help='Override: flat list of stock codes (test mode)')
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f'[FATAL] DB not found: {db_path}', file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(str(db_path))
    con.execute('''CREATE TABLE IF NOT EXISTS daily_prices (
        stock_code TEXT NOT NULL, date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume INTEGER,
        PRIMARY KEY (stock_code, date))''')

    tabs = []
    if args.codes:
        # flat test mode: single "所有" tab
        results = []
        for code in args.codes:
            nm = con.execute('SELECT stock_name FROM daily_holdings WHERE stock_code=? LIMIT 1',
                             (code,)).fetchone()
            results.append((code, nm[0] if nm else code, analyze_e(con, code)))
        tabs.append(('ALL', '所有', results, []))
    else:
        for code in ALL_ETFS:
            print(f'[E] {code} ({ETF_DISPLAY.get(code, code)})...')
            pos_cands = neg_cands = []
            history, dates = fetch_etf_history(con, code)
            if dates:
                analysis = analyze_view(history, dates, code in ETFS_WITH_WEIGHT)
                pos_cands = get_candidates(analysis, 'pos')
                neg_cands = get_candidates(analysis, 'neg')
            print(f'  candidates: {len(pos_cands)} pos, {len(neg_cands)} neg')
            pos_r = [(sc, nm, analyze_e(con, sc)) for sc, nm in pos_cands]
            neg_r = [(sc, nm, analyze_e(con, sc)) for sc, nm in neg_cands]
            tabs.append((code, ETF_DISPLAY.get(code, code), pos_r, neg_r))

    con.close()
    print(f'[E] Rendering → {args.out}')
    render_html(tabs, args.out)
    print('[DONE]')


if __name__ == '__main__':
    main()
