"""
Backtest: Rotation Strategy Comparison
========================================
Compares two equity-bond rotation strategies over 1 year of daily data:
  A) Slope Step: 20d slope + hysteresis, move 1 level (10%) per day
  B) Position Map: BB(20,2) position → direct allocation mapping

Also includes baselines: 100% equity, 100% bond, 50/50 fixed.

Outputs:
  - docs/backtest.html: cumulative return chart + allocation + metrics table

Usage:
    python backtest_rotation.py --out docs/backtest.html
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

import requests

# ── Config ─────────────────────────────────────────────────────────────
EQUITY_TICKER = "00981A.TW"
BOND_TICKER = "00988B.TWO"
SLOPE_WINDOW = 20
BB_WINDOW = 20
BB_STD = 2.0
HYSTERESIS = 0.2  # slope step threshold
MIN_DATA = 60  # minimum days before strategy starts

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ── Data Fetching ──────────────────────────────────────────────────────
def fetch_daily(ticker: str) -> tuple[list[float], list[str]] | None:
    """Fetch 1y daily closes + dates. Returns (closes, dates) or None."""
    url = YAHOO_CHART_URL.format(ticker=ticker)
    params = {"range": "1y", "interval": "1d"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if r.status_code == 429:
            time.sleep(5)
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"  [ERR] {ticker}: HTTP {r.status_code}")
            return None
        data = r.json()
        if "chart" not in data or not data["chart"].get("result"):
            print(f"  [ERR] {ticker}: no result")
            return None
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes_raw = result["indicators"]["quote"][0]["close"]

        from datetime import datetime, timezone
        pairs = []
        for ts, c in zip(timestamps, closes_raw):
            if c is not None:
                d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
                pairs.append((d, c))

        dates = [p[0] for p in pairs]
        closes = [p[1] for p in pairs]
        if len(closes) < MIN_DATA:
            print(f"  [ERR] {ticker}: only {len(closes)} data points")
            return None
        print(f"  OK: {len(closes)} data points ({dates[0]} to {dates[-1]})")
        return dates, closes
    except Exception as e:
        print(f"  [ERR] {ticker}: {e}")
        return None


# ── Indicators ─────────────────────────────────────────────────────────
def calc_slope_at(closes: list[float], i: int, window: int = SLOPE_WINDOW) -> float:
    """Slope at index i (uses closes[i-window] to closes[i])."""
    if i < window or i >= len(closes):
        return 0.0
    past = closes[i - window]
    if past == 0:
        return 0.0
    return (closes[i] / past - 1) * 100


def calc_bb_position_at(closes: list[float], i: int, window: int = BB_WINDOW) -> float:
    """
    Bollinger Band position at index i.
    Returns 0.0 (at lower BB) to 1.0 (at upper BB). 0.5 = middle.
    Uses data from i-window+1 to i (no look-ahead).
    """
    if i < window:
        return 0.5
    segment = closes[i - window + 1: i + 1]
    mean = sum(segment) / len(segment)
    variance = sum((x - mean) ** 2 for x in segment) / len(segment)
    std = math.sqrt(variance)
    if std == 0:
        return 0.5
    upper = mean + BB_STD * std
    lower = mean - BB_STD * std
    pos = (closes[i] - lower) / (upper - lower)
    return max(0.0, min(1.0, pos))


# ── Strategy Simulation ────────────────────────────────────────────────
def run_backtest(eq_closes: list[float], bd_closes: list[float], dates: list[str]) -> dict:
    """Run both strategies + baselines. Returns results dict."""
    n = len(eq_closes)
    # Use common length (should be same but just in case)
    n = min(n, len(bd_closes))
    eq_closes = eq_closes[:n]
    bd_closes = bd_closes[:n]
    dates = dates[:n]

    # Daily returns
    eq_rets = [0.0] * n
    bd_rets = [0.0] * n
    for i in range(1, n):
        if eq_closes[i - 1] != 0:
            eq_rets[i] = eq_closes[i] / eq_closes[i - 1] - 1
        if bd_closes[i - 1] != 0:
            bd_rets[i] = bd_closes[i] / bd_closes[i - 1] - 1

    # Strategy A: Slope Step
    level_a = 5  # start at 50/50
    alloc_a = [50.0] * n  # equity allocation % per day
    for i in range(MIN_DATA, n):
        slope = calc_slope_at(eq_closes, i)
        if slope > HYSTERESIS:
            level_a = min(level_a + 1, 10)
        elif slope < -HYSTERESIS:
            level_a = max(level_a - 1, 0)
        alloc_a[i] = level_a * 10.0

    # Strategy B: Position Mapping
    alloc_b = [50.0] * n
    for i in range(MIN_DATA, n):
        pos = calc_bb_position_at(eq_closes, i)
        equity_pct = round((1 - pos) * 10) * 10  # 0-100 in steps of 10
        alloc_b[i] = equity_pct

    # Baselines
    alloc_eq = [100.0] * n  # 100% equity
    alloc_bd = [0.0] * n    # 100% bond
    alloc_50 = [50.0] * n   # 50/50 fixed

    # Calculate cumulative values
    def cum_values(alloc: list[float]) -> list[float]:
        values = [1.0] * n
        for i in range(1, n):
            eq_alloc = alloc[i] / 100.0
            bd_alloc = 1.0 - eq_alloc
            daily_ret = eq_alloc * eq_rets[i] + bd_alloc * bd_rets[i]
            values[i] = values[i - 1] * (1 + daily_ret)
        return values

    values = {
        'A_slope': cum_values(alloc_a),
        'B_position': cum_values(alloc_b),
        'eq_100': cum_values(alloc_eq),
        'bd_100': cum_values(alloc_bd),
        'fixed_50': cum_values(alloc_50),
    }

    # Metrics
    def calc_metrics(vals: list[float], alloc: list[float]) -> dict:
        total_ret = (vals[-1] - 1) * 100
        # Max drawdown
        peak = vals[0]
        max_dd = 0.0
        for v in vals:
            peak = max(peak, v)
            dd = (peak - v) / peak * 100
            max_dd = max(max_dd, dd)
        # Sharpe (annualized)
        rets = [0.0]
        for i in range(1, len(vals)):
            rets.append(vals[i] / vals[i - 1] - 1)
        rets = rets[1:]  # skip first (0)
        if len(rets) > 1:
            mean_r = sum(rets) / len(rets)
            if len(rets) > 2:
                var = sum((r - mean_r) ** 2 for r in rets[1:]) / (len(rets) - 2)
            else:
                var = 0
            std_r = math.sqrt(var) if var > 0 else 0.0001
            sharpe = (mean_r / std_r) * math.sqrt(252)
        else:
            sharpe = 0
        # Trades: count of days where allocation changed
        trades = sum(1 for i in range(1, len(alloc)) if alloc[i] != alloc[i - 1])
        return {
            'total_return': total_ret,
            'max_drawdown': max_dd,
            'sharpe': sharpe,
            'trades': trades,
            'final_value': vals[-1],
        }

    metrics = {
        'A_slope': calc_metrics(values['A_slope'], alloc_a),
        'B_position': calc_metrics(values['B_position'], alloc_b),
        'eq_100': calc_metrics(values['eq_100'], alloc_eq),
        'bd_100': calc_metrics(values['bd_100'], alloc_bd),
        'fixed_50': calc_metrics(values['fixed_50'], alloc_50),
    }

    return {
        'dates': dates,
        'alloc_a': alloc_a,
        'alloc_b': alloc_b,
        'values': values,
        'metrics': metrics,
        'eq_closes': eq_closes,
        'bd_closes': bd_closes,
    }


# ── HTML Rendering ─────────────────────────────────────────────────────
CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
       max-width: 1000px; margin: 1.5em auto; padding: 0 1em; line-height: 1.6; }
h1 { font-size: 1.5em; border-bottom: 2px solid #888; padding-bottom: .3em; }
h2 { font-size: 1.2em; margin-top: 2em; }
.meta { color: #555; font-size: .9em; }
.chart-container { margin: 1em 0; border: 1px solid #ddd; border-radius: 8px; padding: 1em; }
svg { width: 100%; height: auto; }
.metrics-table { width: 100%; border-collapse: collapse; margin: 1em 0; }
.metrics-table th { background: #f5f5f5; padding: .5em; text-align: left; border-bottom: 2px solid #ddd; font-size: .9em; }
.metrics-table td { padding: .5em; border-bottom: 1px solid #eee; font-size: .9em; }
.highlight { background: #fff9c4; font-weight: 600; }
.best { color: #c62828; font-weight: 700; }
.worst { color: #2e7d32; }

@media (prefers-color-scheme: dark) {
  body { background: #1a1a1a; color: #ddd; }
  h1 { border-color: #555; }
  .meta { color: #999; }
  .chart-container { border-color: #444; background: #222; }
  .metrics-table th { background: #2a2a2a; color: #ccc; border-color: #444; }
  .metrics-table td { border-color: #333; }
  .highlight { background: #3d3a1e; }
}
"""

