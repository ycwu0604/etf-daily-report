#!/usr/bin/env python3
"""
ETF 報表系統 — 一鍵部署腳本
=============================

把 3 個手動步驟整合成 1 個腳本：
  Step 1: 推程式碼到 GitHub (gh_push.py)
  Step 2: 設定 6 個 GitHub Secrets (GitHub API)
  Step 3: 上傳 DB 到 R2 + 手動觸發 Actions (r2_sync.py + GitHub API)

使用方式：
  1. 填好下方所有環境變數（或用命令列參數）
  2. python scripts/deploy_all.py

環境變數：
  # GitHub
  GITHUB_TOKEN      — Personal Access Token (需要 repo + workflow 權限)
  GITHUB_REPO       — 格式: 帳號/repo名 (例如: yc-wu/etf-report)

  # Cloudflare R2
  R2_ENDPOINT       — 例如 https://xxxx.r2.cloudflarestorage.com
  R2_ACCESS_KEY_ID  — R2 API Token 的 Access Key ID
  R2_SECRET_ACCESS_KEY — R2 API Token 的 Secret Access Key
  R2_BUCKET         — bucket 名稱 (預設 etf-report)

  # Cloudflare Pages
  CF_ACCOUNT_ID     — Cloudflare 帳號 ID
  CF_API_TOKEN      — Cloudflare API Token (用於 wrangler pages deploy)
"""

import os
import sys
import json
import time
import argparse

import requests
import boto3
from botocore.config import Config

# ── OA proxy 設定 ──────────────────────────────────────────
PROXIES = {
    "http": os.environ.get("HTTP_PROXY", "http://localhost:3128"),
    "https": os.environ.get("HTTPS_PROXY", "http://localhost:3128"),
}

BASE_DIR = r"C:\Users\YC_WU\Desktop\Side_Project\ETF"
DB_PATH = os.path.join(BASE_DIR, "Ezmoney", "etf_data.db")


# ═══════════════════════════════════════════════════════════
#  GitHub API helpers
# ═══════════════════════════════════════════════════════════

