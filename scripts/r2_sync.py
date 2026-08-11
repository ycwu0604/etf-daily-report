#!/usr/bin/env python3
"""
Cloudflare R2 同步工具

用 S3 相容 API 從 R2 下載/上傳檔案。
ETF pipeline 用它來：
  - 開始前：下載 etf_data.db
  - 結束後：上傳更新後的 etf_data.db + 報表 xlsx

環境變數（GitHub Actions 自動注入，本地測試需手動設）：
  R2_ENDPOINT         — 例如 https://xxxx.r2.cloudflarestorage.com
  R2_ACCESS_KEY_ID    — R2 API Token 的 Access Key ID
  R2_SECRET_ACCESS_KEY— R2 API Token 的 Secret Access Key
  R2_BUCKET           — bucket 名稱（預設 etf-report）

使用方式：
  python scripts/r2_sync.py download etf_data.db Ezmoney/etf_data.db
  python scripts/r2_sync.py upload Ezmoney/etf_data.db etf_data.db
  python scripts/r2_sync.py upload reports/ETF_Combined_Daily_Report_20260810.xlsx reports/2026-08-10.xlsx
"""

import os
import sys
import argparse

import boto3
from botocore.config import Config


def _get_client():
    """建立 S3 相容的 R2 client。"""
    endpoint = os.environ.get("R2_ENDPOINT", "")
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    bucket = os.environ.get("R2_BUCKET", "etf-report")

    if not endpoint or not access_key or not secret_key:
        print("[ERROR] Missing env vars: R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY")
        sys.exit(1)

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            region_name="auto",
            s3={"addressing_style": "path"},  # R2 uses path-style
        ),
    )
    return client, bucket


def download(r2_key: str, local_path: str):
    """從 R2 下載檔案。如果 R2 上不存在，靜默跳過（第一次跑時 DB 還沒上傳）。"""
    client, bucket = _get_client()
    try:
        print(f"[R2] Downloading r2://{bucket}/{r2_key} → {local_path}")
        client.head_object(Bucket=bucket, Key=r2_key)
    except client.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("404", "NoSuchKey"):
            print(f"[R2] Key '{r2_key}' not found in bucket — skipping (first run?)")
            return False
        raise

    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    client.download_file(Bucket=bucket, Key=r2_key, Filename=local_path)
    size = os.path.getsize(local_path)
    print(f"[R2] Downloaded: {local_path} ({size:,} bytes)")
    return True


def upload(local_path: str, r2_key: str):
    """上傳本地檔案到 R2。"""
    client, bucket = _get_client()
    if not os.path.isfile(local_path):
        print(f"[R2] File not found: {local_path} — skipping upload")
        return False

    size = os.path.getsize(local_path)
    print(f"[R2] Uploading {local_path} ({size:,} bytes) → r2://{bucket}/{r2_key}")
    client.upload_file(Bucket=bucket, Key=r2_key, Filename=local_path)
    print(f"[R2] Upload done: {r2_key}")
    return True


def list_keys(prefix: str = "") -> list[str]:
    """列出 R2 bucket 裡指定前綴的所有 key。"""
    client, bucket = _get_client()
    paginator = client.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def main():
    parser = argparse.ArgumentParser(description="Cloudflare R2 sync tool")
    sub = parser.add_subparsers(dest="command", required=True)

    # download
    dl = sub.add_parser("download", help="Download file from R2")
    dl.add_argument("r2_key", help="Key in R2 (e.g. etf_data.db)")
    dl.add_argument("local_path", help="Local file path to save")

    # upload
    ul = sub.add_parser("upload", help="Upload file to R2")
    ul.add_argument("local_path", help="Local file path to upload")
    ul.add_argument("r2_key", help="Key in R2 (e.g. etf_data.db)")

    # list
    ls = sub.add_parser("list", help="List keys in R2 bucket")
    ls.add_argument("--prefix", default="", help="Key prefix filter")

    args = parser.parse_args()

    if args.command == "download":
        ok = download(args.r2_key, args.local_path)
        if not ok:
            sys.exit(0)  # Not an error — key just doesn't exist yet
    elif args.command == "upload":
        ok = upload(args.local_path, args.r2_key)
        if not ok:
            sys.exit(1)
    elif args.command == "list":
        keys = list_keys(args.prefix)
        if keys:
            for k in keys:
                print(f"  {k}")
        else:
            print("(bucket is empty or no matching keys)")


if __name__ == "__main__":
    main()
