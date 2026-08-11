#!/usr/bin/env python3
"""
GitHub API 推送工具 — 不需要 git CLI

用 GitHub REST API + requests 直接推送檔案到 repo。
因為本機沒有 git 且 OA proxy 擋住 git 下載，所以用 API 替代。

使用方式：
  1. 設定環境變數：
     $env:GITHUB_TOKEN = "ghp_你的PersonalAccessToken"
     $env:GITHUB_REPO  = "你的帳號/etf-report"
  2. 執行：
     python scripts/gh_push.py

  或者直接帶參數：
     python scripts/gh_push.py --token ghp_xxx --repo user/etf-report

需要的 Token 權限：repo (Full control of private repositories)
建 Token位置：https://github.com/settings/tokens/new
"""

import base64
import json
import os
import sys
import argparse

import requests

# ── OA proxy 設定 ──────────────────────────────────────────
PROXIES = {
    "http": os.environ.get("HTTP_PROXY", "http://localhost:3128"),
    "https": os.environ.get("HTTPS_PROXY", "http://localhost:3128"),
}


def _api(method, url, token, json_data=None, raw_data=None):
    """Call GitHub API with proxy."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    resp = requests.request(
        method, url, headers=headers, json=json_data, data=raw_data,
        proxies=PROXIES, timeout=30, verify=False,
    )
    if resp.status_code in (200, 201, 204):
        return resp.json() if resp.text.strip() else {}
    print(f"[ERROR] API {method} {url} → {resp.status_code}: {resp.text[:300]}")
    return None


def _get_file_sha(repo, token, path, branch="main"):
    """Get current SHA of a file in the repo (returns None if not exists)."""
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    url += f"?ref={branch}"
    result = _api("GET", url, token)
    if result and "sha" in result:
        return result["sha"]
    return None


def _push_file(repo, token, local_path, remote_path, branch="main"):
    """Push a single file to GitHub via Contents API."""
    with open(local_path, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    sha = _get_file_sha(repo, token, remote_path, branch)

    payload = {
        "message": f"Add {remote_path}",
        "content": content,
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    url = f"https://api.github.com/repos/{repo}/contents/{remote_path}"
    result = _api("PUT", url, token, json_data=payload)
    if result:
        commit = result.get("commit", {})
        sha_short = commit.get("sha", "")[:7]
        print(f"  [OK] {remote_path} (commit {sha_short})")
        return True
    return False


def _parse_gitignore(gitignore_path):
    """Parse .gitignore into ignored dirs and file patterns."""
    ignore_dirs = set()   # Directory paths to skip (e.g. "Ezmoney/downloads")
    ignore_files = set()   # Exact file paths to skip (e.g. "Ezmoney/etf_data.db")
    ignore_exts = set()    # Extensions to skip (e.g. "*.pyc")

    if not os.path.isfile(gitignore_path):
        return ignore_dirs, ignore_files, ignore_exts

    with open(gitignore_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Normalize: strip trailing /
            clean = line.rstrip("/")
            # Convert to OS path separators
            native = clean.replace("/", os.sep)
            if native.startswith("*."):
                ignore_exts.add(native)  # e.g. "*.pyc"
            elif "." in os.path.basename(native):
                # Specific file like "Ezmoney/etf_data.db"
                ignore_files.add(native)
            else:
                # Directory like "Ezmoney/downloads"
                ignore_dirs.add(native)

    return ignore_dirs, ignore_files, ignore_exts


def collect_files(base_dir, gitignore_path):
    """Collect all files to push, respecting .gitignore."""
    ignore_dirs, ignore_files, ignore_exts = _parse_gitignore(gitignore_path)

    files = []
    for root, dirs, filenames in os.walk(base_dir):
        # Always skip these dirs
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".omo",
                                                   "node_modules", "dist")]

        # Check if this dir is inside an ignored directory
        rel_root = os.path.relpath(root, base_dir)
        skip = False
        for ign_dir in ignore_dirs:
            # Match if rel_root starts with ignored dir or IS the ignored dir
            if rel_root == ign_dir or rel_root.startswith(ign_dir + os.sep):
                skip = True
                break
        if skip:
            dirs.clear()
            continue

        for fn in filenames:
            if fn.startswith("~$"):
                continue
            local = os.path.join(root, fn)
            rel = os.path.relpath(local, base_dir)

            # Check against ignored files
            if rel in ignore_files:
                continue
            # Check against ignored extensions
            _, ext = os.path.splitext(fn)
            if f"*{ext}" in ignore_exts:
                continue

            # Use forward slashes for GitHub
            remote = rel.replace(os.sep, "/")
            files.append((local, remote))

    return files


def main():
    parser = argparse.ArgumentParser(description="Push files to GitHub via API (no git needed)")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""),
                        help="GitHub Personal Access Token")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPO", ""),
                        help="GitHub repo (user/repo-name)")
    parser.add_argument("--base-dir", default=r"C:\Users\YC_WU\Desktop\Side_Project\ETF",
                        help="Local directory to push")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only list files, don't push")
    args = parser.parse_args()

    if not args.token:
        print("[ERROR] Set GITHUB_TOKEN env var or use --token")
        print("  Create token: https://github.com/settings/tokens/new")
        print("  Required scopes: repo")
        print()
        print("  PowerShell:")
        print('  $env:GITHUB_TOKEN = "ghp_你的token"')
        print('  $env:GITHUB_REPO = "你的帳號/etf-report"')
        sys.exit(1)

    if not args.repo:
        print("[ERROR] Set GITHUB_REPO env var or use --repo (format: user/repo)")
        sys.exit(1)

    base_dir = args.base_dir
    gitignore = os.path.join(base_dir, ".gitignore")

    print(f"Base dir: {base_dir}")
    print(f"Repo:     {args.repo}")
    print()

    # Collect files
    files = collect_files(base_dir, gitignore)
    print(f"Found {len(files)} files to push")
    print()

    if args.dry_run:
        for local, remote in sorted(files, key=lambda x: x[1]):
            size = os.path.getsize(local)
            print(f"  {remote} ({size:,} bytes)")
        print()
        print("[DRY RUN] No files pushed. Use without --dry-run to push.")
        return

    # Check if repo exists, create if not
    result = _api("GET", f"https://api.github.com/repos/{args.repo}", args.token)
    if not result:
        print(f"[INFO] Repo {args.repo} not found — creating...")
        user = args.repo.split("/")[0]
        name = args.repo.split("/")[1]
        create_result = _api("POST", "https://api.github.com/user/repos", args.token,
                             json_data={"name": name, "private": True})
        if create_result:
            print(f"  [OK] Created private repo: {args.repo}")
        else:
            print(f"  [FAIL] Failed to create repo. Create manually at https://github.com/new")
            sys.exit(1)
    else:
        print(f"[INFO] Repo {args.repo} exists")

    print()
    print("Pushing files...")

    # Push each file
    success = 0
    failed = 0
    for local, remote in sorted(files, key=lambda x: x[1]):
        try:
            if _push_file(args.repo, args.token, local, remote):
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  [FAIL] {remote}: {e}")
            failed += 1

    print()
    print(f"Done: {success} succeeded, {failed} failed out of {len(files)} files")

    if failed == 0:
        print()
        print("=== Next steps ===")
        print(f"1. Set 6 Secrets in {args.repo} → Settings → Secrets → Actions")
        print("2. Upload DB to R2:")
        print('   $env:R2_ENDPOINT = "https://你的.r2.cloudflarestorage.com"')
        print('   $env:R2_ACCESS_KEY_ID = "你的Key"')
        print('   $env:R2_SECRET_ACCESS_KEY = "你的Secret"')
        print('   $env:R2_BUCKET = "etf-report"')
        print("   python scripts/r2_sync.py upload Ezmoney/etf_data.db etf_data.db")
        print("3. Trigger Actions: repo → Actions → ETF Daily Report → Run workflow")


if __name__ == "__main__":
    # Suppress SSL warnings for proxy
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    main()
