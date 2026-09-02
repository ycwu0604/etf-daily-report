"""
TA Report — Technical Analysis for ETF Holdings
================================================
Reads holdings + prices from SQLite, calculates indicators, classifies
trend stage for candidate stocks, renders tabbed HTML.

Candidate selection per ETF:
  - Positive: union of pos_sh & pos_wt top 5 across windows (1,3,5,10)
  - Negative: union of neg_sh & neg_wt top 5 across windows (1,3,5,10)
  - Deduplicated by stock_code

Usage:
    python ta_report.py --db Ezmoney/etf_data.db --out docs/ta_report.html
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from analyze import (
    ALL_ETFS, ETFS_WITH_WEIGHT, ETF_DISPLAY, WINDOWS, TOP_N,
    fetch_etf_history, analyze_view,
)
from ta_indicators import calc_indicators
from ta_classify import classify


# ── Candidate Selection ─────────────────────────────────────
def get_candidates(analysis: dict, key_prefix: str) -> list[tuple[str, str]]:
    """
    From analyze_view() results, get unique (code, name) pairs
    that appear in any window's top N for the given key prefix.

    key_prefix: 'pos' → pos_sh + pos_wt; 'neg' → neg_sh + neg_wt
    Returns list of (stock_code, stock_name) deduplicated.
    """
    seen = {}
    for w in WINDOWS:
        w_data = analysis.get(w, {})
        for key in (f'{key_prefix}_sh', f'{key_prefix}_wt'):
            for entry in w_data.get(key, []):
                code = entry[0]
                name = entry[1]
                if code not in seen or (name and not seen[code]):
                    seen[code] = name
    return list(seen.items())


# ── Price Data ──────────────────────────────────────────────
def load_prices(con: sqlite3.Connection, stock_code: str) -> pd.DataFrame | None:
    """Load price history for one stock. Returns DataFrame or None."""
    rows = con.execute(
        'SELECT date, open, high, low, close, volume FROM daily_prices '
        'WHERE stock_code = ? ORDER BY date ASC', (stock_code,)
    ).fetchall()
    if len(rows) < 30:  # need minimum 30 days for MA20 + buffer
        return None
    df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    return df


# ── Analyze One Stock ───────────────────────────────────────
def analyze_stock(con: sqlite3.Connection, stock_code: str) -> dict | None:
    """Fetch prices, calculate indicators, classify. Returns result or None."""
    df = load_prices(con, stock_code)
    if df is None:
        return None

    ind = calc_indicators(df)
    if ind['macd_hist'].isna().iloc[-1]:
        return None

    latest = ind.iloc[-1].to_dict()
    prev3 = ind.iloc[-4].to_dict() if len(ind) >= 4 else latest

    return classify(latest, prev3)


# ── HTML Rendering ──────────────────────────────────────────
STAGE_COLORS = {
    '初升': '#e8f5e9', '主升': '#c8e6c9', '末升': '#fff9c4',
    '初跌': '#ffebee', '主跌': '#ffcdd2', '末跌': '#f3e5f5',
    '整理': '#eceff1',
}
STAGE_TEXT = {
    '初升': '#2e7d32', '主升': '#1b5e20', '末升': '#f57f17',
    '初跌': '#c62828', '主跌': '#b71c1c', '末跌': '#6a1b9a',
    '整理': '#546e7a',
}
DIR_LABEL = {'bullish': '多頭', 'bearish': '空頭', 'neutral': '整理'}
DIR_COLOR = {'bullish': '#c62828', 'bearish': '#1565c0', 'neutral': '#757575'}


def fmt_val(v, fmt='{:+.2f}'):
    if v is None or (isinstance(v, float) and v != v):
        return '<span class="muted">—</span>'
    return fmt.format(v)


def render_stock_row(code: str, name: str, result: dict) -> str:
    """Render one stock as a table row."""
    if result is None:
        return (f'<tr><td class="code">{code}</td><td>{name}</td>'
                f'<td colspan="5" class="muted">(資料不足)</td></tr>')

    stage = result['stage']
    direction = result['direction']
    score = result['score']
    sig = result['signals']
    bg = STAGE_COLORS.get(stage, '#fff')
    tc = STAGE_TEXT.get(stage, '#333')
    dc = DIR_COLOR.get(direction, '#555')

    return f'''<tr>
  <td class="code">{code}</td>
  <td>{name}</td>
  <td><span class="stage" style="background:{bg};color:{tc}">{stage}</span></td>
  <td style="color:{dc}">{DIR_LABEL[direction]}</td>
  <td>{score:+.2f}</td>
  <td class="ind">RSI {sig['rsi14']:.0f} · MACD {fmt_val(sig['macd_hist'])} · MA20 {fmt_val(sig['ma20'], '{{:.2f}}')}</td>
</tr>'''


def render_etf_tab(etf_code: str, pos_results: list, neg_results: list) -> str:
    """Render one ETF tab content."""
    display = ETF_DISPLAY.get(etf_code, etf_code)
    html = [f'<div class="tab-content" id="tab-{etf_code}">']

    # Positive section
    html.append('<div class="section">')
    html.append('<h3 class="pos-title">▲ 斜率正候選</h3>')
    html.append('<table class="ta-table">')
    html.append('<thead><tr><th>代號</th><th>名稱</th><th>階段</th><th>方向</th><th>Score</th><th>關鍵指標</th></tr></thead>')
    html.append('<tbody>')
    if pos_results:
        for code, name, result in pos_results:
            html.append(render_stock_row(code, name, result))
    else:
        html.append('<tr><td colspan="6" class="muted">(無候選)</td></tr>')
    html.append('</tbody></table>')
    html.append('</div>')

    # Negative section
    html.append('<div class="section">')
    html.append('<h3 class="neg-title">▼ 斜率負候選</h3>')
    html.append('<table class="ta-table">')
    html.append('<thead><tr><th>代號</th><th>名稱</th><th>階段</th><th>方向</th><th>Score</th><th>關鍵指標</th></tr></thead>')
    html.append('<tbody>')
    if neg_results:
        for code, name, result in neg_results:
            html.append(render_stock_row(code, name, result))
    else:
        html.append('<tr><td colspan="6" class="muted">(無候選)</td></tr>')
    html.append('</tbody></table>')
    html.append('</div>')

    html.append('</div>')
    return '\n'.join(html)


CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
       max-width: 1100px; margin: 1.5em auto; padding: 0 1em; line-height: 1.6; }
h1 { font-size: 1.6em; border-bottom: 2px solid #888; padding-bottom: .3em; }
.meta { color: #555; font-size: .9em; }
.muted { color: #888; font-style: italic; }
.ind { font-size: .85em; color: #555; }

/* Tabs */
.tabs { display: flex; gap: 0; margin: 1em 0 0; border-bottom: 2px solid #ccc; }
.tab-btn {
  padding: .5em 1.2em; border: none; background: #f0f0f0;
  cursor: pointer; font-size: .95em; border-radius: 6px 6px 0 0;
  border: 1px solid #ccc; border-bottom: none; margin-right: .3em;
}
.tab-btn:hover { background: #e0e0e0; }
.tab-btn.active { background: #fff; font-weight: 700; border-bottom: 2px solid #fff; margin-bottom: -2px; }
.tab-content { display: none; padding: 1em .5em; }
.tab-content.active { display: block; }

/* Sections */
.section { margin: 1.5em 0; }
.pos-title { color: #c62828; border-left: 4px solid #c62828; padding-left: .5em; }
.neg-title { color: #1565c0; border-left: 4px solid #1565c0; padding-left: .5em; }

/* Table */
.ta-table { width: 100%; border-collapse: collapse; margin-top: .5em; }
.ta-table th { background: #f5f5f5; padding: .4em .6em; text-align: left; font-size: .88em;
               border-bottom: 2px solid #ddd; }
.ta-table td { padding: .4em .6em; border-bottom: 1px solid #eee; font-size: .92em; }
.ta-table .code { font-weight: 700; white-space: nowrap; }
.stage {
  display: inline-block; padding: .15em .5em; border-radius: 4px;
  font-size: .85em; font-weight: 600; white-space: nowrap;
}

/* Dark mode */
@media (prefers-color-scheme: dark) {
  body { background: #1a1a1a; color: #ddd; }
  h1 { border-color: #555; }
  .meta, .ind { color: #999; }
  .tab-btn { background: #2a2a2a; color: #ccc; border-color: #444; }
  .tab-btn:hover { background: #333; }
  .tab-btn.active { background: #1a1a1a; border-bottom: 2px solid #1a1a1a; }
  .ta-table th { background: #2a2a2a; color: #ccc; border-color: #444; }
  .ta-table td { border-color: #333; }
}
"""

