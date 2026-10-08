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
ENTRY = '#4527a0'  # 深紫 — unified entry-signal color (均值回歸買點 / 壓縮突破 / 建倉)
ACTION_COLOR = {'加碼': '#c62828', '建倉': ENTRY, '持有': '#546e7a',
                '減碼': '#e65100', '離場': '#2e7d32'}

# 11 cols: 倉位建議 (mini-bar + target% + 🟢離場/🟣建倉)
#          MR → 均值回歸, SB → 壓縮突破, BBW分位 → BB帶寬, 量比 = vol/vol_MA20, 年分位 = 價格一年分位
HEADER = ('<thead><tr><th>代號</th><th>名稱</th><th>收盤</th><th>趨勢</th>'
          '<th>階段</th><th>倉位建議</th><th>均值回歸</th><th>壓縮突破</th><th>BB帶寬</th><th>量比</th><th>年分位</th></tr></thead>')


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
                f'<td colspan="9" class="muted">(資料不足)</td></tr>')
    stage, regime = res['stage'], res['regime']
    pos, target, action, close = res['position'], res['target'], res['action'], res['close']
    bg, tc = STAGE_COLORS.get(stage, '#fff'), STAGE_TEXT.get(stage, '#333')
    # 倉位建議: mini-bar + target% + 🟢(離場)/🟣(建倉)
    badge = ''
    if action == '建倉':
        badge = ' <b style="color:#4527a0">🟣</b>'
    elif action == '離場':
        badge = ' <b style="color:#2e7d32">🟢</b>'
    poscell = (f'<span class="poscell">'
               f'<span class="posbar"><span class="posfill" style="width:{pos*100:.0f}%"></span></span>'
               f'<b>{target:.0f}%</b>{badge}</span>')
    # 均值回歸 (買點 = 進場訊號 → 深紫; 賣點 = 過熱離場訊號 → 綠)
    if res.get('mr_gate'):
        mrc = f'<b style="color:{ENTRY}">買點</b>'
    elif res.get('mr_sell_gate'):
        mrc = f'<b style="color:#2e7d32">賣點</b>'
    elif res.get('mr_dip'):
        mrc = '觀察'
    else:
        mrc = '—'
    # 壓縮突破 (突破進場 = 進場訊號 → 深紫)
    if res.get('sb_entry'):
        sb_cell = f'<b style="color:{ENTRY}">突破進場</b>'
    elif res.get('sb_squeeze'):
        sb_cell = '擠壓中'
    else:
        sb_cell = '—'
    # BB帶寬
    b = res.get('bbw_pct')
    if b is None:
        bbw_cell = '—'
    else:
        p = b * 100
        if p < 25: tag, bc = '窄', '#1565c0'
        elif p > 75: tag, bc = '寬', '#e65100'
        else: tag, bc = '中', '#546e7a'
        bbw_cell = f'<b style="color:{bc}">{p:.0f}%</b> {tag}'
    # 量比 (RVOL = vol / vol_MA20, reference only)
    rv = res.get('rvol')
    if rv is None:
        rvol_cell = '—'
    else:
        if rv >= 2.0:
            rvol_cell = f'<b style="color:#2e7d32">{rv:.1f}x</b>'
        elif rv >= 1.0:
            rvol_cell = f'{rv:.1f}x'
        else:
            rvol_cell = f'<span style="color:#1565c0">{rv:.1f}x</span>'
    # 1Y分位 (price percentile in 252d window)
    pp = res.get('price_pct')
    if pp is None:
        pp_cell = '—'
    else:
        p = pp * 100
        if p < 25: tag, pc_ = '低', '#1565c0'
        elif p > 75: tag, pc_ = '高', '#e65100'
        else: tag, pc_ = '中', '#546e7a'
        pp_cell = f'<b style="color:{pc_}">{p:.0f}%</b> {tag}'
    return f'''<tr>
  <td class="code">{code}</td>
  <td>{name}</td>
  <td>{close:.2f}</td>
  <td>{regime}</td>
  <td><span class="stage" style="background:{bg};color:{tc}">{stage}</span></td>
  <td>{poscell}</td>
  <td>{mrc}</td>
  <td>{sb_cell}</td>
  <td>{bbw_cell}</td>
  <td>{rvol_cell}</td>
  <td>{pp_cell}</td>
</tr>'''


def render_section(title, cls, results):
    html = [f'<div class="section">', f'<h3 class="{cls}">{title}</h3>',
            '<div class="table-wrap">', '<table class="ta-table">', HEADER, '<tbody>']
    if results:
        for code, name, res in results:
            html.append(render_e_row(code, name, res))
    else:
        html.append('<tr><td colspan="11" class="muted">(無候選)</td></tr>')
    html += ['</tbody></table>', '</div>', '</div>']
    return '\n'.join(html)


# ── 今日訊號摘要 (aggregate over UNIQUE codes across all tabs) ──
def summarize(all_results):
    """all_results: list of (code, res) where res is not None. Dedupe by code
    (same code = same E signal regardless of which ETF tab it appears in)."""
    seen = {}
    for code, res in all_results:
        if code not in seen:
            seen[code] = res
    c = {'mr': 0, 'mr_sell': 0, 'sb': 0, '建倉': 0, '離場': 0}
    for res in seen.values():
        if res.get('mr_gate'):
            c['mr'] += 1
        if res.get('mr_sell_gate'):
            c['mr_sell'] += 1
        if res.get('sb_entry'):
            c['sb'] += 1
        a = res['action']
        if a in c:
            c[a] += 1
    c['total'] = len(seen)
    return c


