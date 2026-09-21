"""
ETF 股債輪動報告 (v3 - Fast Signal, Dual Pair)
=============================================
Strategy: MA5/MA20 regime + 1-day momentum (0.3% threshold)
Pairs: 00981A+00988B, 0050+00988B

Outputs:
  - docs/rotation.html : visual allocation bars + data + history (both pairs)
  - rotation_signal.json : machine-readable signals for future trading system

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

from rotation_strategy import (
    MA_SHORT, MA_LONG, MOM_WINDOW, MOM_THRESHOLD, MIN_DATA, PAIRS,
    calc_ma, calc_momentum, determine_allocation,
    init_rotation_table, get_latest_allocation, record_allocation, get_history,
)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
VERIFY_SSL = True  # Set False with --insecure (local corporate proxy)


# ── Price Fetching ─────────────────────────────────────────────────────
def fetch_data(ticker: str) -> dict | None:
    """Fetch 1y daily data. Returns {'dates': [...], 'closes': [...]} or None."""
    url = YAHOO_CHART_URL.format(ticker=ticker)
    params = {"range": "1y", "interval": "1d"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15, verify=VERIFY_SSL)
        if r.status_code == 429:
            time.sleep(5)
            r = requests.get(url, params=params, headers=HEADERS, timeout=15, verify=VERIFY_SSL)
        if r.status_code != 200:
            return None
        data = r.json()
        if "chart" not in data or not data["chart"].get("result"):
            return None
        result = data["chart"]["result"][0]
        ts = result["timestamp"]
        raw_closes = result["indicators"]["quote"][0]["close"]
        pairs = [(t, c) for t, c in zip(ts, raw_closes) if c is not None]
        if len(pairs) < MIN_DATA:
            return None
        from datetime import datetime as _dt, timezone as _tz
        dates = [_dt.fromtimestamp(t, tz=_tz.utc).strftime('%Y-%m-%d') for t, c in pairs]
        closes = [c for t, c in pairs]
        return {"dates": dates, "closes": closes}
    except Exception as e:
        print(f"  [ERR] {ticker}: {e}")
        return None


def fetch_with_fallback(code: str) -> dict | None:
    """Try .TW, .TWO, then no suffix. Returns {'dates': [...], 'closes': [...]} or None."""
    for suffix in (".TW", ".TWO", ""):
        ticker = f"{code}{suffix}"
        print(f"  Trying {ticker}...")
        data = fetch_data(ticker)
        if data:
            print(f"  OK: {len(data['closes'])} data points (last: {data['dates'][-1]})")
            return data
    return None


# ── HTML Rendering ─────────────────────────────────────────────────────
CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
       max-width: 900px; margin: 1.5em auto; padding: 0 1em; line-height: 1.6; }
h1 { font-size: 1.5em; border-bottom: 2px solid #888; padding-bottom: .3em; }
h2 { font-size: 1.2em; margin-top: 2em; border-bottom: 1px solid #ccc; padding-bottom: .2em; }
.meta { color: #555; font-size: .9em; }

/* Pair Tabs */
.pair-section { margin: 1.5em 0; padding: 1em; border: 1px solid #e0e0e0; border-radius: 10px; }
.pair-title { font-size: 1.1em; font-weight: 700; margin-bottom: .5em; }

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
  h2 { border-color: #444; }
  .meta { color: #999; }
  .pair-section { background: #222; border-color: #444; }
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


def render_data_cards(pair: dict, result: dict, bond_price: float) -> str:
    mom_cls = 'pos' if result['momentum'] >= 0 else 'neg'
    mom_arrow = '↑' if result['momentum'] >= 0 else '↓'
    ma_diff = result['ma_short'] - result['ma_long']
    ma_cls = 'pos' if ma_diff >= 0 else 'neg'
    return f'''<div class="data-grid">
  <div class="data-card">
    <h3>{pair["equity_code"]} 收盤</h3>
    <div class="value">${result["price"]:.2f}</div>
    <div style="font-size:.8em;color:#888">{pair["equity_name"]}</div>
  </div>
  <div class="data-card">
    <h3>MA{MA_SHORT} vs MA{MA_LONG}</h3>
    <div class="value {ma_cls}">{result["ma_short"]:.2f} / {result["ma_long"]:.2f}</div>
    <div style="font-size:.8em" class="{ma_cls}">差 {ma_diff:+.3f}</div>
  </div>
  <div class="data-card">
    <h3>{MOM_WINDOW}日動能</h3>
    <div class="value {mom_cls}">{result["momentum"]:+.3f}% {mom_arrow}</div>
    <div style="font-size:.8em;color:#888">{pair["bond_code"]}: ${bond_price:.2f}</div>
  </div>
</div>'''


def render_action(current_alloc: dict | None, new_alloc: dict) -> str:
    if current_alloc is None or current_alloc["equity_pct"] == new_alloc["equity_pct"]:
        return f'''<div class="action-box hold">
  <div class="action-title">✋ 目前 {new_alloc["equity_pct"]}% 股 / {new_alloc["bond_pct"]}% 債</div>
  <div class="action-detail">{new_alloc["reason"]}</div>
</div>'''
    old_pct = current_alloc["equity_pct"]
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


def render_history(history: list[dict]) -> str:
    if not history:
        return '<p class="meta">尚無紀錄。</p>'
    rows = []
    for i, h in enumerate(history):
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


def render_pair_section(pair: dict, result: dict, current_alloc: dict | None,
                        bond_price: float, history: list[dict]) -> str:
    """Render one pair's full section."""
    regime_cls = 'regime-bull' if result['regime'] == 'bull' else 'regime-bear'
    regime_cn = '多頭' if result['regime'] == 'bull' else '空頭'
    parts = [
        '<div class="pair-section">',
        f'<div class="pair-title">{pair["label"]}</div>',
        f'<span class="regime-badge {regime_cls}">{regime_cn}</span>',
        '<div class="bar-labels">',
        f'<span class="bar-label-eq">← {pair["equity_code"]} {pair["equity_name"]}</span>',
        f'<span class="bar-label-bd">{pair["bond_code"]} {pair["bond_name"]} →</span>',
        '</div>',
        render_bar(result['equity_pct']),
        render_data_cards(pair, result, bond_price),
        render_action(current_alloc, result),
        '<h2 style="font-size:1em;border:none;margin:.5em 0 0">歷史</h2>',
        render_history(history),
        '</div>',
    ]
    return '\n'.join(parts)