def _gh_api(method, url, token, json_data=None):
    """Call GitHub API with proxy."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    resp = requests.request(
        method, url, headers=headers, json=json_data,
        proxies=PROXIES, timeout=30, verify=False,
    )
    if resp.status_code in (200, 201, 204):
        return resp.json() if resp.text.strip() else {}
    print(f"  [ERROR] API {method} {url} → {resp.status_code}: {resp.text[:200]}")
    return None


def ensure_repo(token, repo):
    """確保 GitHub repo 存在，不存在就建。"""
    result = _gh_api("GET", f"https://api.github.com/repos/{repo}", token)
    if result and "id" in result:
        print(f"  [OK] Repo {repo} 已存在")
        return True

    print(f"  [INFO] Repo {repo} 不存在，建立中...")
    name = repo.split("/")[1]
    created = _gh_api("POST", "https://api.github.com/user/repos", token,
                      json_data={"name": name, "private": True})
    if created:
        print(f"  [OK] 已建立 private repo: {repo}")
        time.sleep(3)  # 等 GitHub 建立
        return True
    print(f"  [ERROR] 建立 repo 失敗。請手動建立: https://github.com/new")
    return False


def push_files(token, repo):
    """推所有檔案到 GitHub (同 gh_push.py 邏輯)。"""
    from scripts.gh_push import collect_files, _push_file as _push_one

    gitignore = os.path.join(BASE_DIR, ".gitignore")
    files = collect_files(BASE_DIR, gitignore)
    print(f"  找到 {len(files)} 個檔案")

    success, failed = 0, 0
    for local, remote in sorted(files, key=lambda x: x[1]):
        try:
            if _push_one(repo, token, local, remote):
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"    [ERROR] {remote}: {e}")
            failed += 1

    print(f"  推送完成: {success} 成功, {failed} 失敗")
    return failed == 0


def set_secrets(token, repo, secrets_dict):
    """用 GitHub API 設定 repo secrets。"""
    # 先取 repo public key (加密 secrets 用)
    key_data = _gh_api("GET",
                       f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
                       token)
    if not key_data or "key" not in key_data:
        print("  [ERROR] 取得 repo public key 失敗")
        return False

    from base64 import b64decode
    from nacl import public

    pub_key = public.PublicKey(b64decode(key_data["key"]))
    key_id = key_data["key_id"]

    ok, fail = 0, 0
    for name, value in secrets_dict.items():
        try:
            # 加密
            sealed = public.SealedBox(pub_key).encrypt(value.encode("utf-8"))
            from base64 import b64encode
            encrypted = b64encode(sealed).decode("utf-8")

            result = _gh_api("PUT",
                             f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
                             token,
                             json_data={
                                 "encrypted_value": encrypted,
                                 "key_id": key_id,
                             })
            if result is not None:
                print(f"    [OK] {name}")
                ok += 1
            else:
                print(f"    [FAIL] {name}")
                fail += 1
        except ImportError:
            # If PyNaCl not available, try plaintext approach (won't work, but give clear error)
            print(f"    [ERROR] PyNaCl 未安裝，無法加密 secret。")
            print(f"    請執行: pip install pynacl")
            return False
        except Exception as e:
            print(f"    [ERROR] {name}: {e}")
            fail += 1

    print(f"  Secrets 設定完成: {ok} 成功, {fail} 失敗")
    return fail == 0


def trigger_workflow(token, repo):
    """手動觸發 GitHub Actions workflow。"""
    result = _gh_api("POST",
                     f"https://api.github.com/repos/{repo}/actions/workflows/etf-daily.yml/dispatches",
                     token,
                     json_data={"ref": "main"})
    if result is not None:
        print("  [OK] 已觸發 ETF Daily Report workflow")
        print(f"  查看進度: https://github.com/{repo}/actions")
        return True
    print("  [ERROR] 觸發 workflow 失敗")
    return False


# ═══════════════════════════════════════════════════════════
#  R2 helpers
# ═══════════════════════════════════════════════════════════

def upload_db_to_r2():
    """上傳 etf_data.db 到 R2。"""
    endpoint = os.environ.get("R2_ENDPOINT", "")
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    bucket = os.environ.get("R2_BUCKET", "etf-report")

    if not all([endpoint, access_key, secret_key]):
        print("  [ERROR] R2 環境變數缺少")
        return False

    if not os.path.isfile(DB_PATH):
        print(f"  [ERROR] DB 不存在: {DB_PATH}")
        return False

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(region_name="auto", s3={"addressing_style": "path"}),
    )

    size = os.path.getsize(DB_PATH)
    print(f"  上傳 {DB_PATH} ({size:,} bytes) → r2://{bucket}/etf_data.db")
    client.upload_file(Bucket=bucket, Key="etf_data.db", Filename=DB_PATH)
    print("  [OK] DB 上傳完成")
    return True


# ═══════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="ETF 報表系統一鍵部署")
    parser.add_argument("--skip-push", action="store_true", help="跳過推 GitHub")
    parser.add_argument("--skip-secrets", action="store_true", help="跳過設 Secrets")
    parser.add_argument("--skip-r2", action="store_true", help="跳過上傳 R2")
    parser.add_argument("--skip-trigger", action="store_true", help="跳過觸發 Actions")
    args = parser.parse_args()

    # ── 檢查環境變數 ──────────────────────────────────────
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPO", "")

    if not token:
        print("[ERROR] 缺少 GITHUB_TOKEN")
        print('  PowerShell: $env:GITHUB_TOKEN = "ghp_你的token"')
        print("  建 Token:   https://github.com/settings/tokens/new (勾 repo + workflow)")
        sys.exit(1)
    if not repo:
        print("[ERROR] 缺少 GITHUB_REPO (格式: 帳號/repo名)")
        print('  PowerShell: $env:GITHUB_REPO = "你的帳號/etf-report"')
        sys.exit(1)

    r2_ok = all([
        os.environ.get("R2_ENDPOINT"),
        os.environ.get("R2_ACCESS_KEY_ID"),
        os.environ.get("R2_SECRET_ACCESS_KEY"),
    ])

    print("=" * 60)
    print("  ETF 報表系統 — 一鍵部署")
    print("=" * 60)
    print(f"  GitHub:  {repo}")
    print(f"  R2:      {'已設定' if r2_ok else '未設定 (跳過 R2 步驟)'}")
    print(f"  DB:      {DB_PATH} ({os.path.getsize(DB_PATH):,} bytes)" if os.path.isfile(DB_PATH) else "  DB: 不存在!")
    print()

    # ── Step 1: 推 GitHub ─────────────────────────────────
    if not args.skip_push:
        print("── Step 1/4: 推程式碼到 GitHub ──")
        if not ensure_repo(token, repo):
            sys.exit(1)
        if not push_files(token, repo):
            print("[WARN] 推送有失敗，但繼續...")
        print()
    else:
        print("── Step 1/4: 推 GitHub (SKIP) ──")
        print()

    # ── Step 2: 設 Secrets ─────────────────────────────────
    if not args.skip_secrets and r2_ok:
        print("── Step 2/4: 設定 GitHub Secrets ──")
        secrets = {
            "R2_ENDPOINT": os.environ["R2_ENDPOINT"],
            "R2_ACCESS_KEY_ID": os.environ["R2_ACCESS_KEY_ID"],
            "R2_SECRET_ACCESS_KEY": os.environ["R2_SECRET_ACCESS_KEY"],
            "R2_BUCKET": os.environ.get("R2_BUCKET", "etf-report"),
            "CF_ACCOUNT_ID": os.environ.get("CF_ACCOUNT_ID", ""),
            "CF_API_TOKEN": os.environ.get("CF_API_TOKEN", ""),
        }
        # 只設定有值的 secrets
        secrets = {k: v for k, v in secrets.items() if v}
        if not set_secrets(token, repo, secrets):
            print("[WARN] Secrets 設定有失敗，但繼續...")
        print()
    else:
        reason = "SKIP" if args.skip_secrets else "R2 未設定"
        print(f"── Step 2/4: 設 Secrets ({reason}) ──")
        print()

    # ── Step 3: 上傳 DB 到 R2 ─────────────────────────────
    if not args.skip_r2 and r2_ok:
        print("── Step 3/4: 上傳 DB 到 R2 ──")
        if not upload_db_to_r2():
            print("[WARN] R2 上傳失敗，但繼續...")
        print()
    else:
        reason = "SKIP" if args.skip_r2 else "R2 未設定"
        print(f"── Step 3/4: 上傳 R2 ({reason}) ──")
        print()

    # ── Step 4: 觸發 Actions ─────────────────────────────
    if not args.skip_trigger:
        print("── Step 4/4: 觸發 GitHub Actions ──")
        if not trigger_workflow(token, repo):
            print("[WARN] 觸發失敗。可手動觸發: repo → Actions → ETF Daily Report → Run workflow")
        print()
    else:
        print("── Step 4/4: 觸發 Actions (SKIP) ──")
        print()

    # ── 完成 ──────────────────────────────────────────────
    print("=" * 60)
    print("  部署完成!")
    print(f"  Actions 進度: https://github.com/{repo}/actions")
    print(f"  Pages 網址:   https://{repo.split('/')[1]}.pages.dev")
    print("                (第一次部署後需幾分鐘才會上線)")
    print("=" * 60)


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    main()
