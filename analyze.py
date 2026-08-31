"""
ETF 持股斜率分析 — 給 GitHub Pages 用
=====================================

從 SQLite (daily_holdings) 讀 5 支 ETF 持股,計算最近 N 個交易日的
「張數 / 權重」首尾差斜率,排序出正負 Top 5。

設計原則
--------
- 不做 I/O:不吃 R2、不打 API;只讀 --db 給的本地 SQLite
- 斜率 = (最後一日值 − 第一日值) / N,N = window 內資料點數(3/5/10)
- 股數單位 = 張 (1 張 = 1000 股);權重單位 = %
- 資料點不足(<2 個交易日有該股)時,該 row 留空白
- 有 weight_pct 的 ETF 顯示 張數 + 權重 兩個 metric;5 支 ETF 全都有 weight_pct
- 跨 ETF 合計:sum(shares) by stock_code;weight 跨 ETF 加總無意義(需股價),故只顯示合計 張數
- HTML 純 f-string,零外部依賴

用法
----
    python analyze.py --db Ezmoney/etf_data.db --out docs/index.html

工作流會在 Step 5 之後插入此分析,讀從 R2 拉下來的最新 DB。
"""

import argparse
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ── 設定 ─────────────────────────────────────────────
ALL_ETFS = ['49YTW', '63YTW', '00982A', '00992A', '00991A']
# Ezmoney + Fuhwa 本來就有 weight_pct;Capital (00982A/00992A) 從 API 讀 weight 補上
ETFS_WITH_WEIGHT = {'49YTW', '63YTW', '00982A', '00992A', '00991A'}
# 內部代碼 → 對外代號 (Ezmoney 49YTW/63YTW 是內部碼,Capital/Fuhwa 已是對外)
ETF_DISPLAY = {'49YTW': '00981A', '63YTW': '00403A',
               '00982A': '00982A', '00992A': '00992A', '00991A': '00991A'}
WINDOWS = (1, 3, 5, 10)
TOP_N = 5

# ── 斜率計算 ─────────────────────────────────────────
def linear_slope(points):
    """
    首尾差斜率 (simple first-last difference):
        slope = (last_y - first_y) / N
    其中 N = 資料點數 (window size,例如 3/5/10)。

    points: [(x, y), ...] — x 為交易日 index (0..n-1),y 為該日數值
            (張數已除 1000,權重為 %)
    Returns None if < 2 points。
    """
    if not points or len(points) < 2:
        return None
    first_y = points[0][1]
    last_y = points[-1][1]
    return (last_y - first_y) / len(points)

# ── DB 查詢 ─────────────────────────────────────────
def fetch_etf_history(db, fund_code):
    """
    回傳 [(date, stock_code, stock_name, lots, weight_pct)] 給定 ETF,
    按日期升冪排序。lots = shares / 1000 (張)。只取最近 MAX(WINDOWS) 個交易日。
    """
    max_w = max(WINDOWS)
    rows = db.execute('''
        SELECT date, stock_code, stock_name, shares, weight_pct
        FROM daily_holdings
        WHERE fund_code = ? AND shares IS NOT NULL
        ORDER BY date DESC
        LIMIT 10000
    ''', (fund_code,)).fetchall()
    # Reverse to ascending,並把 shares (股) → lots (張,1 張 = 1000 股)
    rows = [
        (r[0], r[1], r[2],
         (r[3] / 1000.0) if r[3] is not None else None,
         r[4])
        for r in reversed(rows)
    ]
    # 取最近 max_w 個交易日
    dates = sorted({r[0] for r in rows}, reverse=True)[:max_w]
    dates = sorted(dates)
    rows = [r for r in rows if r[0] in set(dates)]
    return rows, dates

def fetch_combined_history(db):
    """
    回傳 [(date, stock_code, stock_name, total_lots)] 跨所有 ETF 加總。
    按 (date, stock_code) 加總 shares (再除 1000 轉 張)。股票名稱取最短
    (避免「國巨」/「國巨*」/「國巨股份」混用)。
    """
    max_w = max(WINDOWS)
    rows = db.execute('''
        SELECT date, stock_code,
               (SELECT stock_name FROM daily_holdings dh2
                WHERE dh2.stock_code = dh1.stock_code
                ORDER BY LENGTH(stock_name) ASC, stock_name ASC
                LIMIT 1) as name,
               SUM(shares) as total_shares
        FROM daily_holdings dh1
        WHERE shares IS NOT NULL
        GROUP BY date, stock_code
        ORDER BY date DESC
    ''').fetchall()
    # shares (股) → lots (張,1 張 = 1000 股)
    rows = [
        (r[0], r[1], r[2],
         (r[3] / 1000.0) if r[3] is not None else None)
        for r in rows
    ]
    dates = sorted({r[0] for r in rows}, reverse=True)[:max_w]
    dates = sorted(dates)  # 升冪,跟 per-ETF 一致
    dates_set = set(dates)
    rows = [r for r in rows if r[0] in dates_set]
    rows = list(reversed(rows))
    return rows, dates