CHART_COLORS = {
    'A_slope': '#ff6f00',
    'B_position': '#7b1fa2',
    'eq_100': '#c62828',
    'bd_100': '#1565c0',
    'fixed_50': '#757575',
}

CHART_LABELS = {
    'A_slope': 'A: Slope Step',
    'B_position': 'B: Position Map',
    'eq_100': '100% 股',
    'bd_100': '100% 債',
    'fixed_50': '50/50 固定',
}


def render_line_chart(values: dict, dates: list[str], height: int = 300) -> str:
    """Render SVG line chart of cumulative returns."""
    n = len(dates)
    width = 900
    padding = {'top': 20, 'right': 20, 'bottom': 40, 'left': 50}
    plot_w = width - padding['left'] - padding['right']
    plot_h = height - padding['top'] - padding['bottom']

    # Find min/max across all series
    all_vals = []
    for vals in values.values():
        all_vals.extend(vals)
    vmin = min(all_vals) * 100  # as percentage
    vmax = max(all_vals) * 100
    margin = (vmax - vmin) * 0.05
    vmin -= margin
    vmax += margin

    def x(i): return padding['left'] + (i / max(n - 1, 1)) * plot_w
    def y(v): return padding['top'] + (1 - (v * 100 - vmin) / (vmax - vmin)) * plot_h

    # Build SVG
    svg = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']

    # Grid lines (5 horizontal)
    for i in range(6):
        gy = padding['top'] + i * plot_h / 5
        gv = vmax - i * (vmax - vmin) / 5
        svg.append(f'<line x1="{padding["left"]}" y1="{gy:.1f}" x2="{width - padding["right"]}" y2="{gy:.1f}" stroke="#888" stroke-width="0.5" stroke-dasharray="4"/>')
        svg.append(f'<text x="{padding["left"] - 5}" y="{gy + 4:.1f}" font-size="11" fill="#888" text-anchor="end">{gv:.1f}%</text>')

    # X-axis labels (every ~30 trading days)
    step = max(1, n // 6)
    for i in range(0, n, step):
        svg.append(f'<text x="{x(i):.1f}" y="{height - 5}" font-size="10" fill="#888" text-anchor="middle">{dates[i][5:]}</text>')

    # Lines
    for key, color in CHART_COLORS.items():
        if key not in values:
            continue
        points = []
        for i in range(n):
            points.append(f"{x(i):.1f},{y(values[key][i]):.1f}")
        opacity = '1' if key in ('A_slope', 'B_position') else '0.5'
        stroke_w = '2.5' if key in ('A_slope', 'B_position') else '1.5'
        svg.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="{stroke_w}" opacity="{opacity}"/>')

    # Legend
    lx = padding['left'] + 10
    ly = padding['top'] + 10
    for key, color in CHART_COLORS.items():
        svg.append(f'<rect x="{lx}" y="{ly - 8}" width="12" height="3" fill="{color}"/>')
        svg.append(f'<text x="{lx + 16}" y="{ly - 3}" font-size="11" fill="{color}">{CHART_LABELS[key]}</text>')
        ly += 16

    svg.append('</svg>')
    return ''.join(svg)


def render_alloc_chart(alloc_a: list[float], alloc_b: list[float], dates: list[str], height: int = 200) -> str:
    """Render SVG chart of equity allocation over time."""
    n = len(dates)
    width = 900
    padding = {'top': 20, 'right': 20, 'bottom': 40, 'left': 50}
    plot_w = width - padding['left'] - padding['right']
    plot_h = height - padding['top'] - padding['bottom']

    def x(i): return padding['left'] + (i / max(n - 1, 1)) * plot_w
    def y(v): return padding['top'] + (1 - v / 100) * plot_h

    svg = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']

    # Grid
    for pct in (0, 25, 50, 75, 100):
        gy = y(pct)
        svg.append(f'<line x1="{padding["left"]}" y1="{gy:.1f}" x2="{width - padding["right"]}" y2="{gy:.1f}" stroke="#888" stroke-width="0.5" stroke-dasharray="4"/>')
        svg.append(f'<text x="{padding["left"] - 5}" y="{gy + 4:.1f}" font-size="11" fill="#888" text-anchor="end">{pct}%</text>')

    # X-axis
    step = max(1, n // 6)
    for i in range(0, n, step):
        svg.append(f'<text x="{x(i):.1f}" y="{height - 5}" font-size="10" fill="#888" text-anchor="middle">{dates[i][5:]}</text>')

    # Alloc lines (step-like)
    for alloc, color, label in [(alloc_a, '#ff6f00', 'A'), (alloc_b, '#7b1fa2', 'B')]:
        points = []
        for i in range(n):
            points.append(f"{x(i):.1f},{y(alloc[i]):.1f}")
        svg.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2"/>')

    # Legend
    svg.append(f'<rect x="{padding["left"] + 10}" y="{padding["top"] + 5}" width="12" height="3" fill="#ff6f00"/>')
    svg.append(f'<text x="{padding["left"] + 26}" y="{padding["top"] + 10}" font-size="11" fill="#ff6f00">A: Slope Step</text>')
    svg.append(f'<rect x="{padding["left"] + 120}" y="{padding["top"] + 5}" width="12" height="3" fill="#7b1fa2"/>')
    svg.append(f'<text x="{padding["left"] + 136}" y="{padding["top"] + 10}" font-size="11" fill="#7b1fa2">B: Position Map</text>')

    svg.append('</svg>')
    return ''.join(svg)


def render_metrics_table(metrics: dict) -> str:
    """Render comparison metrics table."""
    rows = []
    keys = ['A_slope', 'B_position', 'fixed_50', 'eq_100', 'bd_100']
    labels = {
        'A_slope': 'A: Slope Step (動能)',
        'B_position': 'B: Position Map (S/R)',
        'fixed_50': '50/50 固定',
        'eq_100': '100% 股 (00981A)',
        'bd_100': '100% 債 (00988B)',
    }

    # Find best/worst for highlighting
    best_ret = max(metrics[k]['total_return'] for k in keys)
    best_sharpe = max(metrics[k]['sharpe'] for k in keys)

    for k in keys:
        m = metrics[k]
        is_strategy = k in ('A_slope', 'B_position')
        cls = ' class="highlight"' if is_strategy else ''
        ret_cls = ' class="best"' if m['total_return'] == best_ret else ''
        sharpe_cls = ' class="best"' if m['sharpe'] == best_sharpe else ''
        rows.append(f'''<tr{cls}>
  <td>{labels[k]}</td>
  <td{ret_cls}>{m['total_return']:+.2f}%</td>
  <td>{m['max_drawdown']:.2f}%</td>
  <td{sharpe_cls}>{m['sharpe']:.2f}</td>
  <td>{m['trades']}</td>
</tr>''')

    return f'''<table class="metrics-table">
  <thead><tr><th>策略</th><th>總報酬</th><th>最大回撤</th><th>Sharpe</th><th>交易次數</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>'''


def render_html(results: dict) -> str:
    """Assemble full backtest HTML."""
    dates = results['dates']
    values = results['values']
    metrics = results['metrics']
    n = len(dates)

    # Trade details for strategies A and B
    trades_a = [(dates[i], results['alloc_a'][i - 1], results['alloc_a'][i])
                for i in range(1, n) if results['alloc_a'][i] != results['alloc_a'][i - 1]]
    trades_b = [(dates[i], results['alloc_b'][i - 1], results['alloc_b'][i])
                for i in range(1, n) if results['alloc_b'][i] != results['alloc_b'][i - 1]]

    def trades_html(trades, name):
        if not trades:
            return f'<p class="meta">{name}: 無交易</p>'
        items = ''.join(f'<li>{d}: {old:.0f}% → {new:.0f}%</li>' for d, old, new in trades[-20:])
        more = f'<li class="meta">... 共 {len(trades)} 次（顯示最近 20 次）</li>' if len(trades) > 20 else ''
        return f'<p><strong>{name}（{len(trades)} 次交易）：</strong></p><ul style="font-size:.85em">{more}{items}</ul>'

    html = ['<!DOCTYPE html>', '<html lang="zh-Hant"><head>',
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>Rotation Backtest</title>',
            f'<style>{CSS}</style>',
            '</head><body>',
            '<h1>股債輪動策略回測</h1>',
            f'<p class="meta">{dates[0]} ~ {dates[-1]}（{n} 個交易日）| 起始檔位: 50/50</p>',
            '',
            '<h2>累積報酬</h2>',
            '<div class="chart-container">',
            render_line_chart(values, dates),
            '</div>',
            '',
            '<h2>股票配置變化</h2>',
            '<div class="chart-container">',
            render_alloc_chart(results['alloc_a'], results['alloc_b'], dates),
            '</div>',
            '',
            '<h2>績效指標</h2>',
            render_metrics_table(metrics),
            '',
            '<h2>交易明細</h2>',
            trades_html(trades_a, 'A: Slope Step'),
            trades_html(trades_b, 'B: Position Map'),
            '',
            '</body></html>']

    return '\n'.join(html)


# ── Main ───────────────────────────────────────────────────────────────
def filter_by_date(dates, closes, start=None, end=None):
    """Filter data by date range (inclusive). Returns (dates, closes)."""
    if start is None and end is None:
        return dates, closes
    idx_start = 0
    idx_end = len(dates)
    if start:
        for i, d in enumerate(dates):
            if d >= start:
                idx_start = i
                break
    if end:
        for i in range(len(dates) - 1, -1, -1):
            if dates[i] <= end:
                idx_end = i + 1
                break
    return dates[idx_start:idx_end], closes[idx_start:idx_end]


def main():
    p = argparse.ArgumentParser(description='Backtest rotation strategies')
    p.add_argument('--out', default='docs/backtest.html', help='Output HTML path')
    p.add_argument('--start', default=None, help='Start date (YYYY-MM-DD)')
    p.add_argument('--end', default=None, help='End date (YYYY-MM-DD)')
    args = p.parse_args()

    print(f'[Backtest] Fetching {EQUITY_TICKER}...')
    eq_data = fetch_daily(EQUITY_TICKER)
    if not eq_data:
        print('[FATAL] Cannot fetch equity data', file=sys.stderr)
        sys.exit(1)
    eq_dates, eq_closes = eq_data

    time.sleep(1)
    print(f'[Backtest] Fetching {BOND_TICKER}...')
    bd_data = fetch_daily(BOND_TICKER)
    if not bd_data:
        print('[FATAL] Cannot fetch bond data', file=sys.stderr)
        sys.exit(1)
    bd_dates, bd_closes = bd_data

    # Date filtering
    if args.start or args.end:
        eq_dates, eq_closes = filter_by_date(eq_dates, eq_closes, args.start, args.end)
        bd_dates, bd_closes = filter_by_date(bd_dates, bd_closes, args.start, args.end)
        print(f'[Backtest] Date range: {eq_dates[0]} ~ {eq_dates[-1]} ({len(eq_dates)} days)')

    # Align: use min common length
    n = min(len(eq_dates), len(bd_dates))
    eq_dates, eq_closes = eq_dates[:n], eq_closes[:n]
    bd_dates, bd_closes = bd_dates[:n], bd_closes[:n]
    print(f'[Backtest] Running backtest on {n} days...')

    results = run_backtest(eq_closes, bd_closes, eq_dates)

    print(f'[Backtest] Results:')
    for key in ('A_slope', 'B_position', 'fixed_50', 'eq_100', 'bd_100'):
        m = results['metrics'][key]
        print(f"  {key:12s} | ret: {m['total_return']:+.2f}% | maxDD: {m['max_drawdown']:.2f}% | sharpe: {m['sharpe']:.2f} | trades: {m['trades']}")

    html = render_html(results)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding='utf-8')
    print(f'[Backtest] HTML → {args.out}')
    print('[DONE]')


if __name__ == '__main__':
    main()
