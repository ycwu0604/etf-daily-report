#!/usr/bin/env python3
"""
ETF 每日持股 HTML 報表產生器

從 SQLite DB 讀取資料，產生一份可在瀏覽器看的 HTML 報表，
部署到 Cloudflare Pages 後就能隨時查看最新 ETF 持股狀況。

輸出:
  reports/etf_report/index.html — 主報表頁面
  reports/etf_report/ — 包含 HTML + xlsx 下載連結

使用方式:
  python gen_html_report.py
  python gen_html_report.py --from 2026-07-01 --to 2026-07-31
"""

import os
import sys
from datetime import datetime
from html import escape

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 動態 import 子模組 ────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ezmoney_dir = os.path.join(SCRIPT_DIR, 'Ezmoney')

if _ezmoney_dir not in sys.path:
    sys.path.insert(0, _ezmoney_dir)

from ezmoney_db_reader import fetch_all_etf_data, fetch_futures_data, ALL_ETF_CODES  # noqa: E402

# ── 路徑設定 ──────────────────────────────────────────────
OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'reports', 'etf_report')

# ── ETF 顯示名稱對應 ──────────────────────────────────────
ETF_DISPLAY = {
    '49YTW': '統一台股增長 (00981A)',
    '63YTW': '統一升級50 (00403A)',
    '00982A': '群益台灣精選科技 (00982A)',
    '00992A': '群益半導體精選 (00992A)',
}


def _fmt_shares(n) -> str:
    """格式化股數：大數用千分位。"""
    if n is None:
        return '-'
    return f'{n:,}'


def _fmt_change(curr, prev) -> str:
    """格式化變化：帶顏色。"""
    if curr is None or prev is None:
        if curr is not None and prev is None:
            return '<span class="new">NEW</span>'
        if prev is not None and curr is None:
            return '<span class="del">DEL</span>'
        return '-'
    diff = curr - prev
    if diff == 0:
        return '<span class="zero">0</span>'
    pct = (diff / prev * 100) if prev != 0 else 0
    sign = '+' if diff > 0 else ''
    cls = 'up' if diff > 0 else 'down'
    return f'<span class="{cls}">{sign}{diff:,} ({sign}{pct:.1f}%)</span>'


def _fmt_weight(val) -> str:
    if val is None:
        return '-'
    return f'{val:.2f}%'


def _shares_class(curr, prev) -> str:
    """Return CSS class for historical shares cell."""
    if curr is None:
        return 'cell-na'
    if prev is None:
        return 'cell-new'
    if curr > prev:
        return 'cell-up'
    if curr < prev:
        return 'cell-down'
    return 'cell-same'