# ── 分析: 給定一個 (date -> {stock -> (name, shares, weight)}) 的 view ──
def _detect_daily_changes(history, all_dates, has_weight):
    """
    1 日視窗:比較最新日 vs 前一日,偵測所有股數變動。
      - 新增(在最新日、不在前一日) → slope = +∞ (粉紅)
      - 消失(在前一日、不在最新日) → slope = −∞ (淡綠)
      - 既有股數增加 → slope = (latest - prev) / 1 = delta (張/日)
      - 既有股數減少 → slope = (latest - prev) / 1 = delta (張/日,負值)
    排序:∞ 排最前,其餘按斜率由大到小(pos)/由小到大(neg)。
    """
    if len(all_dates) < 2:
        return [], [], [], []

    latest, prev = all_dates[-1], all_dates[-2]

    # 建 pivot: date -> {code -> (name, shares, weight?)}
    pivot = {}
    for row in history:
        d = row[0]
        code = row[1]
        pivot.setdefault(d, {})[code] = row[2:]

    def info(date, code):
        v = pivot[date][code]
        # 補齊 (name, shares, weight) — combined 只有 4 欄時 weight 為 None
        return (v[0], v[1], v[2] if len(v) > 2 else None)

    latest_codes = set(pivot.get(latest, {}).keys())
    prev_codes = set(pivot.get(prev, {}).keys())
    added = latest_codes - prev_codes
    removed = prev_codes - latest_codes
    existing = latest_codes & prev_codes

    INF = float('inf')

    # 4-tuple 格式: (code, name, slope_sh, slope_wt)

    # ── pos_sh: 股數增加(新增 +∞ 排最前,其次按 delta 降冪) ──
    pos_sh = []
    for c in added:
        nm, sh, _ = info(latest, c)
        if sh is not None:
            pos_sh.append((c, nm, INF, INF))
    for c in existing:
        nm, sh_l, wt_l = info(latest, c)
        _, sh_p, _ = info(prev, c)
        if sh_l is not None and sh_p is not None:
            delta = sh_l - sh_p
            if delta > 0:
                delta_wt = None
                if wt_l is not None:
                    _, _, wt_p = info(prev, c)
                    if wt_p is not None:
                        delta_wt = wt_l - wt_p
                pos_sh.append((c, nm, delta, delta_wt))
    # 排序:∞ 排最前,其餘按 slope 降冪
    pos_sh.sort(key=lambda r: (0 if r[2] == INF else 1,
                               0 if r[2] == INF else -r[2]))
    pos_sh = pos_sh[:TOP_N]

    # ── neg_sh: 股數減少(消失 −∞ 排最前,其次按 delta 升冪) ──
    neg_sh = []
    for c in removed:
        nm, sh, _ = info(prev, c)
        if sh is not None:
            neg_sh.append((c, nm, -INF, -INF))
    for c in existing:
        nm, sh_l, wt_l = info(latest, c)
        _, sh_p, _ = info(prev, c)
        if sh_l is not None and sh_p is not None:
            delta = sh_l - sh_p
            if delta < 0:
                delta_wt = None
                if wt_l is not None:
                    _, _, wt_p = info(prev, c)
                    if wt_p is not None:
                        delta_wt = wt_l - wt_p
                neg_sh.append((c, nm, delta, delta_wt))
    # 排序:−∞ 排最前,其餘按 slope 升冪(最負在前)
    neg_sh.sort(key=lambda r: (0 if r[2] == -INF else 1,
                               0 if r[2] == -INF else r[2]))
    neg_sh = neg_sh[:TOP_N]

    # ── pos_wt / neg_wt — 僅有 weight 的 ETF ──
    pos_wt, neg_wt = [], []
    if has_weight:
        for c in added:
            nm, _, wt = info(latest, c)
            if wt is not None:
                pos_wt.append((c, nm, INF, INF))
        for c in existing:
            nm, _, wt_l = info(latest, c)
            _, _, wt_p = info(prev, c)
            if wt_l is not None and wt_p is not None:
                delta = wt_l - wt_p
                if delta > 0:
                    pos_wt.append((c, nm, delta, delta))
        pos_wt.sort(key=lambda r: (0 if r[2] == INF else 1,
                                   0 if r[2] == INF else -r[2]))
        pos_wt = pos_wt[:TOP_N]

        for c in removed:
            nm, _, wt = info(prev, c)
            if wt is not None:
                neg_wt.append((c, nm, -INF, -INF))
        for c in existing:
            nm, _, wt_l = info(latest, c)
            _, _, wt_p = info(prev, c)
            if wt_l is not None and wt_p is not None:
                delta = wt_l - wt_p
                if delta < 0:
                    neg_wt.append((c, nm, delta, delta))
        neg_wt.sort(key=lambda r: (0 if r[2] == -INF else 1,
                                   0 if r[2] == -INF else r[2]))
        neg_wt = neg_wt[:TOP_N]

    return pos_sh, neg_sh, pos_wt, neg_wt


