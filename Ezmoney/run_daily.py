"""
ETF 每日自動化流程：下載 → 解析入庫 → 產出報表
搭配 Windows 工作排程器每日執行。

使用方式：
  python run_daily.py
"""

import sys
import logging
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_DIR = Path(__file__).parent
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ====== Logging ======
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"daily_{datetime.now():%Y%m%d}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def main():
    log.info("=" * 60)
    log.info("ETF 每日自動化流程開始")

    # Step 1: 下載
    log.info("--- Step 1: 下載 xlsx ---")
    try:
        from ezmoney_download import main as download_main
        paths = download_main()
        if not paths:
            log.error("下載全部失敗，流程中止")
            return False
    except Exception as e:
        log.error(f"下載異常：{e}")
        return False

    # Step 2: 解析入庫 + 產報表
    log.info("--- Step 2: 解析入庫 + 產出報表 ---")
    try:
        from etf_processor import init_db, process_all_xlsx, generate_report
        import sqlite3

        db_path = PROJECT_DIR / "etf_data.db"
        db = sqlite3.connect(db_path)
        init_db(db)

        # 只處理本次下載的檔案
        for p in paths:
            log.info(f"解析：{p.name}")
            from etf_processor import parse_xlsx
            # 下載腳本已用 fundCode 前綴命名，不需要 override
            parse_xlsx(p, db)

        # 產報表
        report_path = PROJECT_DIR / "reports" / f"ETF_Daily_Report_{datetime.now():%Y%m%d}.xlsx"
        generate_report(db, report_path)
        db.close()
    except Exception as e:
        log.error(f"處理異常：{e}")
        import traceback
        traceback.print_exc()
        return False

    log.info("ETF 每日自動化流程完成 ✅")
    return True


if __name__ == "__main__":
    success = main()
    if not success:
        log.error("流程失敗 ❌")
        sys.exit(1)
