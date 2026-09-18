"""
ETF Rotation Strategy (v3 - Fast Signal)
=========================================
Strategy: MA5/MA20 regime + 1-day momentum
  - Regime: MA5 vs MA20 (bull/bear)
  - Timing: 1-day momentum with 0.3% threshold
  - Bull regime: equity 50-100%
  - Bear regime: equity 0-50%

Shared between rotation_report.py and rotation_signal.py.
"""

# ── Strategy Parameters ────────────────────────────────────────────────
MA_SHORT = 5
MA_LONG = 20
MOM_WINDOW = 1
MOM_THRESHOLD = 0.3
MIN_DATA = MA_LONG + 5  # minimum closes needed before computing

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


def determine_allocation(closes: list[float]) -> dict:
    """
    Fast-signal strategy:
      Regime: MA5 vs MA20
      Timing: 1-day momentum, 0.3% threshold
      Bull: equity 50-100%, Bear: equity 0-50%

    Returns dict with allocation and indicator values.
    """
    if len(closes) < MIN_DATA:
        return {
            "equity_pct": 50, "bond_pct": 50,
            "regime": "unknown", "momentum": 0.0,
            "ma_short": 0.0, "ma_long": 0.0,
            "price": closes[-1] if closes else 0.0,
            "reason": "數據不足",
        }

    ma_s = calc_ma(closes, MA_SHORT)
    ma_l = calc_ma(closes, MA_LONG)
    mom = calc_momentum(closes, MOM_WINDOW)
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
        "regime": regime,
        "equity_pct": equity_pct,
        "bond_pct": bond_pct,
        "reason": reason,
    }


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
