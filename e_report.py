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
from ta_report import (get_candidates, load_prices, CSS,
                       STAGE_COLORS, STAGE_TEXT, SIGNAL_COLORS)
from ta_indicators import calc_indicators, calc_support_resistance
from ta_classify import generate_signal
from position_advisor import precompute, advisor_at

MIN_BARS = 60  # enough for ADX14 + MACD26 + BB20 to warm up
ENTRY = '#4527a0'  # 深紫 — unified entry-signal color (均值回歸買點 / 壓縮突破 / 建倉)
ACTION_COLOR = {'加碼': '#c62828', '建倉': ENTRY, '持有': '#546e7a',
                '減碼': '#e65100', '離場': '#2e7d32'}

# 11 cols: 倉位建議 (mini-bar + target% + 🟢離場/🟣建倉)
#          MR → 均值回歸, SB → 壓縮突破, BBW分位 → BB帶寬, 量比 = vol/vol_MA20, 年分位 = 價格一年分位
HEADER = ('<thead><tr><th>代號</th><th>名稱</th><th>收盤</th><th>趨勢</th>'
          '<th>階段</th><th>倉位建議</th><th>均值回歸</th><th>壓縮突破</th><th>BB帶寬</th><th>量比</th><th>年分位</th></tr></thead>')
# + MACD訊號 (TA 訊號欄, 取自 ta_classify.generate_signal)
HEADER_MACD = ('<thead><tr><th>代號</th><th>名稱</th><th>收盤</th><th>趨勢</th>'
               '<th>階段</th><th>倉位建議</th><th>均值回歸</th><th>壓縮突破</th><th>BB帶寬</th>'
               '<th>量比</th><th>年分位</th><th>MACD訊號</th></tr></thead>')


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


# ── 5 天 replay: E + MACD 訊號 as-of 每個交易日 ─────────────
def _macd_asof(ind, macd_hist, i, slope_dir, pe):
    """MACD (TA) 訊號 as-of index i. 用 ≤i 的 indicator slice 算 S/R 位置 +
    3 日 MACD slope (calc_support_resistance 是看最後一根, 故先 slice)."""
    ind_asof = ind.iloc[:i + 1]
    if len(ind_asof) < 4 or ind_asof['macd_hist'].isna().iloc[-1]:
        return ('—', '')
    sr = calc_support_resistance(ind_asof)
    slope_3d = macd_hist[i] - (macd_hist[i - 3] if i >= 3 else 0.0)
    g = generate_signal(slope_dir, sr.get('position', 0.5), slope_3d, pe)
    return (g['signal'], g.get('note', ''))


def asof_signals_full(con, code, df, dates, slope_dir):
    """E + MACD 訊號 as-of 每個 date (≤date 最後一根 bar). Returns {date: res_or_None}.
    res 含 close / macd_signal / macd_note. slope_dir: 'pos'/'neg'/'neutral' (固定),
    或 callable(res)->str (依該日 regime 決定, 自訂清單用).
    precompute / calc_indicators 各跑一次; 每個 date 只讀對應 index."""
    if df is None or len(df) < MIN_BARS:
        return {d: None for d in dates}
    pc = precompute(df)
    ind = calc_indicators(df)
    idx = df.index
    closes = df['close'].to_numpy()
    macd_hist = ind['macd_hist'].to_numpy()
    peer = con.execute('SELECT pe_ratio FROM stock_fundamentals WHERE stock_code=? '
                       'ORDER BY date DESC LIMIT 1', (code,)).fetchone()
    pe = peer[0] if peer else None
    out = {}
    for d in dates:
        i = int(idx.searchsorted(d, side='right')) - 1  # last bar with date <= d
        if i < 0:
            out[d] = None
            continue
        res = advisor_at(pc, i, 'pos', 'down')
        if res is None:
            out[d] = None
            continue
        res['close'] = float(closes[i])
        sd = slope_dir(res) if callable(slope_dir) else slope_dir
        res['macd_signal'], res['macd_note'] = _macd_asof(ind, macd_hist, i, sd, pe)
        out[d] = res
    return out


