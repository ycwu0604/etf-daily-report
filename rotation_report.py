"""
ETF 股債輪動報告
=================
Fetches 1y daily prices for equity (00981A) and bond (00988B) ETFs,
calculates 20-day slope, compares with hysteresis, and determines
the target allocation level (0-10, 10% per step).

Outputs:
  - docs/rotation.html : visual allocation bar + data + history
  - rotation_signal.json : machine-readable signal for future trading system

Usage:
    python rotation_report.py --db Ezmoney/etf_data.db --out docs/rotation.html
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Config ─────────────────────────────────────────────────────────────
EQUITY_CODE = "00981A"
EQUITY_NAME = "元大全球高檔股票"
BOND_CODE = "00988B"
BOND_NAME = "國泰彭博亞債"
SLOPE_WINDOW = 20
HYSTERESIS = 0.2  # percentage points — minimum slope diff to trigger move

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ── Price Fetching ─────────────────────────────────────────────────────
def fetch_closes(ticker: str) -> list[float] | None:
    """Fetch 1y daily close prices. Returns list of floats or None on failure."""
    url = YAHOO_CHART_URL.format(ticker=ticker)
    params = {"range": "1y", "interval": "1d"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if r.status_code == 429:
            time.sleep(5)
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if "chart" not in data or not data["chart"].get("result"):
            return None
        result = data["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        # Filter None
        closes = [c for c in closes if c is not None]
        return closes if len(closes) >= SLOPE_WINDOW + 1 else None
    except Exception as e:
        print(f"  [ERR] {ticker}: {e}")
        return None


def fetch_with_fallback(code: str) -> list[float] | None:
    """Try .TW, .TWO, then no suffix. Handles TWSE and TPEx listings."""
    for suffix in (".TW", ".TWO", ""):
        ticker = f"{code}{suffix}"
        print(f"  Trying {ticker}...")
        closes = fetch_closes(ticker)
        if closes:
            print(f"  OK: {len(closes)} data points")
            return closes
    return None


# ── Slope & Signal Logic ────────────────────────────────────────────
def calc_slope(closes: list[float], window: int = SLOPE_WINDOW) -> float:
    """Percentage change over `window` days."""
    if len(closes) < window + 1:
        return 0.0
    current = closes[-1]
    past = closes[-1 - window]
    if past == 0:
        return 0.0
    return (current / past - 1) * 100


def determine_target(current_level: int, equity_slope: float, bond_slope: float) -> tuple[int, str]:
    """
    Compare slopes with hysteresis. Returns (target_level, reason).
    Level 0 = 0% equity / 100% bond
    Level 10 = 100% equity / 0% bond
    """
    diff = equity_slope - bond_slope

    if diff > HYSTERESIS:
        target = min(current_level + 1, 10)
        if target != current_level:
            return target, f"股斜率領先 {diff:+.2f}% > +{HYSTERESIS}% → 加股 10%"
        return current_level, "股較強但已滿倉 (100% 股)"
    elif diff < -HYSTERESIS:
        target = max(current_level - 1, 0)
        if target != current_level:
            return target, f"債斜率領先 {abs(diff):+.2f}% > +{HYSTERESIS}% → 加債 10%"
        return current_level, "債較強但已滿倉 (100% 債)"
    else:
        return current_level, f"斜率差 {diff:+.2f}% 在死區 ±{HYSTERESIS}% 內，維持現狀"


# ── DB Operations ──────────────────────────────────────────────────────
def init_rotation_table(con: sqlite3.Connection):
    con.execute("""
        CREATE TABLE IF NOT EXISTS rotation_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL UNIQUE,
            level       INTEGER NOT NULL,
            equity_pct  INTEGER NOT NULL,
            bond_pct    INTEGER NOT NULL,
            equity_slope REAL,
            bond_slope  REAL,
            action      TEXT,
            reason      TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    con.commit()


