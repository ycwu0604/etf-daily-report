"""
ETF 股債輪動報告 (v2 - Dual Speed Strategy)
=============================================
Strategy: MA regime filter + short-term momentum timing
  - Regime: MA10 vs MA50 (bull/bear)
  - Timing: 3-day momentum with 0.5% threshold
  - Bull regime: equity 50-100%
  - Bear regime: equity 0-50%

Fetches 1y daily prices for equity (00981A) and bond (00988B) ETFs.

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
EQUITY_NAME = "主動統一台股增長"
BOND_CODE = "00988B"
BOND_NAME = "玉山嚴選非投債"

MA_SHORT = 10       # regime: short MA
MA_LONG = 50        # regime: long MA
MOM_WINDOW = 3      # timing: momentum lookback (days)
MOM_THRESHOLD = 0.5 # timing: momentum threshold (%)

MIN_DATA = MA_LONG + 5  # minimum data points needed

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
        closes = [c for c in closes if c is not None]
        return closes if len(closes) >= MIN_DATA else None
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


# ── Indicators ─────────────────────────────────────────────────────────
def calc_ma(closes: list[float], window: int) -> float:
    """Simple moving average of last `window` closes."""
    if len(closes) < window:
        return 0.0
    return sum(closes[-window:]) / window


def calc_momentum(closes: list[float], window: int = MOM_WINDOW) -> float:
    """Percentage change over `window` days."""
    if len(closes) < window + 1:
        return 0.0
    current = closes[-1]
    past = closes[-1 - window]
    if past == 0:
        return 0.0
    return (current / past - 1) * 100


# ── Strategy Logic ─────────────────────────────────────────────────────
def determine_allocation(closes: list[float]) -> dict:
    """
    Dual-speed strategy:
      Regime: MA10 vs MA50
      Timing: 3-day momentum
      Bull: equity 50-100%, Bear: equity 0-50%
    """
    ma_s = calc_ma(closes, MA_SHORT)
    ma_l = calc_ma(closes, MA_LONG)
    mom = calc_momentum(closes, MOM_WINDOW)
    price = closes[-1]

    regime = "bull" if ma_s > ma_l else "bear"

    if regime == "bull":
        # Equity bias: 50-100%
        if mom > MOM_THRESHOLD:
            equity_pct = 100
        elif mom > 0:
            equity_pct = 80
        elif mom > -MOM_THRESHOLD:
            equity_pct = 60
        else:
            equity_pct = 50
    else:
        # Bond bias: 0-50%
        if mom < -MOM_THRESHOLD:
            equity_pct = 0
        elif mom < 0:
            equity_pct = 20
        elif mom < MOM_THRESHOLD:
            equity_pct = 40
        else:
            equity_pct = 50

    bond_pct = 100 - equity_pct

    # Build reason
    regime_cn = "多頭" if regime == "bull" else "空頭"
    mom_dir = "↑" if mom > 0 else "↓" if mom < 0 else "→"
    reason = f"MA{MA_SHORT} {'>' if regime == 'bull' else '<'} MA{MA_LONG} ({regime_cn}) + {MOM_WINDOW}d動能 {mom:+.2f}% {mom_dir}"

    return {
        "price": price,
        "ma_short": ma_s,
        "ma_long": ma_l,
        "momentum": mom,
        "regime": regime,
        "equity_pct": equity_pct,
        "bond_pct": bond_pct,
        "reason": reason,
    }


# ── DB Operations ──────────────────────────────────────────────────────
def init_rotation_table(con: sqlite3.Connection):
    # Auto-migration: drop old schema if it exists (v1 had 'level' and 'slope' columns)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(rotation_history)").fetchall()]
        if cols and 'level' in cols:
            con.execute("DROP TABLE rotation_history")
            print("  [MIGRATION] Dropped old rotation_history (v1 schema)")
    except Exception:
        pass
    con.execute("""
        CREATE TABLE IF NOT EXISTS rotation_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL UNIQUE,
            equity_pct  INTEGER NOT NULL,
            bond_pct    INTEGER NOT NULL,
            price       REAL,
            ma_short    REAL,
            ma_long     REAL,
            momentum    REAL,
            regime      TEXT,
            action      TEXT,
            reason      TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    con.commit()