def analyze_view(history, dates, has_weight):
    """
    history: list of rows (date, stock_code, name, shares, weight_pct_or_None)
    dates: list of dates in window (sorted asc), length >= 2
    回傳 dict: { window: { 'pos_sh': [...], 'neg_sh': [...], 'pos_wt': [...], 'neg_wt': [...] } }
       每個 list 是 [(code, name, slope), ...] sorted by slope
    """
    # 建立 pivot: date -> { stock_code -> (name, shares, weight) }
    pivot = {}
    for row in history:
        d = row[0]
        code = row[1]
        name = row[2]
        shares = row[3]
        weight = row[4] if len(row) >= 5 else None
        if d not in pivot:
            pivot[d] = {}
        pivot[d][code] = (name, shares, weight)

    all_codes = set()
    for d in dates:
        all_codes.update(pivot.get(d, {}).keys())

    n = len(dates)
    results = {}
    for w in WINDOWS:
        if w > n:
            results[w] = {'pos_sh': [], 'neg_sh': [], 'pos_wt': [], 'neg_wt': []}
            continue
        # 1 日:用 set 差集偵測新增/消失 (slope = ±∞)
        if w == 1:
            pos_sh, neg_sh, pos_wt, neg_wt = _detect_daily_changes(
                history, dates, has_weight)
            results[w] = {
                'pos_sh': pos_sh, 'neg_sh': neg_sh,
                'pos_wt': pos_wt, 'neg_wt': neg_wt,
            }
            continue
        recent_dates = dates[-w:]
        # 收集每個 stock 的 (shares_slope, weight_slope)
        rows_for_w = []
        for code in all_codes:
            name = None
            xs = list(range(len(recent_dates)))
            sh_series = []
            wt_series = []
            for i, d in enumerate(recent_dates):
                rec = pivot.get(d, {}).get(code)
                if rec is None:
                    sh_series.append(None)
                    wt_series.append(None)
                else:
                    nm, sh, wt = rec
                    if name is None and nm:
                        name = nm
                    sh_series.append(sh if sh is not None else None)
                    wt_series.append(wt if wt is not None else None)
            sh_pts = [(x, y) for x, y in zip(xs, sh_series) if y is not None]
            wt_pts = [(x, y) for x, y in zip(xs, wt_series) if y is not None] if has_weight else []
            slope_sh = linear_slope(sh_pts)
            slope_wt = linear_slope(wt_pts) if has_weight else None
            rows_for_w.append((code, name or '', slope_sh, slope_wt))

        # Shares ranking (只列斜率非 0 的)
        valid_sh = [r for r in rows_for_w if r[2] is not None and r[2] != 0]
        pos_sh = sorted([r for r in valid_sh if r[2] > 0], key=lambda r: -r[2])[:TOP_N]
        neg_sh = sorted([r for r in valid_sh if r[2] < 0], key=lambda r:  r[2])[:TOP_N]
        results[w] = {'pos_sh': pos_sh, 'neg_sh': neg_sh, 'pos_wt': [], 'neg_wt': []}

        # Weight ranking (獨立於 shares,只用有效 weight 斜率)
        if has_weight:
            valid_wt = [r for r in rows_for_w if r[3] is not None and r[3] != 0]
            pos_wt = sorted([r for r in valid_wt if r[3] > 0], key=lambda r: -r[3])[:TOP_N]
            neg_wt = sorted([r for r in valid_wt if r[3] < 0], key=lambda r:  r[3])[:TOP_N]
            results[w]['pos_wt'] = pos_wt
            results[w]['neg_wt'] = neg_wt
    return results