def generate_html_report(date_from=None, date_to=None) -> str:
    """產生 HTML 報表，回傳檔案路徑。"""

    all_etf_data = fetch_all_etf_data(date_from, date_to)
    futures_data = fetch_futures_data(date_from=date_from, date_to=date_to)

    all_dates = sorted({d for data in all_etf_data.values() for d in data})
    if not all_dates:
        print('[ERROR] No trading days found')
        return ''

    latest_date = all_dates[-1]
    prev_date = all_dates[-2] if len(all_dates) >= 2 else all_dates[-1]
    generated = datetime.now().strftime('%Y-%m-%d %H:%M')

    # ── Build ETF tabs ──────────────────────────────────────
    tabs_html = ''
    panels_html = ''
    active_etfs = [c for c in ALL_ETF_CODES if c in all_etf_data and all_etf_data[c]]

    for idx, etf_code in enumerate(active_etfs):
        etf_data = all_etf_data[etf_code]
        display = ETF_DISPLAY.get(etf_code, etf_code)
        is_active = (idx == 0)
        tab_cls = 'tab active' if is_active else 'tab'
        panel_cls = 'panel active' if is_active else 'panel'

        # ── Holdings table ──
        all_stock_codes = set()
        for d in etf_data:
            all_stock_codes.update(etf_data[d].keys())
        latest_holdings = etf_data.get(latest_date, {})
        sorted_codes = sorted(
            all_stock_codes,
            key=lambda c: (-latest_holdings.get(c, {}).get('shares', 0), c),
        )

        # Left table: latest vs prev comparison
        left_rows = ''
        for code in sorted_codes:
            name = ''
            for d in sorted(etf_data, reverse=True):
                if code in etf_data[d]:
                    n = etf_data[d][code].get('name', '') or ''
                    if n:
                        name = n
                        break

            prev_s = etf_data.get(prev_date, {}).get(code, {}).get('shares')
            curr_s = etf_data.get(latest_date, {}).get(code, {}).get('shares')
            change_html = _fmt_change(curr_s, prev_s)
            weight = latest_holdings.get(code, {}).get('weight_pct')

            left_rows += f"""
            <tr>
              <td class="code">{escape(code)}</td>
              <td class="name">{escape(name)}</td>
              <td class="num">{_fmt_shares(prev_s)}</td>
              <td class="num">{_fmt_shares(curr_s)}</td>
              <td class="change">{change_html}</td>
              <td class="weight">{_fmt_weight(weight)}</td>
            </tr>"""

        # Right table: historical shares (last 10 dates for readability)
        recent_dates = all_dates[-10:]
        right_header = ''.join(f'<th>{d[5:]}</th>' for d in recent_dates)  # show MM-DD only
        right_rows = ''
        for code in sorted_codes:
            name = ''
            for d in sorted(etf_data, reverse=True):
                if code in etf_data[d]:
                    n = etf_data[d][code].get('name', '') or ''
                    if n:
                        name = n
                        break

            cells = ''
            for di, d in enumerate(recent_dates):
                s = etf_data.get(d, {}).get(code, {}).get('shares')
                prev_s = etf_data.get(recent_dates[di - 1], {}).get(code, {}).get('shares') if di > 0 else None
                cls = _shares_class(s, prev_s)
                val = _fmt_shares(s) if s is not None else '-'
                cells += f'<td class="{cls}">{val}</td>'

            right_rows += f"""
            <tr>
              <td class="code">{escape(code)}</td>
              <td class="name">{escape(name)}</td>
              {cells}
            </tr>"""

        # Futures section
        futures_html = ''
        if etf_code in futures_data:
            etf_fut = futures_data[etf_code]
            fut_dates = sorted(etf_fut.keys(), reverse=True)
            if fut_dates:
                fut_rows = ''
                for d in fut_dates:
                    for fut in etf_fut[d]:
                        fut_rows += f"""
                        <tr>
                          <td>{escape(d)}</td>
                          <td class="code">{escape(fut.get('code', ''))}</td>
                          <td class="name">{escape(fut.get('name', ''))}</td>
                          <td class="weight">{_fmt_weight(fut.get('weight_pct'))}</td>
                          <td class="num">{_fmt_shares(fut.get('lots'))}</td>
                          <td>{escape(fut.get('contract_month', ''))}</td>
                        </tr>"""
                futures_html = f"""
                <div class="futures-section">
                  <h3>期貨持倉</h3>
                  <table class="data-table futures-table">
                    <thead><tr><th>日期</th><th>期貨代號</th><th>期貨名稱</th><th>持股權重</th><th>口數</th><th>契約年月</th></tr></thead>
                    <tbody>{fut_rows}</tbody>
                  </table>
                </div>"""

        # Combined left + right into this panel
        panel_content = f"""
        <div class="{panel_cls}" id="panel-{etf_code}">
          <div class="dual-table">
            <div class="table-block left-block">
              <h3>每日持股比較 <span class="date-range">{prev_date} → {latest_date}</span></h3>
              <table class="data-table compare-table">
                <thead><tr><th>代號</th><th>名稱</th><th>{prev_date[5:]}</th><th>{latest_date[5:]}</th><th>變化</th><th>權重</th></tr></thead>
                <tbody>{left_rows}</tbody>
              </table>
            </div>
            <div class="table-block right-block">
              <h3>歷史股數趨勢</h3>
              <table class="data-table history-table">
                <thead><tr><th>代號</th><th>名稱</th>{right_header}</tr></thead>
                <tbody>{right_rows}</tbody>
              </table>
            </div>
          </div>
          {futures_html}
        </div>"""

        tabs_html += f'<button class="{tab_cls}" onclick="switchTab(\'{etf_code}\')">{escape(display)}</button>'
        panels_html += panel_content

    # ── Cross-ETF combined section ──
    combined_section = ''
    if len(active_etfs) >= 2:
        etf_data_list = [all_etf_data[c] for c in active_etfs]
        all_codes = set()
        for ed in etf_data_list:
            for d in ed:
                all_codes.update(ed[d].keys())

        def _sort_key(code):
            total = sum(ed.get(latest_date, {}).get(code, {}).get('shares', 0) for ed in etf_data_list)
            return (-total, code)

        sorted_combined = sorted(all_codes, key=_sort_key)
        recent_dates = all_dates[-10:]
        comb_header = '<th>代號</th><th>名稱</th>' + ''.join(f'<th>{d[5:]}</th>' for d in recent_dates)
        comb_rows = ''
        for code in sorted_combined:
            name = ''
            for ed in etf_data_list:
                for d in sorted(ed, reverse=True):
                    if code in ed[d]:
                        n = ed[d][code].get('name', '') or ''
                        if n:
                            name = n
                            break
                    if name:
                        break
                if name:
                    break

            cells = ''
            for di, d in enumerate(recent_dates):
                total = 0
                has = False
                for ed in etf_data_list:
                    if code in ed.get(d, {}):
                        sh = ed[d][code].get('shares')
                        if sh is not None:
                            total += sh
                            has = True
                if has:
                    prev_total = 0
                    prev_has = False
                    if di > 0:
                        pd = recent_dates[di - 1]
                        for ed in etf_data_list:
                            if code in ed.get(pd, {}):
                                psh = ed[pd][code].get('shares')
                                if psh is not None:
                                    prev_total += psh
                                    prev_has = True
                    cls = _shares_class(total if has else None, prev_total if prev_has else None)
                    cells += f'<td class="{cls}">{total:,}</td>'
                else:
                    cells += '<td class="cell-na">-</td>'

            comb_rows += f'<tr><td class="code">{escape(code)}</td><td class="name">{escape(name)}</td>{cells}</tr>'

        combined_section = f"""
        <div class="combined-section">
          <h2>跨 ETF 持股合計 <span class="date-range">({", ".join(active_etfs)})</span></h2>
          <table class="data-table combined-table">
            <thead><tr>{comb_header}</tr></thead>
            <tbody>{comb_rows}</tbody>
          </table>
        </div>"""

    # ── Available xlsx files ──
    # On Cloudflare Pages, xlsx files are deployed alongside index.html
    # so links are relative to current directory (no ../)
    reports_dir = os.path.join(SCRIPT_DIR, 'reports')
    xlsx_links = ''
    if os.path.isdir(reports_dir):
        xlsx_files = sorted(
            [f for f in os.listdir(reports_dir)
             if f.endswith('.xlsx') and not f.startswith('~$')],
            reverse=True,
        )[:7]
        for xf in xlsx_files:
            # Display name: strip the long prefix for readability
            display = xf.replace('ETF_Combined_Daily_Report_', '').replace('.xlsx', '')
            xlsx_links += f'<li><a href="{xf}">{escape(display)}</a></li>'

    # ── Render full HTML ──
    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ETF 每日持股報表 · {escape(latest_date)}</title>