def get_current_allocation(con: sqlite3.Connection) -> dict | None:
    """Get most recent allocation. Returns None if no history."""
    row = con.execute(
        "SELECT date, equity_pct, bond_pct, price, ma_short, ma_long, momentum, regime, action, reason "
        "FROM rotation_history ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {
        "date": row[0], "equity_pct": row[1], "bond_pct": row[2],
        "price": row[3], "ma_short": row[4], "ma_long": row[5],
        "momentum": row[6], "regime": row[7], "action": row[8], "reason": row[9],
    }


def record_allocation(con: sqlite3.Connection, date: str, result: dict, action: str):
    con.execute(
        """INSERT OR REPLACE INTO rotation_history
           (date, equity_pct, bond_pct, price, ma_short, ma_long, momentum, regime, action, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date, result["equity_pct"], result["bond_pct"], result["price"],
         result["ma_short"], result["ma_long"], result["momentum"],
         result["regime"], action, result["reason"]),
    )
    con.commit()


def get_history(con: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = con.execute(
        """SELECT date, equity_pct, bond_pct, price, momentum, regime, action, reason
           FROM rotation_history ORDER BY date DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        {"date": r[0], "equity_pct": r[1], "bond_pct": r[2], "price": r[3],
         "momentum": r[4], "regime": r[5], "action": r[6], "reason": r[7]}
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
.bar-fill { position: absolute; top: 0; height: 100%;
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; font-size: .9em; color: #fff; }
.bar-fill.equity { background: linear-gradient(90deg, #ef5350, #c62828); border-radius: 6px 0 0 6px; }
.bar-fill.bond { background: linear-gradient(90deg, #42a5f5, #1565c0); border-radius: 0 6px 6px 0; }
.bar-ticks { position: relative; height: 20px; margin-top: 2px; }
.bar-tick { position: absolute; top: 0; width: 1px; height: 8px; background: #999; }
.bar-tick-label { position: absolute; top: 8px; font-size: .65em; color: #666; transform: translateX(-50%); }

/* Regime Badge */
.regime-badge { display: inline-block; padding: .2em .8em; border-radius: 12px;
                font-weight: 700; font-size: .9em; margin: .5em 0; }
.regime-bull { background: #ffcdd2; color: #b71c1c; }
.regime-bear { background: #c8e6c9; color: #1b5e20; }

/* Data Cards */
.data-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: .8em; margin: 1em 0; }
.data-card { background: #f9f9f9; border: 1px solid #e0e0e0; border-radius: 8px; padding: .7em; }
.data-card h3 { margin: 0 0 .3em; font-size: .8em; color: #666; }
.data-card .value { font-size: 1.2em; font-weight: 700; }
.pos { color: #c62828; }
.neg { color: #2e7d32; }

/* Action Box */
.action-box { background: #fff3e0; border: 1px solid #ffcc02; border-radius: 8px;
             padding: .8em 1em; margin: 1em 0; }
.action-box.hold { background: #f5f5f5; border-color: #ccc; }
.action-box .action-title { font-weight: 700; font-size: 1.1em; }
.action-box .action-detail { font-size: .9em; margin-top: .3em; color: #555; }

/* History Table */
.history-table { width: 100%; border-collapse: collapse; margin-top: 1em; font-size: .85em; }
.history-table th { background: #f5f5f5; padding: .4em .5em; text-align: left; border-bottom: 2px solid #ddd; }
.history-table td { padding: .35em .5em; border-bottom: 1px solid #eee; }
.up { color: #c62828; font-weight: 600; }
.down { color: #1565c0; font-weight: 600; }
.hold { color: #757575; }

/* Dark mode */
@media (prefers-color-scheme: dark) {
  body { background: #1a1a1a; color: #ddd; }
  h1 { border-color: #555; }
  .meta { color: #999; }
  .bar-track { background: #333; }
  .data-card { background: #2a2a2a; border-color: #444; }
  .action-box { background: #2d2a1e; border-color: #6d5b00; }
  .action-box.hold { background: #2a2a2a; border-color: #555; }
  .action-box .action-detail { color: #aaa; }
  .history-table th { background: #2a2a2a; color: #ccc; border-color: #444; }
  .history-table td { border-color: #333; }
  .bar-tick { background: #777; }
  .bar-tick-label { color: #aaa; }
  .regime-bull { background: #3d1a1a; color: #ef9a9a; }
  .regime-bear { background: #1a3d1a; color: #a5d6a7; }
}

/* Mobile */
@media (max-width: 600px) {
  .data-grid { grid-template-columns: 1fr; }
  .bar-fill { font-size: .7em; }
}
"""


def render_bar(equity_pct: int) -> str:
    """Render the allocation bar."""
    bond_pct = 100 - equity_pct
    bar = '<div class="bar-track">'
    if equity_pct > 0:
        bar += f'<div class="bar-fill equity" style="left:0%;width:{equity_pct}%">{equity_pct}% 股</div>'
    if bond_pct > 0:
        bar += f'<div class="bar-fill bond" style="left:{equity_pct}%;width:{bond_pct}%">{bond_pct}% 債</div>'
    bar += '</div>'

    ticks = '<div class="bar-ticks">'
    for i in range(0, 11):
        ticks += f'<div class="bar-tick" style="left:{i * 10}%"></div>'
        ticks += f'<div class="bar-tick-label" style="left:{i * 10}%">{i * 10}</div>'
    ticks += '</div>'

    return f'<div class="bar-container">{bar}{ticks}</div>'


def render_data_cards(result: dict, bond_price: float) -> str:
    """Render indicator cards."""
    mom_cls = 'pos' if result['momentum'] >= 0 else 'neg'
    mom_arrow = '↑' if result['momentum'] >= 0 else '↓'
    ma_diff = result['ma_short'] - result['ma_long']
    ma_cls = 'pos' if ma_diff >= 0 else 'neg'

    return f'''<div class="data-grid">
  <div class="data-card">
    <h3>{EQUITY_CODE} 收盤</h3>
    <div class="value">${result["price"]:.2f}</div>
    <div style="font-size:.8em;color:#888">{EQUITY_NAME}</div>
  </div>
  <div class="data-card">
    <h3>MA{MA_SHORT} vs MA{MA_LONG}</h3>
    <div class="value {ma_cls}">{result["ma_short"]:.2f} / {result["ma_long"]:.2f}</div>
    <div style="font-size:.8em" class="{ma_cls}">差 {ma_diff:+.3f}</div>
  </div>
  <div class="data-card">
    <h3>{MOM_WINDOW}日動能</h3>
    <div class="value {mom_cls}">{result["momentum"]:+.3f}% {mom_arrow}</div>
    <div style="font-size:.8em;color:#888">{BOND_CODE}: ${bond_price:.2f}</div>
  </div>
</div>'''


def render_action(current_alloc: dict | None, new_alloc: dict) -> str:
    """Render the action recommendation."""
    if current_alloc is None or current_alloc["equity_pct"] != new_alloc["equity_pct"]:
        old_pct = current_alloc["equity_pct"] if current_alloc else 50
        new_pct = new_alloc["equity_pct"]
        delta = new_pct - old_pct
        if delta > 0:
            action_text = f"買入 {delta}% 股 / 賣出 {delta}% 債"
        else:
            action_text = f"賣出 {abs(delta)}% 股 / 買入 {abs(delta)}% 債"
        return f'''<div class="action-box">
  <div class="action-title">⚡ 調整配置: {old_pct}% → {new_pct}% 股</div>
  <div class="action-detail">{action_text}</div>
  <div class="action-detail">{new_alloc["reason"]}</div>
</div>'''
    else:
        return f'''<div class="action-box hold">
  <div class="action-title">✋ 維持 {new_alloc["equity_pct"]}% 股 / {new_alloc["bond_pct"]}% 債</div>
  <div class="action-detail">{new_alloc["reason"]}</div>
</div>'''


def render_history(history: list[dict]) -> str:
    """Render history table."""
    if not history:
        return '<p class="meta">尚無紀錄。</p>'

    rows = []
    for i, h in enumerate(history):
        # Compare with previous (older) entry
        if i == 0:
            cls, arrow = 'hold', '—'
        else:
            if h['equity_pct'] > history[i - 1]['equity_pct']:
                cls, arrow = 'up', '▲'
            elif h['equity_pct'] < history[i - 1]['equity_pct']:
                cls, arrow = 'down', '▼'
            else:
                cls, arrow = 'hold', '—'

        regime_badge = '🔴' if h['regime'] == 'bull' else '🟢'
        mom_cls = 'pos' if (h['momentum'] or 0) >= 0 else 'neg'

        rows.append(f'''<tr>
  <td>{h['date']}</td>
  <td class="{cls}">{arrow} {h['equity_pct']}/{h['bond_pct']}</td>
  <td>${h['price']:.2f}</td>
  <td class="{mom_cls}">{h['momentum']:+.2f}%</td>
  <td>{regime_badge}</td>
</tr>''')

    return f'''<table class="history-table">
  <thead><tr><th>日期</th><th>股/債</th><th>收盤</th><th>{MOM_WINDOW}d動能</th><th>Regime</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>'''


def render_full_html(result: dict, current_alloc: dict | None, bond_price: float, history: list[dict]) -> str:
    """Assemble the full HTML page."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    regime_cls = 'regime-bull' if result['regime'] == 'bull' else 'regime-bear'
    regime_cn = '多頭' if result['regime'] == 'bull' else '空頭'

    html = ['<!DOCTYPE html>', '<html lang="zh-Hant"><head>',
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>ETF 股債輪動</title>',
            f'<style>{CSS}</style>',
            '</head><body>',
            '<h1>ETF 股債輪動</h1>',
            f'<p class="meta">更新：{now} | MA{MA_SHORT}/MA{MA_LONG} + {MOM_WINDOW}d動能</p>',
            '',
            # Regime badge
            f'<span class="regime-badge {regime_cls}">{regime_cn} ({result["regime"]})</span>',
            '',
            # Bar
            '<div class="bar-labels">',
            f'<span class="bar-label-eq">← {EQUITY_CODE} 股</span>',
            f'<span class="bar-label-bd">{BOND_CODE} 債 →</span>',
            '</div>',
            render_bar(result['equity_pct']),
            '',
            # Data cards
            render_data_cards(result, bond_price),
            '',
            # Action
            render_action(current_alloc, result),
            '',
            # History
            '<h2>配置歷史</h2>',
            render_history(history),
            '</body></html>']

    return '\n'.join(html)


# ── Main ───────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description='ETF Equity-Bond Rotation (Dual Speed)')
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
    print(f'[Rotation] Fetching {BOND_CODE}...')
    bd_closes = fetch_with_fallback(BOND_CODE)

    if not eq_closes:
        print(f'[FATAL] Cannot fetch {EQUITY_CODE} prices', file=sys.stderr)
        con.close()
        sys.exit(1)

    bond_price = bd_closes[-1] if bd_closes else 0.0
    if not bd_closes:
        print(f'  [WARN] Cannot fetch {BOND_CODE}, using 0 for display')

    # 2. Calculate allocation
    result = determine_allocation(eq_closes)
    print(f"  Price: ${result['price']:.2f}")
    print(f"  MA{MA_SHORT}: {result['ma_short']:.3f}, MA{MA_LONG}: {result['ma_long']:.3f}")
    print(f"  {MOM_WINDOW}d Momentum: {result['momentum']:+.4f}%")
    print(f"  Regime: {result['regime']}")
    print(f"  Allocation: {result['equity_pct']}% equity / {result['bond_pct']}% bond")
    print(f"  Reason: {result['reason']}")

    # 3. Record in DB
    today = datetime.now().strftime('%Y-%m-%d')
    current = get_current_allocation(con)
    if current and current['equity_pct'] != result['equity_pct']:
        delta = result['equity_pct'] - current['equity_pct']
        if delta > 0:
            action = f'BUY_EQ_{delta}%'
        else:
            action = f'SELL_EQ_{abs(delta)}%'
    else:
        action = 'HOLD'
    record_allocation(con, today, result, action)

    # 4. Get history
    history = get_history(con, limit=20)
    con.close()

    # 5. Render HTML
    html = render_full_html(result, current, bond_price, history)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding='utf-8')
    print(f'[Rotation] HTML → {args.out}')

    # 6. Write JSON signal (for future trading system)
    signal = {
        'date': today,
        'equity': {'code': EQUITY_CODE, 'name': EQUITY_NAME, 'price': result['price']},
        'bond': {'code': BOND_CODE, 'name': BOND_NAME, 'price': bond_price},
        'indicators': {
            'ma_short': round(result['ma_short'], 4),
            'ma_long': round(result['ma_long'], 4),
            'momentum': round(result['momentum'], 4),
        },
        'regime': result['regime'],
        'target': {
            'equity_pct': result['equity_pct'],
            'bond_pct': result['bond_pct'],
        },
        'current': {
            'equity_pct': current['equity_pct'] if current else 50,
            'bond_pct': current['bond_pct'] if current else 50,
        } if current else None,
        'action': action,
        'reason': result['reason'],
    }
    json_path = Path(args.json)
    json_path.write_text(json.dumps(signal, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[Rotation] JSON → {args.json}')
    print('[DONE]')


if __name__ == '__main__':
    main()
