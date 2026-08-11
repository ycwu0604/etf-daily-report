"""
Capital ETF 每日自動化流程：API 取得持股 → 寫入 DB

搭配 Windows 工作排程器每日執行。
與 Ezmoney/run_daily.py 對稱：Ezmoney 是下載 xlsx → parse → DB，
Capital 是呼叫 API → parse → DB，共用同一個 etf_data.db。

使用方式：
  python run_daily.py                # 當月增量
  python run_daily.py --month 2026-07  # 指定月份回填
  python run_daily.py --force          # 強制覆寫已存在資料
"""

import sys
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_DIR = Path(__file__).parent
ETF_BASE_DIR = PROJECT_DIR.parent
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# DB 路徑 (與 Ezmoney 共用)
DB_PATH = ETF_BASE_DIR / "Ezmoney" / "etf_data.db"

# ====== Logging ======
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"capital_daily_{datetime.now():%Y%m%d}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def main(date_from=None, date_to=None, force=False):
    log.info("=" * 60)
    log.info("Capital ETF 每日自動化流程開始")

    # Step 1: API 取得持股 → 寫入 DB
    log.info("--- Step 1: API → DB ---")
    try:
        # 確保 DB schema
        sys.path.insert(0, str(ETF_BASE_DIR / "Ezmoney"))
        from etf_processor import init_db
        from capital_db_writer import fetch_and_write_capital, ETF_CONFIG

        db = sqlite3.connect(DB_PATH)
        init_db(db)

        fetch_and_write_capital(db, date_from=date_from, date_to=date_to, force=force)
        db.close()
    except Exception as e:
        log.error(f"API → DB 失敗：{e}")
        import traceback
        traceback.print_exc()
        return False

    log.info("Capital ETF 每日自動化流程完成 ✅")
    return True


if __name__ == "__main__":
    import argparse
    from datetime import timedelta

    parser = argparse.ArgumentParser()
    parser.add_argument("--month", help="Month (YYYY-MM)")
    parser.add_argument("--from", dest="date_from", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", help="End date (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing data")
    args = parser.parse_args()

    date_from = args.date_from
    date_to = args.date_to

    if args.month and not date_from and not date_to:
        y, m = int(args.month[:4]), int(args.month[5:7])
        date_from = f"{args.month}-01"
        if m == 12:
            last_day = 31
        else:
            last_day = (datetime(y, m + 1, 1) - timedelta(days=1)).day
        date_to = f"{args.month}-{last_day:02d}"

    success = main(date_from=date_from, date_to=date_to, force=args.force)
    if not success:
        log.error("流程失敗 ❌")
        sys.exit(1)
