"""
ETF Rotation Strategy (v3 - Fast Signal)
=========================================
Strategy: MA5/MA20 regime + 1-day momentum
  - Regime: MA5 vs MA20 (bull/bear)
  - Timing: 1-day momentum with 0.3% threshold
  - Bull regime: equity 50-100%
  - Bear regime: equity 0-50%

Shared between rotation_report.py and rotation_signal.py.

Also exposes determine_alloc_bb_regime() for the Phase 1 BB-Regime backtest.
"""

import math
from datetime import datetime, timedelta, date, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - zoneinfo ships with 3.9+
    ZoneInfo = None

# ── Strategy Parameters ────────────────────────────────────────────────
MA_SHORT = 5
MA_LONG = 20
MOM_WINDOW = 1
MOM_THRESHOLD = 0.3
MIN_DATA = MA_LONG + 5  # minimum closes needed before computing

# Exchange timezone for the bar dates, and the local hour after which a
# same-day bar is treated as a final close (not an intraday price).
EXCHANGE_TZ = "Asia/Taipei"
MARKET_CLOSE_HOUR = 14  # TWSE regular session ends 14:00 local

# ── Pairs ──────────────────────────────────────────────────────────────
PAIRS = [
    {
        "id": "00981A_00988B",
        "label": "00981A + 00988B",
        "equity_code": "00981A",
        "equity_name": "主動統一台股增長",
        "bond_code": "00988B",
        "bond_name": "玉山嚴選非投債",
    },
    {
        "id": "0050_00988B",
        "label": "0050 + 00988B",
        "equity_code": "0050",
        "equity_name": "元大台灣50",
        "bond_code": "00988B",
        "bond_name": "玉山嚴選非投債",
    },
]

# ── Core Functions ─────────────────────────────────────────────────────

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


# ── Date / session helpers ─────────────────────────────────────────────
def is_weekend(date_str: str) -> bool:
    """True if date_str (YYYY-MM-DD) is a Saturday or Sunday."""
    try:
        y, m, d = (int(x) for x in date_str.split("-"))
        return date(y, m, d).weekday() >= 5
    except Exception:
        return False


def prev_trading_day(date_str: str) -> str:
    """Previous weekday (Mon-Fri) before date_str. Skips weekends only;
    holidays are unknown here, so a missing close must be handled by callers
    as a data gap (see calc_momentum_by_date)."""
    try:
        y, m, d = (int(x) for x in date_str.split("-"))
    except Exception:
        return date_str
    prev = date(y, m, d) - timedelta(days=1)
    while prev.weekday() >= 5:  # 5=Sat, 6=Sun
        prev = prev - timedelta(days=1)
    return prev.isoformat()


def classify_session(last_date: str, now=None, tzname: str = EXCHANGE_TZ,
                     close_hour: int = MARKET_CLOSE_HOUR) -> str:
    """Return 'intraday' if the last bar is today's still-open session, else 'final'.
    `last_date` is the exchange-local date (YYYY-MM-DD) of the most recent bar."""
    if now is None:
        now = datetime.now(timezone.utc)
    if ZoneInfo is not None:
        try:
            now_local = now.astimezone(ZoneInfo(tzname))
        except Exception:
            now_local = now
    else:
        now_local = now
    try:
        y, m, d = (int(x) for x in last_date.split("-"))
        is_today = (date(y, m, d) == now_local.date())
    except Exception:
        return "final"
    return "intraday" if (is_today and now_local.hour < close_hour) else "final"


def calc_momentum_by_date(closes: list[float], dates: list[str],
                          window: int = MOM_WINDOW):
    """Date-aware 1-day momentum: close[last] / close[prev_trading_day] - 1.
    Returns (momentum_pct, ok). ok=False when the previous trading day's close
    is absent from the series (a data gap) and the nearest prior bar was used."""
    if not closes or not dates or len(closes) < 2 or len(dates) != len(closes):
        return 0.0, False
    last_close = closes[-1]
    prev_close = None
    if prev_trading_day(dates[-1]) in dates:
        prev_close = closes[dates.index(prev_trading_day(dates[-1]))]
    else:
        prev_close = closes[-2]  # gap: nearest available prior bar
        if prev_close == 0:
            return 0.0, False
        return (last_close / prev_close - 1) * 100, False
    if prev_close == 0:
        return 0.0, False
    return (last_close / prev_close - 1) * 100, True


def value_on_date(dates: list[str], closes: list[float], date_str: str):
    """Return (value, exact) for date_str. If not found, fall back to the most
    recent value on/before date_str (exact=False). Returns (None, False) if none."""
    if not dates or len(dates) != len(closes):
        return None, False
    if date_str in dates:
        return closes[dates.index(date_str)], True
    best = None
    for i, d in enumerate(dates):
        if d <= date_str:
            best = i
    if best is None:
        return None, False
    return closes[best], False