def get_current_level(con: sqlite3.Connection) -> int:
    """Get most recent level. Default 5 (50/50) if no history."""
    row = con.execute(
        "SELECT level FROM rotation_history ORDER BY date DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else 5


def record_level(con: sqlite3.Connection, date: str, level: int, eq_slope: float, bd_slope: float, action: str, reason: str):
    con.execute(
        "INSERT OR REPLACE INTO rotation_history (date, level, equity_pct, bond_pct, equity_slope, bond_slope, action, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (date, level, level * 10, (10 - level) * 10, eq_slope, bd_slope, action, reason),
    )
    con.commit()


def get_history(con: sqlite3.Connection, limit: int = 15) -> list[dict]:
    rows = con.execute(
        """SELECT date, level, equity_pct, bond_pct, equity_slope, bond_slope, action, reason
           FROM rotation_history ORDER BY date DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        {"date": r[0], "level": r[1], "equity_pct": r[2], "bond_pct": r[3],
             "equity_slope": r[4], "bond_slope": r[5], "action": r[6], "reason": r[7]}
        for r in rows
    ]


# ── HTML Rendering ─────────────────────────────────────────────────────
CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
       max-width: 800px; margin: 1.5em auto; padding: 0 1em; line-height: 1.6; }
h1 { font-size: 1.5em; border-bottom: 2px solid #888; padding-bottom: .3em; }
.meta { color: #555; font-size: .9em; }

/* Allocation Bar */
.bar-container { margin: 1.5em 0; }
.bar-labels { display: flex; justify-content: space-between; font-size: .9em; font-weight: 600; margin-bottom: .3em; }
.bar-label-eq { color: #c62828; }
.bar-label-bd { color: #1565c0; }
.bar-track { position: relative; height: 40px; background: #e0e0e0; border-radius: 6px; overflow: visible; }
.bar-fill { position: absolute; top: 0; height: 100%; border-radius: 6px 0 0 6px;
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; font-size: .9em; color: #fff; transition: width .3s; }
.bar-fill.equity { background: linear-gradient(90deg, #ef5350, #c62828); }
.bar-fill.bond { background: linear-gradient(90deg, #42a5f5, #1565c0); border-radius: 0 6px 6px 0; }
.bar-ticks { position: relative; height: 20px; margin-top: 2px; }
.bar-tick { position: absolute; top: 0; width: 1px; height: 8px; background: #999; }
.bar-tick-label { position: absolute; top: 8px; font-size: .65em; color: #666; transform: translateX(-50%); }
.bar-marker { position: absolute; top: -4px; width: 2px; height: 48px; z-index: 2; }
.bar-marker.current { background: #ff6f00; }
.bar-marker.target { background: #7b1fa2; }
.bar-legend { display: flex; gap: 1.5em; margin-top: .5em; font-size: .85em; }
.legend-item { display: flex; align-items: center; gap: .3em; }
.legend-dot { width: 12px; height: 12px; border-radius: 2px; }

/* Data Cards */
.data-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1em; margin: 1em 0; }
.data-card { background: #f9f9f9; border: 1px solid #e0e0e0; border-radius: 8px; padding: .8em; }
.data-card h3 { margin: 0 0 .4em; font-size: .9em; }
.data-card .value { font-size: 1.3em; font-weight: 700; }
.data-card .slope-pos { color: #c62828; }
.data-card .slope-neg { color: #2e7d32; }

/* Action Box */
.action-box { background: #fff3e0; border: 1px solid #ffcc02; border-radius: 8px;
             padding: .8em 1em; margin: 1em 0; font-size: 1em; }
.action-box.hold { background: #f5f5f5; border-color: #ccc; }
.action-box .action-title { font-weight: 700; font-size: 1.1em; }
.action-box .action-detail { font-size: .9em; margin-top: .3em; }

/* History Table */
.history-table { width: 100%; border-collapse: collapse; margin-top: 1em; font-size: .85em; }
.history-table th { background: #f5f5f5; padding: .4em .5em; text-align: left; border-bottom: 2px solid #ddd; }
.history-table td { padding: .35em .5em; border-bottom: 1px solid #eee; }
.level-up { color: #c62828; font-weight: 600; }
.level-down { color: #1565c0; font-weight: 600; }
.level-hold { color: #757575; }

/* Dark mode */
@media (prefers-color-scheme: dark) {
  body { background: #1a1a1a; color: #ddd; }
  h1 { border-color: #555; }
  .meta { color: #999; }
  .bar-track { background: #333; }
  .data-card { background: #2a2a2a; border-color: #444; }
  .action-box { background: #2d2a1e; border-color: #6d5b00; }
  .action-box.hold { background: #2a2a2a; border-color: #555; }
  .history-table th { background: #2a2a2a; color: #ccc; border-color: #444; }
  .history-table td { border-color: #333; }
  .bar-tick { background: #777; }
  .bar-tick-label { color: #aaa; }
}

/* Mobile */
@media (max-width: 600px) {
  .data-grid { grid-template-columns: 1fr; }
  .bar-fill { font-size: .7em; }
}
"""


def render_bar(current_level: int, target_level: int) -> str:
    """Render the allocation bar HTML."""
    cur_pct = current_level * 10
    tgt_pct = target_level * 10

    # Bar fill (equity portion)
    bar = '<div class="bar-track">'
    bar += f'<div class="bar-fill equity" style="width:{cur_pct}%">{cur_pct}% 股</div>'
    bar += f'<div class="bar-fill bond" style="left:{cur_pct}%;width:{100 - cur_pct}">{100 - cur_pct}% 債</div>'

    # Current marker
    if cur_pct > 0 and cur_pct < 100:
        bar += f'<div class="bar-marker current" style="left:{cur_pct}%"></div>'

    # Target marker (only if different from current)
    if tgt_pct != cur_pct:
        bar += f'<div class="bar-marker target" style="left:{tgt_pct}%"></div>'

    bar += '</div>'

    # Tick marks
    ticks = '<div class="bar-ticks">'
    for i in range(0, 11):
        ticks += f'<div class="bar-tick" style="left:{i * 10}%"></div>'
        ticks += f'<div class="bar-tick-label" style="left:{i * 10}%">{i * 10}</div>'
    ticks += '</div>'

    # Legend
    legend = '<div class="bar-legend">'
    legend += '<div class="legend-item"><div class="legend-dot" style="background:#ff6f00"></div>目前</div>'
    if tgt_pct != cur_pct:
        legend += '<div class="legend-item"><div class="legend-dot" style="background:#7b1fa2"></div>目標</div>'
    legend += '</div>'

    return f'<div class="bar-container">{bar}{ticks}{legend}</div>'


def render_data_cards(eq_price: float, eq_slope: float, bd_price: float, bd_slope: float) -> str:
    """Render the data cards."""
    def slope_cls(v): return 'slope-pos' if v >= 0 else 'slope-neg'
    def arrow(v): return '↗' if v >= 0 else '↘'

    return f'''<div class="data-grid">
  <div class="data-card">
    <h3>{EQUITY_CODE} {EQUITY_NAME}</h3>
    <div class="value">${eq_price:.2f}</div>
    <div>20日斜率: <span class="{slope_cls(eq_slope)}">{eq_slope:+.3f}% {arrow(eq_slope)}</span></div>
  </div>
  <div class="data-card">
    <h3>{BOND_CODE} {BOND_NAME}</h3>
    <div class="value">${bd_price:.2f}</div>
    <div>20日斜率: <span class="{slope_cls(bd_slope)}">{bd_slope:+.3f}% {arrow(bd_slope)}</span></div>
  </div>
</div>'''


def render_action_box(current_level: int, target_level: int, reason: str) -> str:
    """Render the action recommendation box."""
    cur_eq = current_level * 10
    tgt_eq = target_level * 10

    if target_level != current_level:
        if target_level > current_level:
            move = f"賣出 {cur_eq - tgt_eq + 10}% 債 → 買入 {tgt_eq - cur_eq + 10}% 股"
            # Fix: target > current means adding equity
            delta = (target_level - current_level) * 10
            move = f"賣出 {delta}% 債 → 買入 {delta}% 股"
        else:
            delta = (current_level - target_level) * 10
            move = f"賣出 {delta}% 股 → 買入 {delta}% 債"
        return f'''<div class="action-box">
  <div class="action-title">⚡ 建議操作</div>
  <div class="action-detail">{move}</div>
  <div class="action-detail">理由：{reason}</div>
</div>'''
    else:
        return f'''<div class="action-box hold">
  <div class="action-title">✋ 維持現狀</div>
  <div class="action-detail">{reason}</div>
</div>'''


def render_history(history: list[dict]) -> str:
    """Render the history table."""
    if not history:
        return '<p class="meta">尚無切換紀錄。</p>'

    rows = []
    prev_level = None
    for i, h in enumerate(history):
        level = h['level']
        # Determine direction relative to previous row (older)
        if i == 0:
            cls = 'level-hold'
            arrow = '—'
        else:
            older = history[i - 1]['level']
            if level > older:
                cls = 'level-up'
                arrow = '▲'
            elif level < older:
                cls = 'level-down'
                arrow = '▼'
            else:
                cls = 'level-hold'
                arrow = '—'

        eq_slope = h['equity_slope']
        bd_slope = h['bond_slope']
        rows.append(f'''<tr>
  <td>{h['date']}</td>
  <td class="{cls}">{arrow} Level {level}</td>
  <td>{h['equity_pct']}% / {h['bond_pct']}%</td>
  <td>{eq_slope:+.3f}% / {bd_slope:+.3f}%</td>
  <td>{h['reason']}</td>
</tr>''')

    return f'''<table class="history-table">
  <thead><tr><th>日期</th><th>檔位</th><th>股/債</th><th>斜率 股/債</th><th>原因</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>'''


def render_full_html(data: dict) -> str:
    """Assemble the full HTML page."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    current = data['current_level']
    target = data['target_level']

    html = ['<!DOCTYPE html>', '<html lang="zh-Hant"><head>',
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>ETF 股債輪動</title>',
            f'<style>{CSS}</style>',
            '</head><body>',
            '<h1>ETF 股債輪動</h1>',
            f'<p class="meta">更新時間：{now}</p>',
            '',
            # Bar labels
            '<div class="bar-labels>',
            f'<span class="bar-label-eq">← {EQUITY_CODE} 股票</span>',
            f'<span class="bar-label-bd">{BOND_CODE} 債券 →</span>',
            '</div>',
            '',
            render_bar(current, target),
            '',
            render_data_cards(data['eq_price'], data['eq_slope'], data['bd_price'], data['bd_slope']),
            '',
            render_action_box(current, target, data['reason']),
            '',
            # Summary line
            f'<p><strong>目前配置：</strong>{current * 10}% 股 / {(10 - current) * 10}% 債'
            f' &nbsp;|&nbsp; <strong>目標配置：</strong>{target * 10}% 股 / {(10 - target) * 10}% 債</p>',
            '',
            '<h2>切換歷史</h2>',
            render_history(data['history']),
            '</body></html>']

    return '\n'.join(html)


# ── Main ───────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description='ETF Equity-Bond Rotation Report')
    p.add_argument('--db', required=True, help='Path to etf_data.db')
    p.add_argument('--out', required=True, help='Output HTML path')
    p.add_argument('--json', default='rotation_signal.json', help='Output JSON path')
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f'[FATAL] DB not found: {db_path}', file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(str(db_path))
    init_rotation_table(con)

    # 1. Fetch prices
    print(f'[Rotation] Fetching {EQUITY_CODE}...')
    eq_closes = fetch_with_fallback(EQUITY_CODE)
    time.sleep(1)
    print(f'[Rotation] {BOND_CODE}...')
    bd_closes = fetch_with_fallback(BOND_CODE)

    if not eq_closes:
        print(f'[FATAL] Cannot fetch {EQUITY_CODE} prices', file=sys.stderr)
        con.close()
        sys.exit(1)
    if not bd_closes:
        print(f'[FATAL] Cannot fetch {BOND_CODE} prices', file=sys.stderr)
        con.close()
        sys.exit(1)

    # 2. Calculate slopes
    eq_price = eq_closes[-1]
    bd_price = bd_closes[-1]
    eq_slope = calc_slope(eq_closes)
    bd_slope = calc_slope(bd_closes)
    print(f'  {EQUITY_CODE}: ${eq_price:.2f}, slope20 = {eq_slope:+.4f}%')
    print(f'  {BOND_CODE}: ${bd_price:.2f}, slope20 = {bd_slope:+.4f}%')

    # 3. Determine target
    current_level = get_current_level(con)
    target_level, reason = determine_target(current_level, eq_slope, bd_slope)
    print(f'  Level: {current_level} → {target_level}')
    print(f'  Reason: {reason}')

    # 3. Record in DB (always update today's entry)
    today = datetime.now().strftime('%Y-%m-%d')
    action = 'HOLD'
    if target_level > current_level:
        action = f'BUY_EQUITY_{(target_level - current_level) * 10}%'
    elif target_level < current_level:
        action = f'SELL_EQUITY_{(current_level - target_level) * 10}%'
    record_level(con, today, target_level, eq_slope, bd_slope, action, reason)

    # 4. Get history
    history = get_history(con, limit=15)
    con.close()

    # 5. Render HTML
    data = {
        'current_level': current_level,
        'target_level': target_level,
        'eq_price': eq_price,
        'bd_price': bd_price,
        'eq_slope': eq_slope,
        'bd_slope': bd_slope,
        'reason': reason,
        'history': history,
    }
    html = render_full_html(data)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding='utf-8')
    print(f'[Rotation] HTML → {args.out}')

    # 6. Write JSON signal
    signal = {
        'date': today,
        'equity': {'code': EQUITY_CODE, 'name': EQUITY_NAME, 'price': eq_price,
                    'slope20': round(eq_slope, 4)},
        'bond': {'code': BOND_CODE, 'name': BOND_NAME, 'price': bd_price,
                  'slope20': round(bd_slope, 4)},
        'current_level': current_level,
        'target_level': target_level,
        'action': action,
        'allocation': {'equity': target_level * 10, 'bond': (10 - target_level) * 10},
        'reason': reason,
    }
    json_path = Path(args.json)
    json_path.write_text(json.dumps(signal, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[Rotation] JSON → {args.json}')
    print('[DONE]')


if __name__ == '__main__':
    main()