TAB_JS = """
<script>
(function () {
  const btns = document.querySelectorAll('.tab-btn');
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      btns.forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');
    });
  });
})();
</script>
"""


def render_html(all_etf_data: dict, output_path: str):
    """Render the full tabbed HTML page."""
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    html = []
    html.append('<!DOCTYPE html>')
    html.append('<html lang="zh-Hant"><head>')
    html.append('<meta charset="utf-8">')
    html.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    html.append('<title>ETF 持股技術分析</title>')
    html.append(f'<style>{CSS}</style>')
    html.append('</head><body>')
    html.append('<h1>ETF 持股技術分析</h1>')
    html.append(f'<p class="meta">產出時間: {today} · 候選來源: 斜率 Top5 (1/3/5/10日) 去重</p>')

    # Tab buttons
    html.append('<div class="tabs">')
    for i, code in enumerate(ALL_ETFS):
        active = ' active' if i == 0 else ''
        display = ETF_DISPLAY.get(code, code)
        html.append(f'  <button class="tab-btn{active}" data-tab="tab-{code}">{display}</button>')
    html.append('</div>')

    # Tab contents
    for code in ALL_ETFS:
        data = all_etf_data.get(code)
        if not data:
            html.append(render_etf_tab(code, [], []))
            continue
        html.append(render_etf_tab(code, data['pos'], data['neg']))

    html.append(TAB_JS)
    html.append('<hr><p class="meta">Generated by ta_report.py · indicators: MA/MACD/RSI/ATR/BB</p>')
    html.append('</body></html>')

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text('\n'.join(html), encoding='utf-8')