# ── HTML rendering (mirrors ta_report style) ─────────────────
def render_e_row(code, name, res, with_macd=False):
    if res is None:
        span = 10 if with_macd else 9
        return (f'<tr><td class="code">{code}</td><td>{name}</td>'
                f'<td colspan="{span}" class="muted">(資料不足)</td></tr>')
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
    # MACD 訊號 cell (e_report only; watchlist 不帶此欄)
    if with_macd:
        macd = res.get('macd_signal')
        if macd and macd != '—':
            sc = SIGNAL_COLORS.get(macd, '#555')
            note = res.get('macd_note', '')
            macd_cell = f'<span style="color:{sc};font-weight:700">{macd}</span>'
            if note:
                macd_cell += f'<span class="sig-note">{note}</span>'
        else:
            macd_cell = '<span class="muted">—</span>'
        macd_td = f'\n  <td>{macd_cell}</td>'
    else:
        macd_td = ''
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
  <td>{pp_cell}</td>{macd_td}
</tr>'''


def render_section(title, cls, results, with_macd=True):
    header = HEADER_MACD if with_macd else HEADER
    span = 12 if with_macd else 11
    html = [f'<div class="section">', f'<h3 class="{cls}">{title}</h3>',
            '<div class="table-wrap">', '<table class="ta-table">', header, '<tbody>']
    if results:
        for code, name, res in results:
            html.append(render_e_row(code, name, res, with_macd=with_macd))
    else:
        html.append(f'<tr><td colspan="{span}" class="muted">(無候選)</td></tr>')
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


# 巢狀 tab 切換 (日期 tab > ETF tab). 每個 [data-tab-group] 独立管自己的
# .tabs 按鈕 + .tab-content; :scope > 確保不誤抓下一層巢狀 group 的按鈕/panel.
NESTED_TAB_JS = """
<script>
(function () {
  document.querySelectorAll('[data-tab-group]').forEach(function (group) {
    var btns = Array.from(group.querySelectorAll(':scope > .tabs .tab-btn'));
    var panes = Array.from(group.querySelectorAll(':scope > .tab-content'));
    function activate(i) {
      i = Math.max(0, Math.min(i, btns.length - 1));
      btns.forEach(function (b, j) { b.classList.toggle('active', j === i); });
      panes.forEach(function (p, j) { p.classList.toggle('active', j === i); });
    }
    btns.forEach(function (b, j) {
      b.addEventListener('click', function () { activate(j); });
    });
  });
})();
</script>
"""


def render_history(date_data, output_path, title='ETF 持股 訊號'):
    """date_data: [(date, summary, tabs)] where tabs=[(key,label,pos_r,neg_r)].
    巢狀: 日期 tab (最上層) > 每個日期內的 ETF tab。最新日期預設選中。"""
    gen_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    ordered = list(reversed(date_data))  # 新 -> 舊, index 0 = 最新 = 預設
    h = ['<!DOCTYPE html>', '<html lang="zh-Hant"><head>', '<meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         f'<title>{title}</title>', f'<style>{CSS}{E_EXTRA_CSS}</style>',
         '<script async src="https://www.googletagmanager.com/gtag/js?id=G-N5G6CVZ1YJ"></script>',
         '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
         'gtag("js",new Date());gtag("config","G-N5G6CVZ1YJ");</script>',
         '</head><body>',
         f'<h1>{title}</h1>', f'<p class="meta">產出 {gen_time}</p>']
    if not ordered:
        h += ['<p class="muted">(無資料 — 無候選或歷史不足)</p>', '</body></html>']
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text('\n'.join(h), encoding='utf-8')
        return
    # 日期 tab-group (最上層)
    h.append('<div data-tab-group>')
    h.append('<div class="tabs">')
    for i, (d, summ, tabs) in enumerate(ordered):
        active = ' active' if i == 0 else ''
        h.append(f'  <button class="tab-btn{active}">{d.strftime("%m-%d")}</button>')
    h.append('</div>')
    for i, (d, summ, tabs) in enumerate(ordered):
        active = ' active' if i == 0 else ''
        h.append(f'<div class="tab-content date-pane{active}">')
        h.append(render_summary(summ))
        h.append('<p class="ind">倉位: 長條=帶內位置、數字=目標倉位(下降趨勢→0)｜'
                 '<b style="color:#4527a0">深紫粗體=進場訊號</b>(均值回歸買點 / 壓縮突破 / 建倉)｜'
                 '歷史=固定今日候選、逐日重算｜MACD訊號=該日 TA 訊號</p>')
        # ETF tab-group (巢狀, 日期 pane 內)
        h.append('<div data-tab-group>')
        h.append('<div class="tabs">')
        for j, (key, label, _, _) in enumerate(tabs):
            active = ' active' if j == 0 else ''
            h.append(f'  <button class="tab-btn{active}">{label}</button>')
        h.append('</div>')
        for j, (key, label, pos_r, neg_r) in enumerate(tabs):
            active = ' active' if j == 0 else ''
            h.append(f'<div class="tab-content etf-pane{active}">')
            h.append(render_section('▲ 斜率正候選', 'pos-title', pos_r))
            h.append(render_section('▼ 斜率負候選', 'neg-title', neg_r))
            h.append('</div>')
        h.append('</div>')  # /ETF group
        h.append('</div>')  # /date pane
    h.append('</div>')  # /date group
    h += [NESTED_TAB_JS, '</body></html>']
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
    p.add_argument('--days', type=int, default=5,
                   help='Number of trading-day history tabs (default: 5)')
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

    # 1. 固定候選 (今天的候選股; 歷史 tab 沿用此清單, 訊號逐日重算)
    etf_cands = []  # (key, label, pos_cands, neg_cands)
    if args.codes:
        cands = []
        for code in args.codes:
            nm = con.execute('SELECT stock_name FROM daily_holdings WHERE stock_code=? LIMIT 1',
                             (code,)).fetchone()
            cands.append((code, nm[0] if nm else code))
        etf_cands.append(('ALL', '所有', cands, []))
    else:
        for code in ALL_ETFS:
            pos_cands = neg_cands = []
            history, dates = fetch_etf_history(con, code)
            if dates:
                analysis = analyze_view(history, dates, code in ETFS_WITH_WEIGHT)
                pos_cands = get_candidates(analysis, 'pos')
                neg_cands = get_candidates(analysis, 'neg')
            print(f'[E] {code} ({ETF_DISPLAY.get(code, code)}): {len(pos_cands)} pos, {len(neg_cands)} neg')
            etf_cands.append((code, ETF_DISPLAY.get(code, code), pos_cands, neg_cands))

    # 2. 載入候選股 df (cache) + 收交易日期
    cand_codes, seen = [], set()
    for (key, label, pos_c, neg_c) in etf_cands:
        for sc, nm in pos_c + neg_c:
            if sc not in seen:
                seen.add(sc)
                cand_codes.append(sc)
    df_cache, all_dates = {}, set()
    for sc in cand_codes:
        df = load_prices(con, sc)
        df_cache[sc] = df
        if df is not None and len(df) >= MIN_BARS:
            all_dates.update(df.index.tolist())
    tab_dates = sorted(all_dates)[-args.days:]
    print(f'[E] {len(tab_dates)} history dates: '
          + ', '.join(d.strftime('%Y-%m-%d') for d in tab_dates))

    # 3. 每個 (etf, code, 方向) 一次算全部日期 (precompute/calc_indicators 各一次)
    precomp = {}
    for (key, label, pos_c, neg_c) in etf_cands:
        for sc, nm in pos_c:
            precomp[(key, sc, 'pos')] = asof_signals_full(con, sc, df_cache[sc], tab_dates, 'pos')
        for sc, nm in neg_c:
            precomp[(key, sc, 'neg')] = asof_signals_full(con, sc, df_cache[sc], tab_dates, 'neg')
    con.close()

    # 4. 組每日期資料
    date_data = []
    for d in tab_dates:
        tabs, all_results = [], []
        for (key, label, pos_c, neg_c) in etf_cands:
            pos_r = [(sc, nm, precomp[(key, sc, 'pos')][d]) for sc, nm in pos_c]
            neg_r = [(sc, nm, precomp[(key, sc, 'neg')][d]) for sc, nm in neg_c]
            for sc, nm, res in pos_r + neg_r:
                if res is not None:
                    all_results.append((sc, res))
            tabs.append((key, label, pos_r, neg_r))
        date_data.append((d, summarize(all_results), tabs))

    print(f'[E] Rendering → {args.out}')
    render_history(date_data, args.out)
    print('[DONE]')


if __name__ == '__main__':
    main()