# ── HTML 渲染 ─────────────────────────────────────────
def render_dense_summary(per_etf_results, combined_results, dates_meta):
    """
    Card carousel: 1 card per ETF (5 支 + 1 跨 ETF 合計 = 6 卡片)。
    每張卡片 = 1 個 ETF 的 4 區段 Top 5 摘要(張+/-, 權+/-)。
    卡片以 CSS scroll-snap 橫向滑動,行動裝置 1 卡滿版 / 桌機 2-3 卡並排。

    與下方 <details> 區段互補:卡片給概覽、<details> 給完整斜率數值。
    """
    col_groups = list(ALL_ETFS) + ['__combined__']

    def label_of(g):
        return '跨 5 ETF' if g == '__combined__' else ETF_DISPLAY[g]

    def has_weight_of(g):
        if g == '__combined__':
            return False
        return g in ETFS_WITH_WEIGHT

    def get_rows(g, w, key):
        if g == '__combined__':
            return combined_results.get(w, {}).get(key, [])
        return per_etf_results.get(g, {}).get(w, {}).get(key, [])

    def get_dates(g):
        return dates_meta.get(g, [])

    sections = [
        ('張數斜率 (+)', 'pos_sh', 'pos'),
        ('張數斜率 (−)', 'neg_sh', 'neg'),
        ('權重斜率 (+)', 'pos_wt', 'pos'),
        ('權重斜率 (−)', 'neg_wt', 'neg'),
    ]

    html = ['<div class="card-carousel" id="summary-carousel">']
    for g in col_groups:
        dates = get_dates(g)
        has_weight = has_weight_of(g)
        if dates:
            date_info = f'{dates[0]} ~ {dates[-1]} ({len(dates)} 天)'
        else:
            date_info = '無資料'

        html.append('<div class="card">')
        html.append(f'  <div class="card-header"><h3>{label_of(g)}</h3>')
        html.append(f'    <p class="dates">{date_info}</p></div>')
        html.append('  <table class="card-table">')
        html.append('    <thead>')
        html.append('      <tr><th class="corner">#</th>')
        for w in WINDOWS:
            html.append(f'      <th>{w} 日</th>')
        html.append('      </tr>')
        html.append('    </thead>')
        html.append('    <tbody>')

        for section_title, key, dir_cls in sections:
            html.append(
                f'      <tr class="section-header">'
                f'<th colspan="{1 + len(WINDOWS)}">{section_title}</th></tr>')
            for rank in range(TOP_N):
                html.append('      <tr>')
                html.append(f'        <td class="rank">{rank + 1}</td>')
                for w in WINDOWS:
                    if key in ('pos_wt', 'neg_wt') and not has_weight:
                        html.append('        <td class="muted">—</td>')
                        continue
                    rows_list = get_rows(g, w, key)
                    if rank < len(rows_list):
                        entry = rows_list[rank]
                        stock_code = entry[0]
                        stock_name = entry[1]
                        slope = entry[2]
                        # 1 日視窗的 ±∞ 斜率 → 粉紅/淡綠底色
                        cell_cls = dir_cls
                        if slope is not None and math.isinf(slope):
                            cell_cls = 'new-add' if slope > 0 else 'new-remove'
                        html.append(
                            f'        <td class="{cell_cls}">'
                            f'<span class="code">{stock_code}</span>'
                            f'<span class="nm">{stock_name}</span></td>')
                    else:
                        html.append('        <td class="muted">—</td>')
                html.append('      </tr>')

        html.append('    </tbody>')
        html.append('  </table>')
        html.append('</div>')
    html.append('</div>')

    # 導航 dots — 6 個,點擊跳到對應卡片
    html.append('<nav class="card-dots" aria-label="卡片導航">')
    for i, g in enumerate(col_groups):
        active = ' active' if i == 0 else ''
        html.append(
            f'  <button class="dot{active}" data-idx="{i}" '
            f'aria-label="{label_of(g)}"></button>')
    html.append('</nav>')

    return ''.join(html)