# ── Main ────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description='Generate TA report for ETF holdings')
    p.add_argument('--db', required=True, help='Path to etf_data.db')
    p.add_argument('--out', required=True, help='Output HTML path')
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f'[FATAL] DB not found: {db_path}', file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(str(db_path))

    # Ensure daily_prices table exists (in case fetch_prices wasn't run)
    con.execute('''CREATE TABLE IF NOT EXISTS daily_prices (
        stock_code TEXT NOT NULL, date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume INTEGER,
        PRIMARY KEY (stock_code, date))''')

    all_etf_data = {}

    for code in ALL_ETFS:
        print(f'[TA] {code} ({ETF_DISPLAY.get(code, code)})...')

        # Get slope analysis
        history, dates = fetch_etf_history(con, code)
        if not dates:
            print(f'  [SKIP] no data')
            all_etf_data[code] = {'pos': [], 'neg': []}
            continue

        has_weight = code in ETFS_WITH_WEIGHT
        analysis = analyze_view(history, dates, has_weight)

        # Get candidates
        pos_candidates = get_candidates(analysis, 'pos')
        neg_candidates = get_candidates(analysis, 'neg')
        print(f'  Candidates: {len(pos_candidates)} pos, {len(neg_candidates)} neg')

        # Classify each candidate
        pos_results = []
        for sc, nm in pos_candidates:
            result = analyze_stock(con, sc)
            pos_results.append((sc, nm, result))

        neg_results = []
        for sc, nm in neg_candidates:
            result = analyze_stock(con, sc)
            neg_results.append((sc, nm, result))

        all_etf_data[code] = {'pos': pos_results, 'neg': neg_results}

    con.close()

    print(f'[TA] Rendering → {args.out}')
    render_html(all_etf_data, args.out)
    print('[DONE]')


if __name__ == '__main__':
    main()