def render_summary(c):
    """One-line actionable signal summary bar. Entry counts (均值回歸/壓縮突破) in 深紫."""
    chips = ['<span class="sig-sum-label">今日訊號</span>']
    chips.append(f'<span class="chip chip-entry">均值回歸買點 <b>{c["mr"]}</b></span>')
    chips.append(f'<span class="chip">均值回歸賣點 <b>{c["mr_sell"]}</b></span>')
    chips.append(f'<span class="chip chip-entry">壓縮突破 <b>{c["sb"]}</b></span>')
    chips.append(f'<span class="chip chip-entry">🟣 建倉 <b>{c["建倉"]}</b></span>')
    chips.append(f'<span class="chip">🟢 離場 <b>{c["離場"]}</b></span>')
    chips.append(f'<span class="chip chip-muted">共 <b>{c["total"]}</b> 檔</span>')
    return f'<div class="sig-summary">{"".join(chips)}</div>'


E_EXTRA_CSS = """
/* 倉位 mini-bar (bar=帶內位置, number=目標倉位) */
.poscell { white-space: nowrap; }
.posbar { display: inline-block; width: 44px; height: 9px; background: #e0e0e0;
          border-radius: 5px; vertical-align: middle; overflow: hidden; margin-right: .45em; }
.posfill { display: block; height: 100%; background: #546e7a; border-radius: 5px; }
/* 今日訊號摘要 */
.sig-summary { display: flex; flex-wrap: wrap; gap: .5em; align-items: center;
               margin: 1em 0; padding: .7em 1em; background: #f5f5f5;
               border: 1px solid #e0e0e0; border-radius: 8px; }
.sig-sum-label { font-weight: 700; margin-right: .3em; }
.chip { font-size: .9em; padding: .2em .7em; border-radius: 12px; background: #eceff1; white-space: nowrap; }
.chip b { font-size: 1.1em; }
.chip-entry { background: rgba(69,39,160,.12); color: #4527a0; }
.chip-entry b { color: #4527a0; }
.chip-muted { background: #f0f0f0; color: #888; }
@media (prefers-color-scheme: dark) {
  .posbar { background: #3a3a3a; }
  .sig-summary { background: #242424; border-color: #3a3a3a; }
  .chip { background: #2e2e2e; }
  .chip-entry { background: rgba(69,39,160,.25); color: #b39ddb; }
  .chip-entry b { color: #b39ddb; }
  .chip-muted { background: #2a2a2a; color: #888; }
}
@media (max-width: 768px) {
  .posbar { width: 32px; }
  .sig-summary { padding: .5em .7em; gap: .4em; }
}
"""


def render_etf_tab(key, pos_results, neg_results):
    html = [f'<div class="tab-content" id="tab-{key}">']
    html.append(render_section('▲ 斜率正候選', 'pos-title', pos_results))
    html.append(render_section('▼ 斜率負候選', 'neg-title', neg_results))
    html.append('</div>')
    return '\n'.join(html)


def render_html(tabs, summary, output_path, title='ETF 持股 訊號'):
    """tabs: list of (key, label, pos_results, neg_results)
    summary: dict from summarize() for the top 今日訊號 bar"""
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    h = ['<!DOCTYPE html>', '<html lang="zh-Hant"><head>', '<meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
          f'<title>{title}</title>', f'<style>{CSS}{E_EXTRA_CSS}</style>',
          '<script async src="https://www.googletagmanager.com/gtag/js?id=G-N5G6CVZ1YJ"></script>',
          '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
          'gtag("js",new Date());gtag("config","G-N5G6CVZ1YJ");</script>',
          '</head><body>',
         f'<h1>{title}</h1>', f'<p class="meta">{today}</p>',
         render_summary(summary),
         '<p class="ind">倉位: 長條=帶內位置、數字=目標倉位(下降趨勢→0)｜'
         '<b style="color:#4527a0">深紫粗體=進場訊號</b>(均值回歸買點 / 壓縮突破 / 建倉)</p>',
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


def collect_e_results(con):
    """Deduped (code, name, res) over ALL ETF candidates — the exact set the
    e_report page summarizes. Reused by telegram_signal.py so the push matches
    the page. (Same candidate logic as main(): get_candidates per ETF.)"""
    seen = {}
    for code in ALL_ETFS:
        history, dates = fetch_etf_history(con, code)
        if not dates:
            continue
        analysis = analyze_view(history, dates, code in ETFS_WITH_WEIGHT)
        for direction in ('pos', 'neg'):
            for sc, nm in get_candidates(analysis, direction):
                if sc not in seen:
                    seen[sc] = (sc, nm, analyze_e(con, sc))
    return list(seen.values())


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
    # 今日訊號摘要: aggregate unique codes across all tabs
    all_results = []
    for (key, label, pos_r, neg_r) in tabs:
        for code, name, res in pos_r + neg_r:
            if res is not None:
                all_results.append((code, res))
    summary = summarize(all_results)
    print(f'[E] Rendering → {args.out}')
    render_html(tabs, summary, args.out)
    print('[DONE]')


if __name__ == '__main__':
    main()
