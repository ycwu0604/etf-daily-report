"""
復華投信 ETF 每日自動下載投資組合檔案
網站：https://www.fhtrust.com.tw/

流程（比 Ezmoney 更簡單，直接 GET 下載）：
1. 組合 URL：https://www.fhtrust.com.tw/api/assetsExcel/{ETF代碼}/{YYYYMMDD}
2. 直接下載 xlsx → 存檔

使用方式：
  python fuhwa_download.py
  python fuhwa_download.py --etf-codes ETF23

搭配 Windows 工作排程器或 GitHub Actions 每日自動執行。
"""

import requests
import sys
import urllib3
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ====== 設定區 ======
ETF_CODES = ["ETF23"]  # 復華 ETF 代碼（非上市代碼，是復華網站用的 ID）
BASE_URL = "https://www.fhtrust.com.tw"
SAVE_DIR = Path(__file__).parent / "downloads"

# 復華 ETF 代碼 → 上市代碼 對應
ETF_CODE_MAP = {
    "ETF23": "00991A",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/octet-stream,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*",
    "Referer": f"{BASE_URL}/ETF/etf_detail/ETF23",
}


def download_one(etf_code: str, date_str: str | None = None) -> Path | None:
    """下載單支 ETF，回傳存檔路徑或 None"""
    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    download_url = f"{BASE_URL}/api/assetsExcel/{etf_code}/{date_str}"
    listed_code = ETF_CODE_MAP.get(etf_code, etf_code)

    try:
        resp = requests.get(
            download_url,
            headers=HEADERS,
            verify=False,
            allow_redirects=True,
            timeout=60,
        )
    except requests.RequestException as e:
        print(f"  [FAIL] [{etf_code}] 下載錯誤: {e}")
        return None

    # 驗證回應是檔案
    content_type = resp.headers.get("Content-Type", "")
    content_disp = resp.headers.get("Content-Disposition", "")
    is_file = (
        "application" in content_type
        or "octet-stream" in content_type
        or "excel" in content_type
        or "spreadsheet" in content_type
        or "attachment" in content_disp
        or "filename" in content_disp
    )
    if not is_file and resp.status_code == 200:
        preview = resp.text[:200] if len(resp.text) > 200 else resp.text
        print(f"  [WARN] [{etf_code}] 回傳內容不像檔案: {preview}")
        return None

    if resp.status_code != 200:
        print(f"  [FAIL] [{etf_code}] HTTP {resp.status_code}")
        return None

    # 存檔
    filename = f"{etf_code}_ETF_Investment_Portfolio_{date_str}.xlsx"
    filepath = SAVE_DIR / filename

    with open(filepath, "wb") as f:
        f.write(resp.content)

    file_size = filepath.stat().st_size
    if file_size < 500:
        print(f"  [WARN] [{etf_code}] 檔案異常地小 ({file_size} bytes)，可能下載失敗")
        return None

    print(f"  [OK] [{listed_code}] 已儲存: {filepath.name} ({file_size:,} bytes)")
    return filepath


def main(etf_codes: list[str] | None = None, date_str: str | None = None) -> list[Path]:
    codes = etf_codes or ETF_CODES
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 開始下載 {len(codes)} 支 Fuhwa ETF...")

    results = []
    for code in codes:
        listed = ETF_CODE_MAP.get(code, code)
        print(f"\n--- 下載 {code} ({listed}) ---")
        path = download_one(code, date_str)
        if path:
            results.append(path)
        else:
            print(f"  [FAIL] {code} 下載失敗")

    print(f"\n完成: {len(results)}/{len(codes)} 支成功")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--etf-codes", nargs="+", default=None)
    parser.add_argument("--date", default=None, help="日期 YYYYMMDD，預設今天")
    args = parser.parse_args()

    paths = main(args.etf_codes, args.date)
    if not paths:
        print("\n[FAIL] 全部下載失敗")
        exit(1)
