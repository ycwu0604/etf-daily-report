<#
.SYNOPSIS
    ETF 報告上雲 — 一次性設定腳本
.DESCRIPTION
    執行以下步驟：
      1. git init + commit
      2. 推到 GitHub（需先在 github.com/new 建 private repo）
      3. 上傳 etf_data.db 到 R2（需先設定環境變數）
    
    使用方式：
      1. 先在 GitHub 建 private repo（名稱 etf-report）
      2. 填入下方 YOUR_ 開頭的變數
      3. 右鍵 → PowerShell 執行：.\setup_cloud.ps1
#>

param(
    [switch]$GitOnly,     # 只做 git init + push，不做 R2
    [switch]$R2Only        # 只做 R2 上傳，不做 git
)

# ══════════════════════════════════════════════════════════
# ⚠️ 填入你的實際值 ⚠️
# ══════════════════════════════════════════════════════════
$GITHUB_USERNAME = "YOUR_GITHUB_USERNAME"   # 你的 GitHub 帳號
$REPO_NAME       = "etf-report"              # repo 名稱

$R2_ENDPOINT         = "YOUR_R2_ENDPOINT"          # 例如 https://xxxx.r2.cloudflarestorage.com
$R2_ACCESS_KEY_ID     = "YOUR_R2_ACCESS_KEY_ID"     # R2 API Token Access Key
$R2_SECRET_ACCESS_KEY = "YOUR_R2_SECRET_ACCESS_KEY"  # R2 API Token Secret Key
$R2_BUCKET            = "etf-report"                  # R2 bucket 名稱
# ══════════════════════════════════════════════════════════

$ETF_DIR = "C:\Users\YC_WU\Desktop\Side_Project\ETF"
$PYTHON  = "C:\Users\YC_WU\AppData\Local\Programs\Python\Python313\python.exe"

$ErrorActionPreference = "Stop"

# ── Step A: Git init + push ──────────────────────────────
if (-not $R2Only) {
    Write-Host ""
    Write-Host "=== Step A: Git init + push ===" -ForegroundColor Cyan

    # 檢查是否已經是 git repo
    if (Test-Path (Join-Path $ETF_DIR ".git")) {
        Write-Host "[SKIP] Git repo already initialized" -ForegroundColor Yellow
    } else {
        Write-Host "[1/3] git init + add + commit..."
        git init $ETF_DIR
        git -C $ETF_DIR add .
        git -C $ETF_DIR commit -m "ETF daily report: xlsx + HTML + R2 + GitHub Actions"
        Write-Host "[1/3] Done" -ForegroundColor Green
    }

    # 檢查 remote 是否已設定
    $remote = git -C $ETF_DIR remote get-url origin 2>$null
    if ($LASTEXITCODE -eq 0 -and $remote) {
        Write-Host "[SKIP] Remote origin already set: $remote" -ForegroundColor Yellow
    } else {
        if ($GITHUB_USERNAME -eq "YOUR_GITHUB_USERNAME") {
            Write-Host ""
            Write-Host "[ERROR] 請先編輯此腳本，填入 GITHUB_USERNAME" -ForegroundColor Red
            Write-Host "  或者手動執行：" -ForegroundColor Yellow
            Write-Host "  git -C `"$ETF_DIR`" remote add origin https://github.com/你的帳號/$REPO_NAME.git"
            Write-Host "  git -C `"$ETF_DIR`" branch -M main"
            Write-Host "  git -C `"$ETF_DIR`" push -u origin main"
            if (-not $GitOnly) { exit 1 }
        } else {
            Write-Host "[2/3] Setting remote + pushing..."
            git -C $ETF_DIR remote add origin "https://github.com/$GITHUB_USERNAME/$REPO_NAME.git"
            git -C $ETF_DIR branch -M main
            git -C $ETF_DIR push -u origin main
            Write-Host "[2/3] Done" -ForegroundColor Green
        }
    }

    Write-Host ""
    Write-Host "[3/3] 請到 GitHub repo 設定 Secrets：" -ForegroundColor Yellow
    Write-Host "  repo → Settings → Secrets → Actions → New repository secret"
    Write-Host ""
    Write-Host "  需要加入的 Secrets："
    Write-Host "  ┌─────────────────────┬──────────────────────────────────────────────┐"
    Write-Host "  │ R2_ENDPOINT         │ $R2_ENDPOINT"
    Write-Host "  │ R2_ACCESS_KEY_ID    │ $R2_ACCESS_KEY_ID"
    Write-Host "  │ R2_SECRET_ACCESS_KEY│ $R2_SECRET_ACCESS_KEY"
    Write-Host "  │ R2_BUCKET           │ $R2_BUCKET"
    Write-Host "  │ CF_ACCOUNT_ID       │ (你的 Cloudflare Account ID)"
    Write-Host "  │ CF_API_TOKEN        │ (Cloudflare Pages: Edit 權限的 Token)"
    Write-Host "  └─────────────────────┴──────────────────────────────────────────────┘"
    Write-Host ""
}

# ── Step B: 上傳 DB 到 R2 ────────────────────────────────
if (-not $GitOnly) {
    Write-Host ""
    Write-Host "=== Step B: Upload DB to R2 ===" -ForegroundColor Cyan

    if ($R2_ENDPOINT -eq "YOUR_R2_ENDPOINT") {
        Write-Host "[ERROR] 請先編輯此腳本，填入 R2 相關值" -ForegroundColor Red
        Write-Host "  或者手動執行：" -ForegroundColor Yellow
        Write-Host '  $env:R2_ENDPOINT = "https://xxxx.r2.cloudflarestorage.com"'
        Write-Host '  $env:R2_ACCESS_KEY_ID = "你的Key"'
        Write-Host '  $env:R2_SECRET_ACCESS_KEY = "你的Secret"'
        Write-Host '  $env:R2_BUCKET = "etf-report"'
        Write-Host "  pip install boto3"
        Write-Host "  python scripts\r2_sync.py upload Ezmoney\etf_data.db etf_data.db"
        exit 1
    }

    # 檢查 boto3
    $boto3Check = & $PYTHON -c "import boto3; print('ok')" 2>&1
    if ($boto3Check -ne "ok") {
        Write-Host "[1/2] Installing boto3..."
        & $PYTHON -m pip install boto3 -q
    }

    # 設定環境變數
    $env:R2_ENDPOINT = $R2_ENDPOINT
    $env:R2_ACCESS_KEY_ID = $R2_ACCESS_KEY_ID
    $env:R2_SECRET_ACCESS_KEY = $R2_SECRET_ACCESS_KEY
    $env:R2_BUCKET = $R2_BUCKET

    Write-Host "[1/1] Uploading etf_data.db to R2..."
    & $PYTHON (Join-Path $ETF_DIR "scripts\r2_sync.py") upload (Join-Path $ETF_DIR "Ezmoney\etf_data.db") "etf_data.db"

    if ($LASTEXITCODE -eq 0) {
        Write-Host "[1/1] Done" -ForegroundColor Green
    } else {
        Write-Host "[1/1] Upload FAILED — check R2 credentials" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "=== 設定完成 ===" -ForegroundColor Green
Write-Host ""
Write-Host "下一步："
Write-Host "  1. 到 GitHub repo → Settings → Secrets → Actions 加入 6 個 Secrets"
Write-Host "  2. 到 GitHub repo → Actions → ETF Daily Report → Run workflow"
Write-Host "  3. 等幾分鐘，打開 https://etf-report.你的帳號.pages.dev"