def determine_allocation(closes: list[float], dates: list[str] | None = None) -> dict:
    """
    Fast-signal strategy:
      Regime: MA5 vs MA20
      Timing: 1-day momentum, 0.3% threshold
      Bull: equity 50-100%, Bear: equity 0-50%

    If `dates` (exchange-local dates aligned to closes) is provided, momentum is
    computed against the previous *trading day* (date-aware); otherwise it falls
    back to the last two closes. Returns dict with allocation and indicator
    values; `momentum_ok` is False when a data gap forced a fallback momentum.
    """
    if len(closes) < MIN_DATA:
        return {
            "equity_pct": 50, "bond_pct": 50,
            "regime": "unknown", "momentum": 0.0,
            "ma_short": 0.0, "ma_long": 0.0,
            "price": closes[-1] if closes else 0.0,
            "momentum_ok": False,
            "reason": "數據不足",
        }

    ma_s = calc_ma(closes, MA_SHORT)
    ma_l = calc_ma(closes, MA_LONG)
    if dates and len(dates) == len(closes):
        mom, mom_ok = calc_momentum_by_date(closes, dates, MOM_WINDOW)
    else:
        mom = calc_momentum(closes, MOM_WINDOW)
        mom_ok = True
    price = closes[-1]

    regime = "bull" if ma_s > ma_l else "bear"

    if regime == "bull":
        if mom > MOM_THRESHOLD:
            equity_pct = 100
        elif mom > 0:
            equity_pct = 80
        elif mom > -MOM_THRESHOLD:
            equity_pct = 60
        else:
            equity_pct = 50
    else:
        if mom < -MOM_THRESHOLD:
            equity_pct = 0
        elif mom < 0:
            equity_pct = 20
        elif mom < MOM_THRESHOLD:
            equity_pct = 40
        else:
            equity_pct = 50

    bond_pct = 100 - equity_pct

    regime_cn = "多頭" if regime == "bull" else "空頭"
    mom_dir = "↑" if mom > 0 else "↓" if mom < 0 else "→"
    reason = f"MA{MA_SHORT} {'>' if regime == 'bull' else '<'} MA{MA_LONG} ({regime_cn}) + {MOM_WINDOW}d動能 {mom:+.2f}% {mom_dir}"

    return {
        "price": price,
        "ma_short": ma_s,
        "ma_long": ma_l,
        "momentum": mom,
        "momentum_ok": mom_ok,
        "regime": regime,
        "equity_pct": equity_pct,
        "bond_pct": bond_pct,
        "reason": reason,
    }


# ── BB-Regime Strategy (Phase 1 backtest only) ─────────────────────────
# Strategy: Regime-gated, BB-position-proportional stock/bond allocation.
# Per trading day i (closes list, 0-indexed):
#   1. Regime from MA20 slope:
#      ma20_i   = mean(closes[i-19 .. i])      (20 values ending at i)
#      ma20_prev= mean(closes[i-20 .. i-1])    (20 values ending at i-1)
#      slope    = (ma20_i - ma20_prev) / ma20_prev * 100  (percent)
#      SLOPE_THRESH = 0.1:
#        slope < -SLOPE_THRESH -> "down"
#        slope >  SLOPE_THRESH -> "up"
#        else                  -> "flat"
#   2. If regime == "down": target_equity_pct = 0.0
#   3. Else: BB-position FORWARD piecewise mapping (UNCLAMPED pos):
#      seg = closes[i-19 .. i]
#      m  = mean(seg);  sd = population std of seg
#      upper = m + 2*sd;  lower = m - 2*sd
#      pos = (close[i] - lower) / (upper - lower)
#      piecewise linear:
#        pos <= 0          -> 0
#        0    < pos <= 0.5 -> (pos/0.5)*50                  # 0%  -> 50%
#        0.5  < pos <= 1.0 -> 50 + ((pos-0.5)/0.5)*35       # 50% -> 85%
#        pos > 1.0         -> 100 if regime=="up" else 85   # above upper band
#      Guard: if sd==0 or upper==lower -> treat pos as 0.5 (-> 50%).
# Returns TARGET equity pct. No deadband (deadband lives in the backtest loop).

BB_WINDOW = 20
BB_STD = 2.0
SLOPE_THRESH = 0.1


def _pop_std(values: list[float], mean: float) -> float:
    """Population (N-denominator) standard deviation."""
    n = len(values)
    if n == 0:
        return 0.0
    var = sum((x - mean) ** 2 for x in values) / n
    return math.sqrt(var)