def render_full_html(pair_results: list[dict]) -> str:
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    html = ['<!DOCTYPE html>', '<html lang="zh-Hant"><head>',
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>ETF 股債輪動</title>',
            f'<style>{CSS}</style>',
            '</head><body>',
            '<h1>ETF 股債輪動</h1>',
            f'<p class="meta">更新：{now} | MA{MA_SHORT}/MA{MA_LONG} + {MOM_WINDOW}d動能 {MOM_THRESHOLD}%</p>',
            '']
    for pr in pair_results:
        html.append(render_pair_section(
            pr["pair"], pr["result"], pr["current"], pr["bond_price"], pr["history"]))
    html.append('</body></html>')
    return '\n'.join(html)


# ── Main ───────────────────────────────────────────────────────────────
def main():
    global VERIFY_SSL
    p = argparse.ArgumentParser(description='ETF Equity-Bond Rotation (Fast Signal, Dual Pair)')
    p.add_argument('--db', required=True, help='Path to etf_data.db')
    p.add_argument('--out', required=True, help='Output HTML path')
    p.add_argument('--json', default='rotation_signal.json', help='Output JSON path')
    p.add_argument('--insecure', action='store_true', help='Skip SSL verification (local corporate proxy)')
    args = p.parse_args()
    if args.insecure:
        VERIFY_SSL = False
        requests.packages.urllib3.disable_warnings()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f'[FATAL] DB not found: {db_path}', file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(str(db_path))
    init_rotation_table(con)

    # Fetch prices for all unique ETFs
    all_codes = set()
    for pair in PAIRS:
        all_codes.add(pair["equity_code"])
        all_codes.add(pair["bond_code"])

    print(f'[Rotation] Fetching {len(all_codes)} ETFs: {sorted(all_codes)}')
    price_cache = {}
    for code in sorted(all_codes):
        print(f'  Fetching {code}...')
        price_cache[code] = fetch_with_fallback(code)
        time.sleep(0.5)

    # Process each pair
    pair_results = []
    signals = []

    for pair in PAIRS:
        eq_data = price_cache.get(pair["equity_code"])
        bd_data = price_cache.get(pair["bond_code"])

        if not eq_data:
            print(f'  [FATAL] Cannot fetch {pair["equity_code"]}', file=sys.stderr)
            continue

        eq_closes = eq_data["closes"]
        trading_date = eq_data["dates"][-1]  # Use actual trading date, not now()
        bond_price = bd_data["closes"][-1] if bd_data else 0.0
        result = determine_allocation(eq_closes)

        print(f'  [{pair["id"]}] Date: {trading_date} | Price: ${result["price"]:.2f} | '
              f'MA{MA_SHORT}: {result["ma_short"]:.3f}, MA{MA_LONG}: {result["ma_long"]:.3f} | '
              f'{MOM_WINDOW}d mom: {result["momentum"]:+.4f}% | '
              f'Regime: {result["regime"]} | {result["equity_pct"]}%/{result["bond_pct"]}%')

        # DB: compare + record (using trading_date, not datetime.now())
        current = get_latest_allocation(con, pair["id"])
        if current and current['equity_pct'] != result['equity_pct']:
            delta = result['equity_pct'] - current['equity_pct']
            action = f'BUY_EQ_{delta}%' if delta > 0 else f'SELL_EQ_{abs(delta)}%'
        else:
            action = 'HOLD'
        record_allocation(con, trading_date, pair["id"], result, action)
        history = get_history(con, pair["id"], limit=20)

        pair_results.append({
            "pair": pair, "result": result, "current": current,
            "bond_price": bond_price, "history": history,
        })

        signals.append({
            "pair_id": pair["id"],
            "label": pair["label"],
            "equity": {"code": pair["equity_code"], "name": pair["equity_name"],
                       "price": result["price"]},
            "bond": {"code": pair["bond_code"], "name": pair["bond_name"],
                     "price": bond_price},
            "indicators": {
                "ma_short": round(result["ma_short"], 4),
                "ma_long": round(result["ma_long"], 4),
                "momentum": round(result["momentum"], 4),
            },
            "regime": result["regime"],
            "target": {"equity_pct": result["equity_pct"], "bond_pct": result["bond_pct"]},
            "previous": ({"equity_pct": current["equity_pct"], "bond_pct": current["bond_pct"]}
                         if current else None),
            "action": action,
            "reason": result["reason"],
        })

    con.close()

    # Render HTML
    html = render_full_html(pair_results)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding='utf-8')
    print(f'[Rotation] HTML → {args.out}')

    # Write JSON signal
    signal = {'date': datetime.now().strftime('%Y-%m-%d %H:%M'), 'pairs': signals}
    json_path = Path(args.json)
    json_path.write_text(json.dumps(signal, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[Rotation] JSON → {args.json}')
    print('[DONE]')


if __name__ == '__main__':
    main()