<style>
:root {{
  --bg: #0d1117; --card: #161b22; --card2: #1c2333;
  --text: #e6edf3; --dim: #8b949e; --accent: #58a6ff;
  --green: #3fb950; --red: #f85149; --orange: #d29922;
  --border: #30363d; --radius: 8px;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:"Noto Sans TC","Segoe UI",system-ui,sans-serif; background:var(--bg); color:var(--text); line-height:1.5; padding:12px; max-width:1200px; margin:0 auto; }}

.hero {{ text-align:center; padding:24px 0 12px; }}
.hero h1 {{ font-size:clamp(1.6rem,4vw,2.4rem); font-weight:700; color:#fff; }}
.hero .sub {{ color:var(--dim); font-size:.9rem; margin-top:4px; }}
.hero .meta {{ color:var(--dim); font-size:.75rem; margin-top:8px; }}

/* Tabs */
.tabs {{ display:flex; gap:4px; flex-wrap:wrap; margin:16px 0 8px; }}
.tab {{ background:var(--card); color:var(--dim); border:1px solid var(--border); padding:8px 16px; border-radius:var(--radius) var(--radius) 0 0; cursor:pointer; font-size:.85rem; font-weight:600; transition:all .15s; }}
.tab:hover {{ color:var(--text); background:var(--card2); }}
.tab.active {{ color:var(--accent); border-bottom-color:var(--bg); background:var(--card2); }}

/* Panels */
.panel {{ display:none; background:var(--card); border:1px solid var(--border); border-radius:0 0 var(--radius) var(--radius); padding:16px; margin-bottom:16px; overflow-x:auto; }}
.panel.active {{ display:block; }}

/* Dual table layout */
.dual-table {{ display:grid; grid-template-columns:1fr 2fr; gap:16px; }}
@media(max-width:900px) {{ .dual-table {{ grid-template-columns:1fr; }} }}

.table-block h3 {{ font-size:1rem; margin-bottom:8px; }}
.date-range {{ color:var(--dim); font-size:.8rem; font-weight:400; }}

/* Data tables */
.data-table {{ width:100%; border-collapse:collapse; font-size:.8rem; }}
.data-table th {{ text-align:left; color:var(--dim); padding:6px 8px; border-bottom:1px solid var(--border); font-weight:500; white-space:nowrap; }}
.data-table td {{ padding:5px 8px; border-bottom:1px solid rgba(255,255,255,.04); white-space:nowrap; }}
.data-table tr:hover {{ background:rgba(88,166,255,.04); }}

.code {{ font-family:"Cascadia Code","Fira Code",monospace; font-size:.78rem; color:var(--accent); }}
.name {{ font-weight:600; max-width:120px; overflow:hidden; text-overflow:ellipsis; }}
.num {{ font-variant-numeric:tabular-nums; text-align:right; }}
.weight {{ font-variant-numeric:tabular-nums; text-align:right; color:var(--dim); }}
.change {{ font-variant-numeric:tabular-nums; }}

.up {{ color:var(--green); font-weight:600; }}
.down {{ color:var(--red); font-weight:600; }}
.zero {{ color:var(--dim); }}
.new {{ color:var(--green); font-weight:700; }}
.del {{ color:var(--red); font-weight:700; }}

.cell-up {{ background:rgba(63,185,80,.08); }}
.cell-down {{ background:rgba(248,81,73,.08); }}
.cell-new {{ background:rgba(63,185,80,.15); }}
.cell-na {{ color:var(--dim); }}
.cell-same {{ }}

/* Futures */
.futures-section {{ margin-top:16px; }}
.futures-section h3 {{ font-size:1rem; margin-bottom:8px; color:var(--orange); }}

/* Combined */
.combined-section {{ background:var(--card); border:1px solid var(--border); border-radius:var(--radius); padding:16px; margin:16px 0; overflow-x:auto; }}
.combined-section h2 {{ font-size:1.1rem; margin-bottom:8px; }}

/* Downloads */
.downloads {{ background:var(--card); border:1px solid var(--border); border-radius:var(--radius); padding:16px; margin:16px 0; }}
.downloads h2 {{ font-size:1rem; margin-bottom:8px; }}
.downloads ul {{ list-style:none; }}
.downloads li {{ padding:4px 0; }}
.downloads a {{ color:var(--accent); text-decoration:none; }}
.downloads a:hover {{ text-decoration:underline; }}

/* Footer */
.footer {{ text-align:center; color:var(--dim); font-size:.72rem; margin-top:32px; padding:16px 0; border-top:1px solid var(--border); }}

/* Scroll hint */
.table-block {{ overflow-x:auto; }}

/* Responsive */
@media(max-width:600px) {{
  .data-table {{ font-size:.68rem; }}
  .data-table th, .data-table td {{ padding:3px 4px; }}
  .name {{ max-width:60px; }}
}}
</style>
</head>
<body>

<div class="hero">
  <h1>ETF 每日持股報表</h1>
  <p class="sub">最新持股日：{escape(latest_date)} · {len(active_etfs)} 檔 ETF</p>
  <p class="meta">資料來源：Ezmoney + Capital API · 產生時間 {escape(generated)}</p>
</div>

<div class="tabs">{tabs_html}</div>
{panels_html}

{combined_section}

<div class="downloads">
  <h2>下載 Excel 報表</h2>
  <ul>{xlsx_links if xlsx_links else '<li>尚無報表</li>'}</ul>
</div>

<div class="footer">
  ETF Daily Report · Ezmoney (00981A, 00403A) + Capital API (00982A, 00992A)<br>
  DB 持久化於 Cloudflare R2 · 報表部署於 Cloudflare Pages
</div>

<script>
function switchTab(id) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + id).classList.add('active');
  event.target.classList.add('active');
}}
</script>

</body>
</html>"""

    # ── Write output ──
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, 'index.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'[HTML] Report saved: {out_path}')
    return out_path


def main():
    # Parse same args as gen_combined_report.py
    args = sys.argv[1:]
    date_from = date_to = None
    i = 0
    while i < len(args):
        if args[i] == '--from' and i + 1 < len(args):
            date_from = args[i + 1]
            i += 2
        elif args[i] == '--to' and i + 1 < len(args):
            date_to = args[i + 1]
            i += 2
        elif args[i] == '--month' and i + 1 < len(args):
            month = args[i + 1]
            date_from = f'{month}-01'
            y, m = int(month[:4]), int(month[5:7])
            if m == 12:
                last_day = 31
            else:
                from datetime import timedelta
                last_day = (datetime(y, m + 1, 1) - timedelta(days=1)).day
            date_to = f'{month}-{last_day:02d}'
            i += 2
        else:
            i += 1

    generate_html_report(date_from, date_to)


if __name__ == '__main__':
    main()