def render_etf_summary(label, res, has_weight):
    """
    Render one ETF's summary card: 4 (or 2) sub-grids.
    每個 sub-grid 是 5×3 (rank × window) table,顯示 Top 5 股票代號/名稱。
    res: analyze_view() 結果 (含 pos_sh/neg_sh/pos_wt/neg_wt 各 window)
    has_weight: True 顯示 4 個 sub-grid (股+/-, 權+/-),False 只 2 個 (股+/-)
    """
    html = []
    html.append(f'<div class="etf-card">')
    html.append(f'  <h3 class="etf-header">{label}</h3>')
    html.append('  <div class="summary-grids">')

    sub_grids = [
        ('張數斜率 ▲', 'pos_sh', 'pos'),
        ('張數斜率 ▼', 'neg_sh', 'neg'),
    ]
    if has_weight:
        sub_grids += [
            ('權重斜率 ▲', 'pos_wt', 'pos'),
            ('權重斜率 ▼', 'neg_wt', 'neg'),
        ]

    for grid_title, key, dir_class in sub_grids:
        html.append('    <div class="grid">')
        html.append(f'      <div class="grid-title {dir_class}">{grid_title}</div>')
        html.append('      <table>')
        html.append('        <thead>')
        html.append('          <tr><th>#</th>')
        for w in WINDOWS:
            html.append(f'          <th>{w} 日</th>')
        html.append('          </tr>')
        html.append('        </thead>')
        html.append('        <tbody>')
        for rank in range(TOP_N):
            html.append('          <tr>')
            html.append(f'          <td class="rank">{rank + 1}</td>')
            for w in WINDOWS:
                rows_list = res.get(w, {}).get(key, [])
                if rank < len(rows_list):
                    entry = rows_list[rank]
                    stock_code = entry[0]
                    stock_name = entry[1]
                    html.append(
                        f'          <td class="{dir_class}">'
                        f'<span class="code">{stock_code}</span>'
                        f'<span class="nm">{stock_name}</span></td>')
                else:
                    html.append('          <td class="muted">—</td>')
            html.append('          </tr>')
        html.append('        </tbody>')
        html.append('      </table>')
        html.append('    </div>')

    html.append('  </div>')
    html.append('</div>')
    return ''.join(html)


def fmt_slope(v, kind='shares'):
    if v is None:
        return '<span class="muted">—</span>'
    # 1 日視窗的 ±∞ → 顯示「新增/退出」並套底色
    if math.isinf(v):
        if v > 0:
            return '<span class="new-add">新增</span>'
        return '<span class="new-remove">退出</span>'
    if kind == 'shares':
        # 張/日
        if abs(v) >= 1000:
            return f'{v:+,.0f} 張/日'
        elif abs(v) >= 1:
            return f'{v:+,.2f} 張/日'
        else:
            return f'{v:+,.4f} 張/日'
    else:
        # weight %/日
        return f'{v:+.3f}%/日'

def render_block(title, shares_rows, weight_rows, has_weight, direction):
    """Render one analysis block: shares slope table + (optional) weight slope table."""
    dir_class = 'pos' if direction == 'pos' else 'neg'
    arrow = '▲' if direction == 'pos' else '▼'
    html = [f'<div class="block">']
    html.append(f'  <h3 class="{dir_class}">{arrow} {title}</h3>')
    # Shares table (張數斜率)
    html.append('  <table class="t">')
    html.append('    <thead><tr><th colspan="4">張數斜率</th></tr>')
    html.append('    <tr><th>#</th><th>代號</th><th>名稱</th><th>斜率</th></tr></thead>')
    html.append('    <tbody>')
    if not shares_rows:
        html.append('      <tr><td colspan="4" class="muted">(資料點不足,留白)</td></tr>')
    else:
        for i, (code, name, slope_sh, _wt) in enumerate(shares_rows, 1):
            html.append(f'      <tr><td>{i}</td><td>{code}</td><td>{name}</td>'
                        f'<td class="{dir_class}">{fmt_slope(slope_sh, "shares")}</td></tr>')
    html.append('    </tbody></table>')
    # Weight table (independent ranking)
    if has_weight:
        html.append('  <table class="t">')
        html.append('    <thead><tr><th colspan="4">權重斜率</th></tr>')
        html.append('    <tr><th>#</th><th>代號</th><th>名稱</th><th>斜率</th></tr></thead>')
        html.append('    <tbody>')
        if not weight_rows:
            html.append('      <tr><td colspan="4" class="muted">(資料點不足,留白)</td></tr>')
        else:
            for i, (code, name, _sh, slope_wt) in enumerate(weight_rows, 1):
                html.append(f'      <tr><td>{i}</td><td>{code}</td><td>{name}</td>'
                            f'<td class="{dir_class}">{fmt_slope(slope_wt, "weight")}</td></tr>')
        html.append('    </tbody></table>')
    html.append('</div>')
    return ''.join(html)

CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
       max-width: 1100px; margin: 1.5em auto; padding: 0 1em; line-height: 1.5; }
h1 { font-size: 1.6em; border-bottom: 2px solid #888; padding-bottom: .3em; }
h2 { font-size: 1.3em; margin-top: 2em; border-left: 4px solid #4472C4; padding-left: .5em; }
h3 { font-size: 1.0em; margin: 1em 0 .5em; }
.pos { color: #c62828; }
.neg { color: #1565c0; }
.muted { color: #888; font-style: italic; }
.meta { color: #555; font-size: .9em; }

/* card carousel — 1 card per ETF, swipe horizontally */
.card-carousel {
  display: flex;
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  scroll-behavior: smooth;
  -webkit-overflow-scrolling: touch;
  margin: 1em 0;
  border-radius: 6px;
  scrollbar-width: none;        /* Firefox */
}
.card-carousel::-webkit-scrollbar { display: none; }   /* Chrome/Safari */
.card {
  flex: 0 0 100%;
  scroll-snap-align: start;
  padding: .6em .8em;
  box-sizing: border-box;
  border: 1px solid #ccc;
  border-radius: 6px;
  background: #fff;
  margin-right: .6em;
}

.card-header { text-align: center; margin-bottom: .8em;
               border-bottom: 1px solid #eee; padding-bottom: .5em; }
.card-header h3 { margin: 0; font-size: 1.3em; color: #2c4f8e; }
.card-header .dates { margin: .2em 0 0; font-size: .85em; color: #888; }

table.card-table { width: 100%; border-collapse: collapse; font-size: .82em; }
table.card-table th, table.card-table td {
  padding: .25em .35em; border: 1px solid #ddd;
  text-align: center; vertical-align: middle; white-space: nowrap;
}
table.card-table thead th { background: #4472C4; color: white; font-weight: 600; }
table.card-table thead th.corner { background: #2c4f8e; }
table.card-table tr.section-header th {
  background: #e8edf7; color: #2c4f8e;
  text-align: left; padding: .25em .6em;
  font-weight: 700; font-size: .9em;
}
table.card-table td.rank {
  background: #f0f0f0; color: #666; font-weight: 700; width: 1.8em;
}
table.card-table td .code { font-weight: 700; }
table.card-table td .nm {
  font-size: .85em; color: #555; margin-left: .3em;
  display: inline;   /* 跟 .code 同一行 */
}
table.card-table td.pos { color: #c62828; }
table.card-table td.neg { color: #1565c0; }
table.card-table td.muted { color: #aaa; font-weight: normal; }
table.card-table tbody tr:nth-child(even) td:not(.rank):not(.new-add):not(.new-remove) { background: #fafafa; }

/* 1 日視窗:新增(+∞)粉紅底、消失(−∞)淡綠底 */
table.card-table td.new-add {
  background: #fce4ec;   /* Material pink 50 */
  color: #880e4f;
  font-weight: 600;
}
table.card-table td.new-remove {
  background: #e8f5e9;   /* Material green 50 */
  color: #1b5e20;
  font-weight: 600;
}

.card-dots {
  display: flex; justify-content: center; gap: .5em;
  margin: .8em 0 1em;
}
.card-dots .dot {
  width: 10px; height: 10px;
  border-radius: 50%;
  border: 1px solid #888;
  background: transparent;
  cursor: pointer;
  padding: 0;
  transition: background .2s;
}
.card-dots .dot:hover { background: #aaa; }
.card-dots .dot.active { background: #4472C4; border-color: #4472C4; }

/* collapsible ETF sections */
details.etf-section { border: 1px solid #ccc; border-radius: 6px;
                      padding: .6em 1em; margin: 1em 0; }
details.etf-section > summary { cursor: pointer; font-size: 1.15em; font-weight: 600;
                                list-style: none; padding: .3em 0; }
details.etf-section > summary::-webkit-details-marker { display: none; }
details.etf-section > summary::before { content: '▶ '; color: #888; font-size: .8em; }
details.etf-section[open] > summary::before { content: '▼ '; }
details.etf-section[open] { background: #fafafa; padding-bottom: 1em; }

.block { display: flex; flex-wrap: wrap; gap: 1em; margin-bottom: 1.2em; }
.block table { flex: 1 1 320px; border-collapse: collapse; }
.block th { background: #f0f0f0; color: #222; padding: .4em .6em; text-align: left; font-size: .9em; }
.block td { padding: .35em .6em; border-bottom: 1px solid #eee; font-size: .95em; }
.block tr:last-child td { border-bottom: none; }
@media (prefers-color-scheme: dark) {
  body { background: #1a1a1a; color: #ddd; }
  h1, h2 { border-color: #555; }
  .block th { background: #333; color: #eee; }
  .block td { border-bottom-color: #333; }
  .pos { color: #ef5350; }
  .neg { color: #64b5f6; }
  table.summary th, table.summary td { border-color: #555; }
  table.dense th, table.dense td { border-color: #555; }
  table.dense thead th { background: #1f3a6e; }
  table.dense tr.section-header th { background: #2a3a5e; color: #c0d0f0; }
  table.dense tbody tr:nth-child(even) td:not(.rank) { background: #222; }
  table.dense td .nm { color: #aaa; }
  .dense-summary { border-color: #555; background: #1f1f1f; }
  details.etf-section { border-color: #555; }
  details.etf-section[open] { background: #222; }
  /* card carousel dark */
  .card { background: #1f1f1f; border-color: #555; }
  .card-header h3 { color: #c0d0f0; }
  .card-header .dates { color: #aaa; }
  table.card-table th, table.card-table td { border-color: #555; }
  table.card-table thead th { background: #1f3a6e; }
  table.card-table tr.section-header th { background: #2a3a5e; color: #c0d0f0; }
  table.card-table td.rank { background: #333; color: #aaa; }
  table.card-table td .nm { color: #aaa; }
  table.card-table tbody tr:nth-child(even) td:not(.rank):not(.new-add):not(.new-remove) { background: #222; }
  table.card-table td.new-add { background: #4a2c35; color: #f8bbd0; }
  table.card-table td.new-remove { background: #2c4a32; color: #c8e6c9; }
}"""

# ── Card carousel JavaScript ─────────────────────────────
CARD_CAROUSEL_JS = """
<script>
(function () {
  const carousel = document.getElementById('summary-carousel');
  if (!carousel) return;
  const cards = carousel.querySelectorAll('.card');
  const dots = document.querySelectorAll('.card-dots .dot');

  function setActive(idx) {
    dots.forEach((d, i) => d.classList.toggle('active', i === idx));
  }

  // dot click → scroll to card
  dots.forEach((dot, idx) => {
    dot.addEventListener('click', () => {
      cards[idx].scrollIntoView({ behavior: 'smooth', inline: 'start', block: 'nearest' });
      setActive(idx);
    });
  });

  // scroll → update active dot
  let scrollTimer = null;
  carousel.addEventListener('scroll', () => {
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      const carouselLeft = carousel.getBoundingClientRect().left;
      let activeIdx = 0;
      cards.forEach((c, i) => {
        const r = c.getBoundingClientRect();
        // 第一個左邊界在 viewport 內的卡片 = active
        if (r.left - carouselLeft > -c.offsetWidth / 2) activeIdx = i;
      });
      setActive(Math.min(activeIdx, cards.length - 1));
    }, 80);
  });

  // 鍵盤左右鍵切換(桌機)
  document.addEventListener('keydown', (e) => {
    const activeIdx = Array.from(dots).findIndex(d => d.classList.contains('active'));
    if (e.key === 'ArrowRight' && activeIdx < cards.length - 1) {
      cards[activeIdx + 1].scrollIntoView({ behavior: 'smooth', inline: 'start', block: 'nearest' });
    } else if (e.key === 'ArrowLeft' && activeIdx > 0) {
      cards[activeIdx - 1].scrollIntoView({ behavior: 'smooth', inline: 'start', block: 'nearest' });
    }
  });
})();
</script>
"""


def render_html(per_etf_results, combined_results, dates_meta, output_path):
    """
    per_etf_results: { etf_code: analyze_view(...) result dict }
    combined_results: analyze_view(...) result dict
    dates_meta: { etf_code: [dates, ...] or 'combined': [...] }
    """
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    html = []
    html.append('<!DOCTYPE html>')
    html.append('<html lang="zh-Hant"><head>')
    html.append('<meta charset="utf-8">')
    html.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    html.append('<title>ETF 持股分析</title>')
    html.append(f'<style>{CSS}</style>')
    html.append('</head><body>')
    html.append('<h1>ETF 持股分析</h1>')
    html.append(f'<p class="meta">產出時間:{today}</p>')

    # ── Card carousel summary at top (one card per ETF, swipe horizontally) ──
    html.append('<h2>總表</h2>')
    html.append(render_dense_summary(per_etf_results, combined_results, dates_meta))

    # ── Per ETF sections (collapsible) ──
    for code in ALL_ETFS:
        if code not in per_etf_results:
            continue
        res = per_etf_results[code]
        dates = dates_meta.get(code, [])
        has_weight = code in ETFS_WITH_WEIGHT
        display = ETF_DISPLAY[code]
        html.append('<details class="etf-section">')
        date_info = ''
        if dates:
            date_info = f' — 最近交易日:{dates[0]} ~ {dates[-1]} ({len(dates)} 天可用)'
        else:
            date_info = ' — 無資料'
        html.append(f'<summary>{display}{date_info}</summary>')
        for w in WINDOWS:
            html.append(render_block(
                f'最近 {w} 個交易日 — 斜率正最大 Top {TOP_N}',
                res[w]['pos_sh'], res[w]['pos_wt'], has_weight, 'pos'))
            html.append(render_block(
                f'最近 {w} 個交易日 — 斜率負最大 Top {TOP_N}',
                res[w]['neg_sh'], res[w]['neg_wt'], has_weight, 'neg'))
        html.append('</details>')

    # ── Combined ETF section (collapsible, 僅股數) ──
    if combined_results:
        dates = dates_meta.get('combined', [])
        html.append('<details class="etf-section">')
        date_info = ''
        if dates:
            date_info = f' — 合計後交易日:{dates[0]} ~ {dates[-1]} ({len(dates)} 天可用)'
        else:
            date_info = ' — 無資料'
        html.append(f'<summary>跨 5 ETF 合計 (僅張數){date_info}</summary>')
        for w in WINDOWS:
            html.append(render_block(
                f'最近 {w} 個交易日 — 合計張數斜率正最大 Top {TOP_N}',
                combined_results[w]['pos_sh'], [], False, 'pos'))
            html.append(render_block(
                f'最近 {w} 個交易日 — 合計張數斜率負最大 Top {TOP_N}',
                combined_results[w]['neg_sh'], [], False, 'neg'))
        html.append('</details>')

    # ── Card carousel navigation script ──
    html.append(CARD_CAROUSEL_JS)

    html.append('<hr><p class="meta">Generated by analyze.py · data: SQLite daily_holdings</p>')
    html.append('</body></html>')

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text('\n'.join(html), encoding='utf-8')

# ── Main ─────────────────────────────────────────
def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', required=True, help='path to etf_data.db')
    p.add_argument('--out', required=True, help='output HTML path')
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f'[FATAL] DB not found: {db_path}', file=sys.stderr)
        sys.exit(1)

    print(f'[1] Opening {db_path}')
    con = sqlite3.connect(str(db_path))

    per_etf_results = {}
    dates_meta = {}
    for code in ALL_ETFS:
        print(f'[2] Analyzing {code}...')
        history, dates = fetch_etf_history(con, code)
        dates_meta[code] = dates
        has_weight = code in ETFS_WITH_WEIGHT
        per_etf_results[code] = analyze_view(history, dates, has_weight)
        print(f'    dates available: {len(dates)} ({dates[0] if dates else "-"} ~ {dates[-1] if dates else "-"})')

    print('[3] Analyzing combined (sum across 5 ETFs)...')
    comb_history, comb_dates = fetch_combined_history(con)
    dates_meta['combined'] = comb_dates
    combined_results = analyze_view(comb_history, comb_dates, has_weight=False)
    print(f'    dates available: {len(comb_dates)} ({comb_dates[0] if comb_dates else "-"} ~ {comb_dates[-1] if comb_dates else "-"})')

    print(f'[4] Rendering HTML → {args.out}')
    render_html(per_etf_results, combined_results, dates_meta, args.out)
    con.close()
    print('[OK]')

if __name__ == '__main__':
    main()
