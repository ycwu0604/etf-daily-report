"""
ezmoney 每日自動下載投資組合檔案
網站：https://www.ezmoney.com.tw/

流程（每支 ETF 獨立跑完整三步驟）：
1. 造訪首頁 → 取得初始 Cookie（建立 session）
2. 帶 Cookie 造訪投資組合頁面 → 取得 __RequestVerificationToken
3. 帶完整 Cookie + Token 下載檔案 → 存檔

使用方式：
  python ezmoney_download.py
  python ezmoney_download.py --fund-codes 49YTW 63YTW 78YTW

搭配 Windows 工作排程器每日自動執行。
"""

import requests
import re
import sys
import urllib3
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ====== 設定區 ======
FUND_CODES = ["49YTW", "63YTW"]
BASE_URL = "https://www.ezmoney.com.tw"
SAVE_DIR = Path(__file__).parent / "downloads"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}


def download_one(fund_code: str) -> Path | None:
    """下載單支 ETF，回傳存檔路徑或 None"""
    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    page_url = f"{BASE_URL}/ETF/Fund/Info?fundCode={fund_code}&tabName=asset"
    download_url = f"{BASE_URL}/ETF/Fund/AssetExcelNPOI?fundCode={fund_code}"

    session = requests.Session()
    session.headers.update(HEADERS)

    # Step 1：造訪首頁，建立 session
    resp1 = session.get(f"{BASE_URL}/", verify=False, allow_redirects=True, timeout=30)
    if resp1.status_code != 200:
        print(f"  ❌ [{fund_code}] 首頁回傳錯誤：{resp1.status_code}")
        return None

    # Step 2：造訪投資組合頁面，拿 Token
    session.headers["Referer"] = f"{BASE_URL}/"
    resp2 = session.get(page_url, verify=False, allow_redirects=True, timeout=30)
    if resp2.status_code != 200:
        print(f"  ❌ [{fund_code}] 投資組合頁面回傳錯誤：{resp2.status_code}")
        return None

    # Step 3：下載檔案
    download_headers = {
        "Referer": page_url,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
    }
    resp3 = session.get(
        download_url,
        headers=download_headers,
        verify=False,
        allow_redirects=True,
        timeout=60,
    )

    # 驗證回應是檔案
    content_type = resp3.headers.get("Content-Type", "")
    content_disp = resp3.headers.get("Content-Disposition", "")
    is_file = (
        "application" in content_type
        or "octet-stream" in content_type
        or "excel" in content_type
        or "spreadsheet" in content_type
        or "attachment" in content_disp
        or "filename" in content_disp
    )
    if not is_file and resp3.status_code == 200:
        preview = resp3.text[:200] if len(resp3.text) > 200 else resp3.text
        print(f"  ⚠️ [{fund_code}] 回傳內容不像檔案：{preview}")
        return None

    # 存檔：用 fundCode + 日期命名，避免衝突
    today = datetime.now().strftime("%Y%m%d")
    filename = f"{fund_code}_ETF_Investment_Portfolio_{today}.xlsx"
    filepath = SAVE_DIR / filename

    with open(filepath, "wb") as f:
        f.write(resp3.content)

    file_size = filepath.stat().st_size
    if file_size < 500:
        print(f"  ⚠️ [{fund_code}] 檔案異常地小 ({file_size} bytes)，可能下載失敗")
        return None

    print(f"  ✅ [{fund_code}] 已儲存：{filepath.name} ({file_size:,} bytes)")
    return filepath


def main(fund_codes: list[str] | None = None) -> list[Path]:
    codes = fund_codes or FUND_CODES
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 開始下載 {len(codes)} 支 ETF...")

    results = []
    for code in codes:
        print(f"\n--- 下載 {code} ---")
        path = download_one(code)
        if path:
            results.append(path)
        else:
            print(f"  ❌ {code} 下載失敗")

    print(f"\n完成：{len(results)}/{len(codes)} 支成功")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--fund-codes", nargs="+", default=None)
    args = parser.parse_args()

    paths = main(args.fund_codes)
    if not paths:
        print("\n❌ 全部下載失敗")
        exit(1)