def determine_alloc_bb_regime(
    closes: list[float],
    i: int,
    bb_window: int = BB_WINDOW,
    bb_std: float = BB_STD,
    slope_thresh: float = SLOPE_THRESH,
) -> float:
    """
    BB-Regime strategy: regime-gated, BB-position-proportional target equity pct.

    Uses only closes[<=.i] (no look-ahead). Returns target equity percentage
    in [0, 100]. NO deadband applied — deadband + transaction cost live in
    the backtest loop.
    """
    # Need at least bb_window+1 closes (i >= bb_window) to compute ma20_prev.
    if i < bb_window or i >= len(closes):
        return 50.0

    seg_now = closes[i - bb_window + 1: i + 1]            # closes[i-19..i]
    seg_prev = closes[i - bb_window: i]                   # closes[i-20..i-1]
    if len(seg_now) != bb_window or len(seg_prev) != bb_window:
        return 50.0

    ma20_i = sum(seg_now) / bb_window
    ma20_prev = sum(seg_prev) / bb_window

    # Regime from slope (percent)
    if ma20_prev == 0:
        slope = 0.0
    else:
        slope = (ma20_i - ma20_prev) / ma20_prev * 100

    if slope < -slope_thresh:
        regime = "down"
    elif slope > slope_thresh:
        regime = "up"
    else:
        regime = "flat"

    if regime == "down":
        return 0.0

    # BB position (unclamped) on seg_now
    m = ma20_i
    sd = _pop_std(seg_now, m)
    if sd == 0:
        # Guard: flat band -> treat as middle -> 50%
        return 50.0
    upper = m + bb_std * sd
    lower = m - bb_std * sd
    if upper == lower:
        return 50.0
    pos = (closes[i] - lower) / (upper - lower)

    # Piecewise linear forward mapping
    if pos <= 0:
        target = 0.0
    elif pos <= 0.5:
        target = (pos / 0.5) * 50.0
    elif pos <= 1.0:
        target = 50.0 + ((pos - 0.5) / 0.5) * 35.0
    else:
        target = 100.0 if regime == "up" else 85.0

    return float(target)


# ── DB Helpers ─────────────────────────────────────────────────────────

def init_rotation_table(con):
    """Initialize rotation_history table with pair support. Auto-migrates from v1/v2."""
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(rotation_history)").fetchall()]
        # v1 had 'level', v2 had no 'pair'
        if cols and ('level' in cols or 'pair' not in cols):
            con.execute("DROP TABLE rotation_history")
            print("  [MIGRATION] Dropped old rotation_history schema")
    except Exception:
        pass
    con.execute("""
        CREATE TABLE IF NOT EXISTS rotation_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            pair        TEXT NOT NULL,
            equity_pct  INTEGER NOT NULL,
            bond_pct    INTEGER NOT NULL,
            price       REAL,
            ma_short    REAL,
            ma_long     REAL,
            momentum    REAL,
            regime      TEXT,
            action      TEXT,
            reason      TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(date, pair)
        )
    """)
    con.commit()


def get_latest_allocation(con, pair_id: str) -> dict | None:
    """Get most recent allocation for a specific pair."""
    row = con.execute(
        "SELECT date, equity_pct, bond_pct, price, ma_short, ma_long, momentum, regime, action, reason "
        "FROM rotation_history WHERE pair = ? ORDER BY date DESC LIMIT 1",
        (pair_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "date": row[0], "equity_pct": row[1], "bond_pct": row[2],
        "price": row[3], "ma_short": row[4], "ma_long": row[5],
        "momentum": row[6], "regime": row[7], "action": row[8], "reason": row[9],
    }


def record_allocation(con, date: str, pair_id: str, result: dict, action: str):
    """Record allocation for a pair on a given date."""
    con.execute(
        """INSERT OR REPLACE INTO rotation_history
           (date, pair, equity_pct, bond_pct, price, ma_short, ma_long, momentum, regime, action, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date, pair_id, result["equity_pct"], result["bond_pct"], result["price"],
         result["ma_short"], result["ma_long"], result["momentum"],
         result["regime"], action, result["reason"]),
    )
    con.commit()


def get_history(con, pair_id: str, limit: int = 20) -> list[dict]:
    rows = con.execute(
        """SELECT date, equity_pct, bond_pct, price, momentum, regime, action, reason
           FROM rotation_history WHERE pair = ? ORDER BY date DESC LIMIT ?""",
        (pair_id, limit),
    ).fetchall()
    return [
        {"date": r[0], "equity_pct": r[1], "bond_pct": r[2], "price": r[3],
         "momentum": r[4], "regime": r[5], "action": r[6], "reason": r[7]}
        for r in rows
    ]

